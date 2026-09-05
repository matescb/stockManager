import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Printer, Warehouse } from "lucide-react";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import BatchPrintDialog, { type BatchPrintItem } from "@/routes/labels/BatchPrintDialog";
import type { StorageLocation } from "@/types";

export default function StorageList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const storageQuery = useQuery({
    queryKey: useWsKey("storage", { archived }),
    queryFn: ({ signal }) => api.get<StorageLocation[]>(`/storage${archived ? "?archived=true" : ""}`, { signal }),
  });
  const { data } = storageQuery;

  // Batch bin labels: the classic "re-label a whole shelf" job. The dialog
  // owns the template choice, the per-bin loop and the failure message.
  const [batchPrint, setBatchPrint] = useState<{
    items: BatchPrintItem[];
    clear: () => void;
  } | null>(null);

  function openBatchPrint(ids: string[], clear: () => void) {
    const byId = new Map((data ?? []).map(row => [row.id, row] as const));
    setBatchPrint({
      items: ids.map(id => ({ id, label: byId.get(id)?.name ?? id })),
      clear,
    });
  }

  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink to="/storage" end className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Storage</NavLink>
        <NavLink to="/storage/archived" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Archived</NavLink>
        <Link to="/storage/create" className="btn-primary ml-auto">+ Storage</Link>
      </div>
      <QueryStateBoundary query={storageQuery} resourceLabel="storage locations">
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="storage"
        selectable
        selectionAccessory={(ids, clear) => (
          <button
            type="button"
            className="btn inline-flex items-center gap-1.5"
            onClick={() => openBatchPrint(ids, clear)}
          >
            <Printer size={14} />
            Print labels ({ids.length})
          </button>
        )}
        empty={
          archived ? (
            <EmptyState
              icon={Warehouse}
              title="No archived storage"
              description="Archived storage locations will appear here."
            />
          ) : (
            <EmptyState
              icon={Warehouse}
              title="No storage locations yet"
              description="Add a shelf, bin, or reel to organise your inventory."
              action={{ label: "+ Storage", to: "/storage/create" }}
            />
          )
        }
        exportFilename="storage"
        onRowClick={r => nav(`/storage/${r.id}/info`)}
        columns={[
          { key: "name", header: "Location", accessor: r => r.name },
          { key: "description", header: "Description", accessor: r => r.description ?? "" },
          { key: "single_part_only", header: "Single-part", accessor: r => r.single_part_only ? "yes" : "" },
          { key: "is_full", header: "Full", accessor: r => r.is_full ? "yes" : "" },
        ]}
      />
      </QueryStateBoundary>

      <BatchPrintDialog
        open={batchPrint !== null}
        entityType="storage_location"
        items={batchPrint?.items ?? []}
        onClose={() => setBatchPrint(null)}
        onDone={() => {
          batchPrint?.clear();
          setBatchPrint(null);
        }}
      />
    </div>
  );
}
