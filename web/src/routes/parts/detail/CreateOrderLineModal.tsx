import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { ExternalLink } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { isSafeHttpUrl } from "@/lib/url";
import type { Order, OrderEntry } from "@/types";

const CREATE_NEW = "__create_new__";

export type CreateOrderLineSource = {
  partId: string;
  distributor: string;
  packaging: string | null;
  leadTimeDays: number | null;
  fetchedAt: string | null;
  quantity: number;
  unitPrice: number | null;
  currency: string | null;
  productUrl: string | null;
};

type OrderEntryPayload = {
  part_id: string;
  quantity_ordered: number;
  unit_price?: string;
  currency?: string;
  comments?: string;
};

type OrderCreatePayload = {
  name: string;
  order_type: "purchase";
  supplier?: string;
  currency?: string;
  entries: OrderEntryPayload[];
};

export function buildComplianceSafeOrderLineNote(source: CreateOrderLineSource): string {
  const packaging = source.packaging?.trim() || "unknown";
  const leadTime = source.leadTimeDays == null ? "unknown" : String(source.leadTimeDays);
  const fetchedAt = source.fetchedAt?.trim() || "unknown";
  return `From TrustedParts: distributor=${source.distributor}, packaging=${packaging}, lead_time=${leadTime} days, fetched_at=${fetchedAt}`;
}

function stripUrls(value: string): string {
  return value.replace(/https?:\/\/\S+/gi, "[link omitted]").trim();
}

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function orderLabel(order: Order | undefined, fallbackId: string): string {
  return order?.name || fallbackId.slice(0, 8);
}

export function CreateOrderLineModal({
  open,
  source,
  onClose,
}: {
  open: boolean;
  source: CreateOrderLineSource | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { workspaceId } = useAuth();
  const ordersKey = useWsKey("orders", "draft");
  const [targetOrderId, setTargetOrderId] = useState("");
  const [name, setName] = useState("");
  const [supplier, setSupplier] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [unitPrice, setUnitPrice] = useState("");
  const [currency, setCurrency] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const ordersQuery = useQuery({
    queryKey: ordersKey,
    queryFn: ({ signal }) => api.get<Order[]>("/orders?order_status=draft", { signal }),
    enabled: open,
  });

  const draftOrders = useMemo(() => ordersQuery.data ?? [], [ordersQuery.data]);
  const selectedOrder = draftOrders.find(order => order.id === targetOrderId);
  const isCreatingNew = targetOrderId === CREATE_NEW;

  useEffect(() => {
    if (!open || !source) return;
    setTargetOrderId("");
    setName(`TrustedParts ${source.distributor} ${todayIsoDate()}`);
    setSupplier(source.distributor);
    setQuantity(Math.max(1, Math.floor(source.quantity || 1)));
    setUnitPrice(source.unitPrice == null ? "" : String(source.unitPrice));
    setCurrency(source.currency ?? "");
    setNote(buildComplianceSafeOrderLineNote(source));
    setError(null);
  }, [open, source]);

  useEffect(() => {
    if (!open || targetOrderId || draftOrders.length === 0) return;
    setTargetOrderId(draftOrders[0].id);
  }, [draftOrders, open, targetOrderId]);

  const entryPayload = useMemo<OrderEntryPayload | null>(() => {
    if (!source) return null;
    const cleanCurrency = currency.trim().toUpperCase();
    const cleanUnitPrice = unitPrice.trim();
    return {
      part_id: source.partId,
      quantity_ordered: Math.max(1, Math.floor(quantity || 1)),
      unit_price: cleanUnitPrice || undefined,
      currency: cleanCurrency || undefined,
      comments: stripUrls(note) || undefined,
    };
  }, [currency, note, quantity, source, unitPrice]);

  const submitMutation = useApiMutation<Order | OrderEntry, void>({
    mutationKey: ["parts", source?.partId, "create-order-line"],
    mutationFn: async () => {
      if (!entryPayload) throw new Error("Missing source row");
      if (isCreatingNew) {
        return api.post<Order, OrderCreatePayload>("/orders", {
          name: name.trim(),
          order_type: "purchase",
          supplier: supplier.trim() || undefined,
          currency: currency.trim().toUpperCase() || undefined,
          entries: [entryPayload],
        });
      }
      if (!targetOrderId) throw new Error("Pick an order");
      return api.post<OrderEntry, OrderEntryPayload>(
        `/orders/${targetOrderId}/entries`,
        entryPayload,
      );
    },
    onSuccess: result => {
      const orderId = "order_id" in result ? result.order_id : result.id;
      const label = "order_id" in result
        ? orderLabel(selectedOrder, result.order_id)
        : orderLabel(result, result.id);
      queryClient.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "orders") });
      queryClient.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "orders", orderId) });
      queryClient.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "order", orderId) });
      toast.success("Order line created", {
        action: {
          label: `Open ${label}`,
          onClick: () => window.location.assign(`/orders/${orderId}`),
        },
      });
      onClose();
    },
    onError: err => {
      setError(err instanceof ApiError ? err.userMessage : "Failed to create order line");
    },
  });

  if (!open || !source) return null;
  const safeProductUrl = isSafeHttpUrl(source.productUrl) ? source.productUrl : null;

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (isCreatingNew && !name.trim()) {
      setError("Order name is required.");
      return;
    }
    submitMutation.mutate();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-order-line-title"
      onMouseDown={event => {
        if (event.target === event.currentTarget && !submitMutation.isPending) onClose();
      }}
    >
      <form className="card w-full max-w-2xl p-4 shadow-lg" onSubmit={submit}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="create-order-line-title" className="text-base font-semibold text-text">
              Create order line
            </h2>
            <p className="mt-1 text-sm text-muted">{source.distributor}</p>
          </div>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={onClose}
            disabled={submitMutation.isPending}
          >
            Close
          </button>
        </div>

        {error && (
          <div className="mt-3 card p-3 text-sm text-danger" role="alert">
            {error}
          </div>
        )}

        <div className="mt-4 space-y-3">
          <label className="label" htmlFor="order-line-order">
            Order
            <select
              id="order-line-order"
              className="input"
              value={targetOrderId}
              onChange={event => setTargetOrderId(event.currentTarget.value)}
              disabled={ordersQuery.isLoading || submitMutation.isPending}
              required
            >
              <option value="" disabled>
                {ordersQuery.isLoading ? "Loading draft orders..." : "Choose an order"}
              </option>
              {draftOrders.map(order => (
                <option key={order.id} value={order.id}>
                  {order.name}
                </option>
              ))}
              <option value={CREATE_NEW}>Or create a new order</option>
            </select>
          </label>

          {isCreatingNew && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="label" htmlFor="order-line-name">
                Order name
                <input
                  id="order-line-name"
                  className="input"
                  value={name}
                  onChange={event => setName(event.currentTarget.value)}
                  disabled={submitMutation.isPending}
                  required
                />
              </label>
              <label className="label" htmlFor="order-line-supplier">
                Supplier
                <input
                  id="order-line-supplier"
                  className="input"
                  value={supplier}
                  onChange={event => setSupplier(event.currentTarget.value)}
                  disabled={submitMutation.isPending}
                />
              </label>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="label" htmlFor="order-line-quantity">
              Quantity
              <input
                id="order-line-quantity"
                className="input"
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={quantity}
                onChange={event => setQuantity(Number(event.currentTarget.value))}
                disabled={submitMutation.isPending}
                required
              />
            </label>
            <label className="label" htmlFor="order-line-unit-price">
              Unit price
              <input
                id="order-line-unit-price"
                className="input"
                type="number"
                min={0}
                step="0.000001"
                value={unitPrice}
                onChange={event => setUnitPrice(event.currentTarget.value)}
                disabled={submitMutation.isPending}
              />
            </label>
            <label className="label" htmlFor="order-line-currency">
              Currency
              <input
                id="order-line-currency"
                className="input"
                maxLength={3}
                value={currency}
                onChange={event => setCurrency(event.currentTarget.value.toUpperCase())}
                disabled={submitMutation.isPending}
              />
            </label>
          </div>

          <label className="label" htmlFor="order-line-note">
            Note
            <textarea
              id="order-line-note"
              className="input"
              rows={3}
              value={note}
              onChange={event => setNote(event.currentTarget.value)}
              disabled={submitMutation.isPending}
            />
          </label>

          {safeProductUrl && (
            <a
              href={safeProductUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm text-accent hover:underline"
            >
              Open distributor page
              <ExternalLink size={14} aria-hidden="true" />
            </a>
          )}
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="btn-ghost"
            onClick={onClose}
            disabled={submitMutation.isPending}
          >
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={submitMutation.isPending}>
            {submitMutation.isPending ? "Creating..." : "Create order line"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default CreateOrderLineModal;
