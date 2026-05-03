import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import EntityHeader from "@/components/EntityHeader";
import { DataTable } from "@/components/DataTable";
import AttachmentsPanel from "@/components/AttachmentsPanel";
import ActivityTimeline from "@/components/ActivityTimeline";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Order, OrderEntry, Part, StorageLocation } from "@/types";

type DetailOut = { order: Order; entries: OrderEntry[] };

type AddEntryRequest = {
  part_id?: string;
  name?: string;
  quantity_ordered: number;
  unit_price?: string;
  currency?: string;
};

type ReceiveLine = {
  order_entry_id: string;
  quantity: number;
  storage_location_id?: string;
  serial_number?: string;
};

type ReceiveRequest = {
  received_on?: string;
  lines: ReceiveLine[];
};

export default function OrderDetail() {
  const { orderId } = useParams<{ orderId: string }>();
  const qc = useQueryClient();
  const nav = useNavigate();
  const confirm = useConfirm();
  const { workspaceId } = useAuth();

  const { data, isError, error } = useQuery({
    queryKey: useWsKey("order", orderId),
    queryFn: () => api.get<DetailOut>(`/orders/${orderId}`),
    enabled: !!orderId,
  });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts?limit=200") });
  const { data: storage } = useQuery({ queryKey: useWsKey("storage"), queryFn: () => api.get<StorageLocation[]>("/storage") });

  const partsById = new Map(parts?.map(p => [p.id, p]) ?? []);
  const storageById = new Map(storage?.map(s => [s.id, s]) ?? []);

  const [adding, setAdding] = useState(false);
  const [newPartId, setNewPartId] = useState("");
  const [newName, setNewName] = useState("");
  const [newQty, setNewQty] = useState<number>(1);
  const [newPrice, setNewPrice] = useState<string>("");

  // Per-entry receive state
  const [receiveLines, setReceiveLines] = useState<Record<string, { qty: number; storage: string; serial?: string }>>({});
  const [receivedOn, setReceivedOn] = useState("");
  // Inline error surface — preserved for the handful of branches that
  // still want a banner (e.g. "enter a quantity on at least one row");
  // mutation failures route through `mutation.error` instead.
  const [err, setErr] = useState<string | null>(null);

  // ---- Mutations (FE2-006) -------------------------------------------------
  // `mutationKey` ties concurrent submits from the same user/tab to one
  // in-flight POST so a double-click on "Add" can't append the same line
  // twice (the original bug called out in the issue body). The Add
  // button is also gated on `isPending` for belt-and-braces UX.
  const addEntryMutation = useApiMutation<{ id: string } | unknown, AddEntryRequest>({
    mutationKey: ["order", orderId, "add-entry"],
    mutationFn: (input) =>
      api.post<{ id: string }, AddEntryRequest>(`/orders/${orderId}/entries`, input),
    onSuccess: () => {
      setAdding(false);
      setNewPartId("");
      setNewName("");
      setNewQty(1);
      setNewPrice("");
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "order", orderId) });
    },
  });

  const removeEntryMutation = useApiMutation<unknown, string>({
    // Per-entry key — different entries can be deleted concurrently,
    // but the same entry can't be deleted twice in flight.
    mutationFn: (entryId) => api.delete(`/orders/${orderId}/entries/${entryId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "order", orderId) });
      toast.success("Entry deleted.");
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Delete failed");
    },
  });

  const receiveMutation = useApiMutation<unknown, ReceiveRequest>({
    mutationKey: ["order", orderId, "receive"],
    mutationFn: (input) => api.post(`/orders/${orderId}/receive`, input),
    onSuccess: (_data, vars) => {
      const totalQty = vars.lines.reduce((s, l) => s + l.quantity, 0);
      setReceiveLines({});
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "order", orderId) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      toast.success(`Received ${totalQty} unit${totalQty === 1 ? "" : "s"}.`);
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.userMessage : "Receive failed";
      setErr(msg);
      toast.error(msg);
    },
  });

  const archiveMutation = useApiMutation<unknown, { wasArchived: boolean }>({
    mutationKey: ["order", orderId, "archive"],
    mutationFn: ({ wasArchived }) =>
      api.post(`/orders/${orderId}/${wasArchived ? "restore" : "archive"}`),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "order", orderId) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "orders") });
      toast.success(vars.wasArchived ? "Order restored." : "Order archived.");
      if (!vars.wasArchived) nav("/orders");
    },
  });

  if (isError) return <div className="text-red-600 text-sm p-4">Failed to load order. {error instanceof ApiError ? error.userMessage : ""}</div>;
  if (!data) return <div className="text-muted">Loading…</div>;
  const { order, entries } = data;
  const isClosed = order.status === "received" || order.status === "cancelled" || !!order.archived_at;

  function addEntry() {
    setErr(null);
    addEntryMutation.mutate(
      {
        part_id: newPartId || undefined,
        name: newName || undefined,
        quantity_ordered: newQty,
        unit_price: newPrice ? newPrice : undefined,
        currency: order.currency || undefined,
      },
      {
        onError: (e) => {
          setErr(e instanceof ApiError ? e.userMessage : "Failed");
        },
      },
    );
  }

  async function removeEntry(entryId: string) {
    if (!(await confirm({ message: "Delete this entry?", severity: "danger" }))) return;
    removeEntryMutation.mutate(entryId);
  }

  function doReceive() {
    const lines = Object.entries(receiveLines)
      .filter(([, v]) => v.qty > 0)
      .map(([entryId, v]) => ({
        order_entry_id: entryId,
        quantity: v.qty,
        storage_location_id: v.storage || undefined,
        serial_number: v.serial?.trim() || undefined,
      }));
    if (lines.length === 0) {
      setErr("Enter a quantity on at least one row.");
      return;
    }
    setErr(null);
    receiveMutation.mutate({
      received_on: receivedOn || undefined,
      lines,
    });
  }

  function doArchive() {
    archiveMutation.mutate({ wasArchived: !!order.archived_at });
  }

  return (
    <div>
      <EntityHeader
        title={order.name}
        subtitle={
          <span>
            {order.supplier || "—"}
            <span className="pill ml-2">{order.status}</span>
            {order.archived_at && <span className="pill ml-2 bg-danger/20 text-danger">archived</span>}
          </span>
        }
        stats={[
          { label: "Ordered",     value: order.totals.ordered },
          { label: "Received",    value: order.totals.received, tone: order.status === "received" ? "success" : "default" },
          {
            label: "Outstanding",
            value: order.totals.ordered - order.totals.received,
            tone: order.totals.received < order.totals.ordered ? "warning" : "default",
          },
          ...(order.currency ? [{ label: "Currency", value: order.currency } as const] : []),
        ]}
        actions={
          <button className="btn" onClick={doArchive} disabled={archiveMutation.isPending}>
            {order.archived_at ? "Restore" : "Archive"}
          </button>
        }
      />

      {err && <div className="card p-3 text-danger text-sm mb-3">{err}</div>}

      <div className="card p-4 mb-4">
        <div className="flex items-center mb-3">
          <h3 className="text-md font-semibold">Lines</h3>
          {!isClosed && (
            <button className="btn ml-auto" onClick={() => setAdding(a => !a)}>
              {adding ? "Cancel" : "+ Line"}
            </button>
          )}
        </div>
        {adding && (
          <div className="grid grid-cols-5 gap-2 mb-3 items-end">
            <div className="col-span-2">
              <label className="label" htmlFor="order-entry-part">Part</label>
              <select id="order-entry-part" className="input" value={newPartId} onChange={e => setNewPartId(e.target.value)}>
                <option value="">— free text —</option>
                {parts?.filter(p => !p.archived_at).map(p => (
                  <option key={p.id} value={p.id}>{p.name}{p.mpn ? ` — ${p.mpn}` : ""}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="order-entry-free-name">Free-text name</label>
              <input id="order-entry-free-name" className="input" value={newName} onChange={e => setNewName(e.target.value)} disabled={!!newPartId} />
            </div>
            <div>
              <label className="label" htmlFor="order-entry-qty">Qty</label>
              <input id="order-entry-qty" className="input" type="number" min={1} step={1} value={newQty} onChange={e => setNewQty(Number(e.target.value))} />
            </div>
            <div>
              <label className="label" htmlFor="order-entry-price">Unit price</label>
              <input id="order-entry-price" className="input" type="number" step="0.0001" value={newPrice} onChange={e => setNewPrice(e.target.value)} />
            </div>
            <div className="col-span-5">
              {/* Disable on isPending — this is the OrderDetail double-submit
                  fix called out in the issue body. The mutationKey on the
                  hook is the second line of defence. */}
              <button className="btn-primary" onClick={addEntry} disabled={addEntryMutation.isPending}>
                {addEntryMutation.isPending ? "Adding…" : "Add"}
              </button>
            </div>
          </div>
        )}
        <DataTable
          rows={entries}
          rowKey={r => r.id}
          tableId="order-lines"
          empty="No lines yet."
          columns={[
            { key: "part", header: "Part", accessor: r => r.part_id ? (partsById.get(r.part_id)?.name ?? "") : (r.name ?? "") },
            { key: "qty", header: "Ordered", accessor: r => r.quantity_ordered, width: "90px" },
            { key: "got", header: "Received", accessor: r => r.quantity_received, width: "90px" },
            {
              key: "price", header: "Unit price",
              accessor: r => r.unit_price ?? "",
              render: r => r.unit_price != null ? <span className="tabular-nums">{r.unit_price.toFixed(4)} {r.currency || order.currency || ""}</span> : <span className="text-muted">—</span>,
            },
            {
              key: "actions", header: "", accessor: () => "",
              render: r => !isClosed && r.quantity_received === 0 ? (
                <button
                  className="btn-danger text-xs"
                  onClick={() => removeEntry(r.id)}
                  disabled={removeEntryMutation.isPending && removeEntryMutation.variables === r.id}
                >
                  Delete
                </button>
              ) : null,
            },
          ]}
        />
      </div>

      {!isClosed && entries.some(e => e.part_id && e.quantity_received < e.quantity_ordered) && (
        <div className="card p-4">
          <h3 className="text-md font-semibold mb-3">Receive</h3>
          <div className="text-sm text-muted mb-2">
            Enter a quantity per line and (optionally) a storage location. Lines with no part are skipped — match them first by editing the line.
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Part</th>
                <th className="w-24">Outstanding</th>
                <th className="w-28">Receive</th>
                <th className="w-64">Storage</th>
                <th className="w-40">Serial #</th>
              </tr>
            </thead>
            <tbody>
              {entries.filter(e => e.part_id && e.quantity_received < e.quantity_ordered).map(e => {
                const outstanding = e.quantity_ordered - e.quantity_received;
                const cur = receiveLines[e.id] ?? { qty: 0, storage: "", serial: "" };
                const part = partsById.get(e.part_id!);
                return (
                  <tr key={e.id}>
                    <td>
                      {part?.name ?? e.part_id}
                      {part?.serialized && <span className="pill ml-2 bg-warning/20 text-warning">serialized</span>}
                    </td>
                    <td className="tabular-nums">{outstanding}</td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        min={0}
                        max={outstanding}
                        step={1}
                        value={cur.qty || ""}
                        onChange={ev => setReceiveLines(s => ({ ...s, [e.id]: { ...cur, qty: Number(ev.target.value) } }))}
                      />
                    </td>
                    <td>
                      <select
                        className="input"
                        value={cur.storage}
                        onChange={ev => setReceiveLines(s => ({ ...s, [e.id]: { ...cur, storage: ev.target.value } }))}
                      >
                        <option value="">— none —</option>
                        {storage?.filter(s => !s.archived_at && !s.is_full).map(s => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        placeholder={part?.serialized ? "required" : ""}
                        value={cur.serial ?? ""}
                        onChange={ev => setReceiveLines(s => ({ ...s, [e.id]: { ...cur, serial: ev.target.value } }))}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="grid grid-cols-3 gap-2 mt-3 items-end">
            <div>
              <label className="label" htmlFor="order-receive-on">Received on</label>
              <input id="order-receive-on" className="input" type="date" value={receivedOn} onChange={e => setReceivedOn(e.target.value)} />
            </div>
            <div className="col-span-2 flex justify-end">
              <button
                className="btn-primary"
                onClick={doReceive}
                disabled={receiveMutation.isPending}
              >
                {receiveMutation.isPending ? "Receiving…" : "Receive"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="grid lg:grid-cols-2 gap-4 mt-4">
        <AttachmentsPanel objectType="order" objectId={order.id} canWrite={!order.archived_at} />
        <ActivityTimeline endpoint={`/orders/${order.id}/activity`} />
      </div>
    </div>
  );
}
