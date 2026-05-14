import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import type { StorageLocation } from "@/types";

/**
 * Mirror of `backend/app/domain/stock/schemas.py::AddStockIn` (extra="forbid").
 * Optional sub-objects (`price`, `lot`) follow the BE Pydantic shape so a
 * future schema change surfaces as a TS error in this file rather than as
 * silent FE/BE drift on a 4xx response.
 */
type AddStockRequest = {
  part_id: string;
  quantity: number;
  storage_location_id?: string;
  price?: {
    mode: "per_component" | "entire_lot";
    currency: string;
    unit_price?: number;
    total_price?: number;
  };
  lot?: { name?: string; serial_number?: string };
  comments?: string;
};

const currencySchema = z.string().regex(/^[A-Z]{3}$/, "Currency must be a three-letter uppercase code.");

export default function PartAddStock() {
  const { partId } = useParams<{ partId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const storageQuery = useQuery({ queryKey: useWsKey("storage"), queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }) });
  const { data: storage } = storageQuery;
  const [qty, setQty] = useState<number>(0);
  const [location, setLocation] = useState("");
  const [priceMode, setPriceMode] = useState<"none" | "per_component" | "entire_lot">("none");
  const [unitPrice, setUnitPrice] = useState<string>("");
  const [totalPrice, setTotalPrice] = useState<string>("");
  const [currency, setCurrency] = useState("USD");
  const [lotName, setLotName] = useState("");
  const [serial, setSerial] = useState("");
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [currencyErr, setCurrencyErr] = useState<string | null>(null);

  // FE2-006: stock-add is the most damaging double-submit on the
  // ledger model — two concurrent requests would append two
  // `stock_entries` rows. Key on the part to hard-block double POST.
  const addMutation = useApiMutation<unknown, AddStockRequest>({
    mutationKey: ["part", partId, "stock-add"],
    mutationFn: (payload) => api.post<unknown, AddStockRequest>("/stock/add", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      nav(`/parts/${partId}/stock`);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setCurrencyErr(null);
    const payload: AddStockRequest = {
      part_id: partId!,
      quantity: Number(qty),
      comments: comments || undefined,
    };
    if (location) payload.storage_location_id = location;
    if (priceMode !== "none") {
      const parsedCurrency = currencySchema.safeParse(currency);
      if (!parsedCurrency.success) {
        setCurrencyErr(parsedCurrency.error.issues[0]?.message ?? "Invalid currency.");
        return;
      }
      payload.price = {
        mode: priceMode,
        currency: parsedCurrency.data,
        unit_price: priceMode === "per_component" ? Number(unitPrice) : undefined,
        total_price: priceMode === "entire_lot" ? Number(totalPrice) : undefined,
      };
    }
    if (lotName || serial) payload.lot = { name: lotName || undefined, serial_number: serial || undefined };
    addMutation.mutate(payload);
  }

  const busy = addMutation.isPending;

  return (
    <form onSubmit={submit} className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">Add stock</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <InlineQueryError query={storageQuery} label="storage locations" />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="add-stock-qty">Quantity *</label>
          <input id="add-stock-qty" className="input" type="number" min={1} required value={qty || ""} onChange={e => setQty(Number(e.target.value))} />
        </div>
        <div>
          <label className="label" htmlFor="add-stock-storage">Storage location</label>
          <select id="add-stock-storage" className="input" value={location} onChange={e => setLocation(e.target.value)}>
            <option value="">— none —</option>
            {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="label" htmlFor="add-stock-price-mode">Price mode</label>
          <select id="add-stock-price-mode" className="input" value={priceMode} onChange={e => setPriceMode(e.target.value as "none" | "per_component" | "entire_lot")}>
            <option value="none">No price</option>
            <option value="per_component">Per component</option>
            <option value="entire_lot">Entire lot</option>
          </select>
        </div>
        {priceMode === "per_component" && (
          <div>
            <label className="label" htmlFor="add-stock-unit-price">Unit price</label>
            <input id="add-stock-unit-price" className="input" type="number" step="0.0001" value={unitPrice} onChange={e => setUnitPrice(e.target.value)} />
          </div>
        )}
        {priceMode === "entire_lot" && (
          <div>
            <label className="label" htmlFor="add-stock-lot-total">Lot total</label>
            <input id="add-stock-lot-total" className="input" type="number" step="0.01" value={totalPrice} onChange={e => setTotalPrice(e.target.value)} />
          </div>
        )}
        {priceMode !== "none" && (
          <div>
            <label className="label" htmlFor="add-stock-currency">Currency</label>
            <input
              id="add-stock-currency"
              className="input"
              maxLength={3}
              value={currency}
              aria-invalid={currencyErr ? "true" : undefined}
              aria-describedby={currencyErr ? "add-stock-currency-error" : undefined}
              onChange={e => {
                setCurrency(e.target.value.toUpperCase());
                setCurrencyErr(null);
              }}
            />
            {currencyErr && (
              <p id="add-stock-currency-error" className="mt-1 text-danger text-sm">
                {currencyErr}
              </p>
            )}
          </div>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="add-stock-lot-name">Lot name (optional)</label>
          <input id="add-stock-lot-name" className="input" value={lotName} onChange={e => setLotName(e.target.value)} placeholder="LOT-2026-001" />
        </div>
        <div>
          <label className="label" htmlFor="add-stock-serial">Serial number (optional)</label>
          <input id="add-stock-serial" className="input" value={serial} onChange={e => setSerial(e.target.value)} placeholder="SN-…" />
        </div>
      </div>
      <div>
        <label className="label" htmlFor="add-stock-comments">Comments</label>
        <textarea id="add-stock-comments" className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <button className="btn-primary" disabled={busy}>{busy ? "Adding…" : "Add"}</button>
      </div>
    </form>
  );
}
