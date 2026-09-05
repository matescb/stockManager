import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { wsKeyOf } from "@/lib/queryKeys";
import type { Order } from "@/types";

type OrderCreatePayload = {
  name: string;
  supplier?: string;
  currency?: string;
  ordered_on?: string;
  expected_on?: string;
  comments?: string;
};

export default function OrderCreate() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [name, setName] = useState("");
  const [supplier, setSupplier] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [orderedOn, setOrderedOn] = useState("");
  const [expectedOn, setExpectedOn] = useState("");
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const createMutation = useApiMutation<Order, OrderCreatePayload>({
    mutationKey: ["order", "create"],
    mutationFn: (payload) => api.post<Order>("/orders", payload),
    onSuccess: (o) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "orders") });
      nav(`/orders/${o.id}`);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const busy = createMutation.isPending;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    createMutation.mutate({
      name,
      supplier: supplier || undefined,
      currency: currency || undefined,
      ordered_on: orderedOn || undefined,
      expected_on: expectedOn || undefined,
      comments: comments || undefined,
    });
  }

  return (
    <form onSubmit={submit} className="card p-4 max-w-2xl space-y-3">
      <h1 className="page-title">New order</h1>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div>
        <label className="label" htmlFor="order-create-name">Name *</label>
        <input id="order-create-name" className="input" required value={name} onChange={e => setName(e.target.value)} placeholder="PO-2026-001" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="order-create-supplier">Supplier</label>
          <input id="order-create-supplier" className="input" value={supplier} onChange={e => setSupplier(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="order-create-currency">Currency</label>
          <input id="order-create-currency" className="input" maxLength={3} value={currency} onChange={e => setCurrency(e.target.value.toUpperCase())} />
        </div>
        <div>
          <label className="label" htmlFor="order-create-ordered-on">Ordered on</label>
          <input id="order-create-ordered-on" className="input" type="date" value={orderedOn} onChange={e => setOrderedOn(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="order-create-expected-on">Expected on</label>
          <input id="order-create-expected-on" className="input" type="date" value={expectedOn} onChange={e => setExpectedOn(e.target.value)} />
        </div>
      </div>
      <div>
        <label className="label" htmlFor="order-create-comments">Comments</label>
        <textarea id="order-create-comments" className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <div>
        <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create order"}</button>
      </div>
    </form>
  );
}
