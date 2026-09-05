/**
 * ScanImport — parent orchestrator (~150 lines). Sub-components under ./ScanImport/:
 * ScanImportSession (camera+lookup), ScanImportQueue (row list), ScanImportActions
 * (submit/storage/summary), hooks.ts (useScanImportRows), storage.ts, types.ts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { bagLotName, bagComments } from "@/lib/bagCode";
import { formatQuantity } from "@/lib/format";
import type { StorageLocation } from "@/types";
import { useScanImportRows } from "./ScanImport/hooks";
import { clearDraft, saveDraft } from "./ScanImport/storage";
import { type ImportResponse, type LookupState, type Row } from "./ScanImport/types";
import ScanImportSession from "./ScanImport/ScanImportSession";
import ScanImportQueue from "./ScanImport/ScanImportQueue";
import ScanImportActions from "./ScanImport/ScanImportActions";
import { InlineQueryError } from "@/components/QueryStateBoundary";

export default function ScanImport() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [searchParams] = useSearchParams();
  const { rows, setRows, seenSigs, seenMpns, removeRow, setQuantity } = useScanImportRows();
  // wsId captured at mount guards draft writes/clears against workspace-switch races.
  const mountWsId = useRef<string | undefined>(workspaceId);
  const [storageId, setStorageId] = useState<string>(() => searchParams.get("storage_id") ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [lastSummary, setLastSummary] = useState<ImportResponse | null>(null);
  const restoredCount = useRef<number>(rows.length);
  const restoredBannerShown = useRef(false);
  const [showRestoredBanner, setShowRestoredBanner] = useState(() => rows.length > 0);

  // Show restored-draft banner once on mount.
  useEffect(() => {
    if (!restoredBannerShown.current && restoredCount.current > 0 && showRestoredBanner) {
      restoredBannerShown.current = true;
      const n = restoredCount.current;
      toast.info(`Restored ${n} draft scan${n === 1 ? "" : "s"} from your previous session.`, {
        duration: 8000,
        action: {
          label: "Discard",
          onClick: () => {
            setRows([]);
            seenSigs.current.clear();
            seenMpns.current.clear();
            const wsId = mountWsId.current;
            if (wsId) clearDraft(wsId);
            setShowRestoredBanner(false);
          },
        },
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist rows to sessionStorage on every change.
  useEffect(() => {
    const wsId = mountWsId.current;
    if (!wsId || workspaceId !== wsId) return;
    saveDraft(wsId, rows);
  }, [rows, workspaceId]);

  // Warn on unload when there are unsaved rows.
  useEffect(() => {
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      const hasPending = rows.some(r => r.state.kind === "found" || r.state.kind === "pending");
      if (rows.length > 0 && hasPending) e.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [rows]);

  // Honour ?storage_id= changes mid-session.
  useEffect(() => {
    const fromUrl = searchParams.get("storage_id");
    if (fromUrl && fromUrl !== storageId) setStorageId(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const storagesQuery = useQuery({
    queryKey: useWsKey("storage"),
    queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }),
  });
  const { data: storages } = storagesQuery;

  const handleRow = useCallback(
    (row: Row) => setRows(prev => [...prev, row]),
    [setRows],
  );
  const handleLookupUpdate = useCallback(
    (rowId: string, next: LookupState) =>
      setRows(prev => prev.map(r => (r.rowId === rowId ? { ...r, state: next } : r))),
    [setRows],
  );

  async function quickRemoveFromBag(rowId: string, quantity: number) {
    const row = rows.find(r => r.rowId === rowId);
    if (!row || row.state.kind !== "bag_rescan") return;
    const st = row.state;
    if (quantity <= 0 || quantity > st.quantity) return;
    try {
      await api.post(`/parts/${st.part_id}/quick-remove-bag`, {
        quantity, lot_id: st.lot_id, storage_location_id: st.storage_location_id,
      });
      setRows(prev =>
        prev.map(r =>
          r.rowId === rowId ? { ...r, state: { kind: "consumed", partId: st.part_id, quantity } } : r,
        ),
      );
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", st.part_id) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", st.part_id, "stock") });
      toast.success(`Removed ${formatQuantity(quantity)} from this bag.`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Quick-remove failed");
    }
  }

  const importable = useMemo(() => rows.filter(r => r.state.kind === "found"), [rows]);

  async function submitAll() {
    if (importable.length === 0) { toast.error("Nothing to import."); return; }
    setSubmitting(true);
    try {
      const out = await api.post<ImportResponse>("/parts/bulk-import-from-scan", {
        rows: importable.map(r => ({
          mpn: r.bag.mpn,
          quantity: r.quantity > 0 ? r.quantity : undefined,
          storage_location_id: storageId || undefined,
          lot_name: bagLotName(r.bag) ?? undefined,
          lot_serial: r.bag.serial,
          comments: bagComments(r.bag) ?? undefined,
          bag_signature: r.bagSig ?? undefined,
        })),
      });
      setLastSummary(out);
      const importedMpns = new Set(out.rows.filter(r => r.status === "created").map(r => r.mpn));
      setRows(prev => {
        const remaining = prev.filter(r => !importedMpns.has(r.bag.mpn));
        const wsId = mountWsId.current;
        if (remaining.length === 0 && wsId) clearDraft(wsId);
        return remaining;
      });
      importedMpns.forEach(m => seenMpns.current.delete(m));
      toast.success(`Imported ${out.summary.created} part${out.summary.created === 1 ? "" : "s"}.`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Import failed");
    } finally {
      setSubmitting(false);
    }
  }

  function handleOpenExisting(row: Row) {
    if (row.state.kind === "duplicate") nav(`/parts/${row.state.existing.id}/info`);
    else if (row.state.kind === "bag_rescan") nav(`/parts/${row.state.part_id}/info`);
    else if (row.state.kind === "consumed") nav(`/parts/${row.state.partId}/info`);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h1 className="text-xl font-semibold">Scan to import</h1>
        <Link to="/parts" className="btn">Back to parts</Link>
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <ScanImportSession
          seenSigs={seenSigs}
          seenMpns={seenMpns}
          onRow={handleRow}
          onLookupUpdate={handleLookupUpdate}
        />
        <div className="card p-3 flex flex-col">
          <InlineQueryError query={storagesQuery} label="storage locations" className="mb-2" />
          <ScanImportActions
            rowCount={rows.length}
            importableCount={importable.length}
            submitting={submitting}
            storageId={storageId}
            storages={storages}
            lastSummary={lastSummary}
            onStorageChange={setStorageId}
            onSubmit={submitAll}
          />
          <ScanImportQueue
            rows={rows}
            onRemove={removeRow}
            onQuantity={setQuantity}
            onOpenExisting={handleOpenExisting}
            onQuickRemove={(rowId, qty) => quickRemoveFromBag(rowId, qty)}
          />
        </div>
      </div>
    </div>
  );
}
