import type { PurchasePlanLine } from "./purchasePlanTypes";

type Props = {
  line: PurchasePlanLine | null;
  onClose: () => void;
};

export default function OverrideOfferModal({ line, onClose }: Props) {
  if (!line) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div
        className="card max-w-md w-full p-4 space-y-4"
        role="dialog"
        aria-modal="true"
        aria-labelledby="override-offer-title"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="override-offer-title" className="text-lg font-semibold">
            Override offer
          </h2>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="text-sm text-muted">
          Override requires backend validation support and is disabled for this phase.
        </div>
        <div className="rounded border border-border p-3 text-sm">
          <div className="font-medium">{line.mpn_searched}</div>
          <div className="text-muted">{line.selected_distributor ?? "Unfilled"}</div>
        </div>
      </div>
    </div>
  );
}
