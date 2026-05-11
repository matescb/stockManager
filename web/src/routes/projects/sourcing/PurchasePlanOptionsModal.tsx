import { useState, type FormEvent } from "react";
import { Modal } from "@/components/Modal";
import type { PurchasePlanRequest } from "./purchasePlanTypes";

type Props = {
  open: boolean;
  buildQuantity: number;
  baseRequest: Omit<PurchasePlanRequest, "strategy">;
  pending?: boolean;
  onClose: () => void;
  onSubmit: (request: PurchasePlanRequest) => void;
};

const strategies = [
  ["preferred_first", "Preferred first"],
  ["lowest_total_price", "Lowest total price"],
  ["fewest_distributors", "Fewest distributors"],
  ["fastest_availability", "Fastest availability"],
] as const;

export default function PurchasePlanOptionsModal({
  open,
  buildQuantity,
  baseRequest,
  pending = false,
  onClose,
  onSubmit,
}: Props) {
  const [strategy, setStrategy] = useState("preferred_first");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxDistributors, setMaxDistributors] = useState("");
  const [moqOverbuyCap, setMoqOverbuyCap] = useState("");
  const [priceTolerancePct, setPriceTolerancePct] = useState("5");

  if (!open) return null;

  function submit(event: FormEvent) {
    event.preventDefault();
    const request: PurchasePlanRequest = {
      ...baseRequest,
      build_quantity: Math.max(1, Math.floor(buildQuantity || 1)),
      strategy,
    };
    if (maxDistributors.trim()) request.max_distributors = Number(maxDistributors);
    if (moqOverbuyCap.trim()) request.moq_overbuy_cap = Number(moqOverbuyCap);
    if (priceTolerancePct.trim()) request.price_tolerance_pct = priceTolerancePct.trim();
    onSubmit(request);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Generate purchase plan"
      size="sm"
      className="card max-w-lg w-full"
    >
      <form className="p-4 space-y-4" onSubmit={submit}>
        <div className="flex items-start justify-between gap-3">
          <h2 id="purchase-plan-options-title" className="text-lg font-semibold">
            Generate purchase plan
          </h2>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>

        <div>
          <label className="label" htmlFor="purchase-plan-strategy">
            Strategy
          </label>
          <select
            id="purchase-plan-strategy"
            className="input"
            value={strategy}
            onChange={event => setStrategy(event.target.value)}
          >
            {strategies.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <button
          type="button"
          className="btn"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen(open => !open)}
        >
          Advanced options
        </button>

        {advancedOpen && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="label" htmlFor="purchase-plan-max-distributors">
                Max distributors
              </label>
              <input
                id="purchase-plan-max-distributors"
                className="input"
                type="number"
                min={1}
                value={maxDistributors}
                onChange={event => setMaxDistributors(event.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="purchase-plan-moq-cap">
                MOQ cap
              </label>
              <input
                id="purchase-plan-moq-cap"
                className="input"
                type="number"
                min={1}
                value={moqOverbuyCap}
                onChange={event => setMoqOverbuyCap(event.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="purchase-plan-tolerance">
                Tolerance %
              </label>
              <input
                id="purchase-plan-tolerance"
                className="input"
                type="number"
                min={0}
                step="0.1"
                value={priceTolerancePct}
                onChange={event => setPriceTolerancePct(event.target.value)}
              />
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={pending}>
            {pending ? "Generating..." : "Generate"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
