import { Link, Outlet, useParams, NavLink, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ScanLine } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useWsKey, wsKeyOf, wsScope } from "@/lib/queryKeys";
import EntityHeader from "@/components/EntityHeader";
import SubNav from "@/components/SubNav";
import type { StorageLocation, Part, StockEntry } from "@/types";
import { DataTable } from "@/components/DataTable";

export function StorageDetailLayout() {
  const { storageId } = useParams<{ storageId: string }>();
  const { data } = useQuery({
    queryKey: useWsKey("storage", storageId),
    queryFn: () => api.get<StorageLocation>(`/storage/${storageId}`),
    enabled: !!storageId,
  });
  if (!data) return <div className="text-muted">Loading…</div>;
  const items = [
    { to: `/storage/${data.id}/info`, label: "Info" },
    { to: `/storage/${data.id}/history`, label: "History" },
    { to: `/storage/${data.id}/settings`, label: "Settings" },
    { to: `/storage/${data.id}/other`, label: "Other" },
  ];
  return (
    <div>
      <EntityHeader
        title={data.name}
        subtitle={
          <span>
            {data.description}
            {data.is_full && <span className="pill ml-2">full</span>}
            {data.archived_at && <span className="pill ml-2 bg-danger/20 text-danger">archived</span>}
          </span>
        }
        idCode={data.id}
        actions={
          !data.archived_at && (
            // "Scan into here": jumps to the bulk-import flow with this
            // bin pre-selected as the destination, so a fresh bag of
            // parts lands directly without picking storage in the form.
            <Link
              to={`/parts/scan-import?storage_id=${data.id}`}
              className="btn-primary inline-flex items-center gap-1.5"
            >
              <ScanLine size={14} />
              Scan into here
            </Link>
          )
        }
      />
      <SubNav items={items} />
      <Outlet context={{ storage: data }} />
    </div>
  );
}

export function StorageInfo() {
  const { storageId } = useParams();
  const { data: rows } = useQuery({
    queryKey: useWsKey("storage", storageId, "parts"),
    queryFn: () => api.get<{ part_id: string; lot_id: string | null; quantity: number }[]>(`/storage/${storageId}/parts`),
  });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts") });
  const partName = new Map(parts?.map(p => [p.id, p.name]) ?? []);
  return (
    <div className="card overflow-hidden">
      <table className="table">
        <thead>
          <tr><th>Part</th><th>Lot</th><th>Quantity</th></tr>
        </thead>
        <tbody>
          {(!rows || rows.length === 0) && (
            <tr><td colSpan={3} className="text-center py-6 text-muted">Empty.</td></tr>
          )}
          {rows?.map((r, i) => (
            <tr key={i}>
              <td>{partName.get(r.part_id) || r.part_id}</td>
              <td className="font-mono text-xs">{r.lot_id || "—"}</td>
              <td className="tabular-nums">{r.quantity}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StorageHistory() {
  const { storageId } = useParams();
  const { data } = useQuery({
    queryKey: useWsKey("storage", storageId, "history"),
    queryFn: () => api.get<StockEntry[]>(`/storage/${storageId}/history`),
  });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts") });
  const partName = new Map(parts?.map(p => [p.id, p.name]) ?? []);
  return (
    <DataTable
      rows={data ?? []}
      rowKey={r => r.id}
      tableId="storage-history"
      empty="No history."
      columns={[
        { key: "occurred_at", header: "Date", accessor: r => r.occurred_at, render: r => new Date(r.occurred_at).toLocaleString() },
        { key: "operation_type", header: "Op", accessor: r => r.operation_type },
        { key: "part", header: "Part", accessor: r => partName.get(r.part_id) || r.part_id },
        { key: "qty", header: "Δ", accessor: r => r.quantity_delta },
        { key: "comments", header: "Comments", accessor: r => r.comments ?? "" },
      ]}
    />
  );
}

export function StorageSettings() {
  const { storageId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data } = useQuery({ queryKey: useWsKey("storage", storageId), queryFn: () => api.get<StorageLocation>(`/storage/${storageId}`), enabled: !!storageId });
  if (!data) return null;
  async function patch(field: string, v: any) {
    await api.patch(`/storage/${storageId}`, { [field]: v });
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "storage", storageId) });
  }
  return (
    <div className="card p-4 max-w-xl space-y-3">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={data.single_part_only} onChange={e => patch("single_part_only", e.target.checked)} />
        Limit to a single part
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={data.existing_parts_only} onChange={e => patch("existing_parts_only", e.target.checked)} />
        Only allow existing parts
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={data.is_full} onChange={e => patch("is_full", e.target.checked)} />
        Mark as full
      </label>
    </div>
  );
}

export function StorageOther() {
  const { storageId } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data } = useQuery({ queryKey: useWsKey("storage", storageId), queryFn: () => api.get<StorageLocation>(`/storage/${storageId}`), enabled: !!storageId });
  if (!data) return null;
  async function arch() {
    await api.post(`/storage/${storageId}/archive`);
    qc.invalidateQueries({ queryKey: wsScope(workspaceId) });
    nav("/storage");
  }
  async function restore() {
    await api.post(`/storage/${storageId}/restore`);
    qc.invalidateQueries({ queryKey: wsScope(workspaceId) });
  }
  return (
    <div className="card p-4 max-w-xl">
      {data.archived_at ? (
        <button className="btn" onClick={restore}>Restore</button>
      ) : (
        <button className="btn-danger" onClick={arch}>Archive</button>
      )}
    </div>
  );
}
