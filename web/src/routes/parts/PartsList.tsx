import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Boxes, ImageOff, Trash2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { PartsListSchema } from "@/lib/schemas";
import { wsKey } from "@/lib/queryKeys";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import { useConfirm } from "@/components/ConfirmDialog";
import QueryStateBoundary from "@/components/QueryStateBoundary";

export default function PartsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const partsKey = wsKey("parts", { archived });
  const [busy, setBusy] = useState(false);
  const query = useQuery({
    queryKey: partsKey,
    queryFn: () =>
      api.parsed.get(`/parts${archived ? "?archived=true" : ""}`, PartsListSchema),
  });
  const { data, isLoading } = query;

  async function doDelete(ids: string[], clear: () => void) {
    // Build a friendly preview of the first few names for the dialog —
    // mirrors the pre-fix overlay UX without rolling our own modal
    // (FE2-005).
    const partsById = new Map((data ?? []).map(p => [p.id, p]));
    const previewLines = ids
      .slice(0, 12)
      .map(id => partsById.get(id)?.name ?? id)
      .join("\n");
    const more = ids.length > 12 ? `\n…and ${ids.length - 12} more` : "";
    const ok = await confirm({
      title: `Archive ${ids.length} part${ids.length === 1 ? "" : "s"}?`,
      message:
        "Stock history is preserved. Archived parts can be restored from the Archived view.\n\n" +
        previewLines +
        more,
      severity: "danger",
      confirmLabel: "Archive",
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await api.post<{ archived_ids: string[]; skipped: number }>(
        "/parts/bulk-delete",
        { part_ids: ids },
      );
      qc.invalidateQueries({ queryKey: wsKey("parts") });
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
      <QueryStateBoundary query={query} resourceLabel="parts">
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
              onClick={() => doDelete(ids, clear)}
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
      </QueryStateBoundary>
    </div>
  );
}
