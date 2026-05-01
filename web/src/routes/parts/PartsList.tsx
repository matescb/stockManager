import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Boxes, ImageOff, Trash2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { PartsListSchema } from "@/lib/schemas";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import type { Part } from "@/types";

export default function PartsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState<{ ids: string[]; clear: () => void } | null>(null);
  const [busy, setBusy] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["parts", { archived }],
    queryFn: () =>
      api.parsed.get(`/parts${archived ? "?archived=true" : ""}`, PartsListSchema),
  });

  const partsById = new Map((data ?? []).map(p => [p.id, p]));

  async function doDelete(ids: string[], clear: () => void) {
    setBusy(true);
    try {
      const res = await api.post<{ archived_ids: string[]; skipped: number }>(
        "/parts/bulk-delete",
        { part_ids: ids },
      );
      qc.invalidateQueries({ queryKey: ["parts"] });
      clear();
      toast.success(
        res.archived_ids.length === ids.length
          ? `Archived ${res.archived_ids.length} part${res.archived_ids.length === 1 ? "" : "s"}.`
          : `Archived ${res.archived_ids.length} of ${ids.length}; ${res.skipped} skipped.`
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Bulk delete failed");
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  return (
    <div>
      <PartsTopNav
        rightAccessory={
          <>
            <Link to="/parts/scan-import" className="btn">Scan</Link>
            <Link to="/parts/create" className="btn-primary">+ Part</Link>
          </>
        }
      />
      {isLoading ? (
        <div className="text-muted">Loading…</div>
      ) : (
        <DataTable
          rows={data ?? []}
          rowKey={(r) => r.id}
          tableId="parts"
          searchPlaceholder="Search parts…"
          selectable
          selectionAccessory={(ids, clear) => (
            <button
              type="button"
              className="btn-danger inline-flex items-center gap-1.5"
              disabled={busy}
              onClick={() => setConfirming({ ids, clear })}
            >
              <Trash2 size={14} />
              Delete ({ids.length})
            </button>
          )}
          empty={
            archived ? (
              <EmptyState
                icon={Boxes}
                title="No archived parts"
                description="Archived parts will appear here."
              />
            ) : (
              <EmptyState
                icon={Boxes}
                title="No parts yet"
                description="Create your first part to start tracking stock."
                action={{ label: "+ Part", to: "/parts/create" }}
              />
            )
          }
          exportFilename="parts"
          onRowClick={(r) => nav(`/parts/${r.id}/info`)}
          columns={[
            {
              key: "image",
              header: "",
              width: "44px",
              render: r =>
                r.image_url ? (
                  <img
                    src={r.image_url}
                    alt=""
                    loading="lazy"
                    className="h-8 w-8 object-contain rounded bg-panel"
                  />
                ) : (
                  <div className="h-8 w-8 rounded bg-panel2/40 flex items-center justify-center text-muted">
                    <ImageOff size={14} />
                  </div>
                ),
            },
            { key: "part_type", header: "Type", accessor: r => r.part_type, width: "100px" },
            { key: "name", header: "Part", accessor: r => r.name, render: r => <span className="font-medium">{r.name}</span> },
            { key: "mpn", header: "MPN", accessor: r => r.mpn ?? "" },
            { key: "manufacturer", header: "Manufacturer", accessor: r => r.manufacturer ?? "" },
            { key: "footprint", header: "Footprint", accessor: r => r.footprint ?? "" },
            { key: "on_hand", header: "Stock", accessor: r => r.on_hand ?? 0, width: "80px" },
            { key: "reserved", header: "Reserved", accessor: r => r.reserved ?? 0, width: "100px", hidden: true },
          ]}
        />
      )}

      {confirming && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/40"
          onClick={() => !busy && setConfirming(null)}
        >
          <div
            className="card p-4 max-w-md w-full mx-4 space-y-3"
            onClick={e => e.stopPropagation()}
          >
            <h3 className="text-md font-semibold">
              Archive {confirming.ids.length} part{confirming.ids.length === 1 ? "" : "s"}?
            </h3>
            <p className="text-sm text-muted">
              Stock history is preserved. Archived parts can be restored from
              the Archived view.
            </p>
            <ul className="text-xs text-muted max-h-48 overflow-auto space-y-0.5">
              {confirming.ids.slice(0, 12).map(id => {
                const p = partsById.get(id);
                return (
                  <li key={id} className="font-mono">
                    {p ? p.name : id}
                  </li>
                );
              })}
              {confirming.ids.length > 12 && (
                <li className="italic">…and {confirming.ids.length - 12} more</li>
              )}
            </ul>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirming(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-danger inline-flex items-center gap-1.5"
                disabled={busy}
                onClick={() => doDelete(confirming.ids, confirming.clear)}
              >
                <Trash2 size={14} />
                {busy ? "Archiving…" : "Archive"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
