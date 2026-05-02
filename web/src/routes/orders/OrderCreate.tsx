import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import type { Order } from "@/types";

export default function OrderCreate() {
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [supplier, setSupplier] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [orderedOn, setOrderedOn] = useState("");
  const [expectedOn, setExpectedOn] = useState("");
  const [comments, setComments] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const o = await api.post<Order>("/orders", {
        name,
        supplier: supplier || undefined,
        currency: currency || undefined,
        ordered_on: orderedOn || undefined,
        expected_on: expectedOn || undefined,
        comments: comments || undefined,
      });
      nav(`/orders/${o.id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">New order</h3>
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
