import type { PurchasePlanLine, PurchasePlanOffer } from "./purchasePlanTypes";

type Props = {
  line: PurchasePlanLine | null;
  onSelect: (line: PurchasePlanLine, offer: PurchasePlanOffer) => void;
  onClose: () => void;
};

function formatValue(value: string | number | null | undefined): string {
  if (value == null || value === "") return "-";
  return String(value);
}

function formatMoney(value: string | number | null | undefined, currency?: string | null): string {
  if (value == null || value === "") return "-";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return currency ? `${value} ${currency}` : String(value);
  const formatted = numeric.toLocaleString(undefined, {
    maximumFractionDigits: 4,
    minimumFractionDigits: numeric % 1 === 0 ? 0 : 2,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

function formatLeadTime(days: number | null | undefined): string {
  if (days == null) return "-";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

function numericValue(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isCurrentOffer(line: PurchasePlanLine, offer: PurchasePlanOffer): boolean {
  const sameDistributor =
    (offer.distributor ?? "").toLowerCase() === (line.selected_distributor ?? "").toLowerCase();
  if (!sameDistributor) return false;
  if (offer.url && line.selected_url) return offer.url === line.selected_url;
  return (
    numericValue(offer.unit_price) === numericValue(line.selected_unit_price) &&
    (offer.currency ?? "").toUpperCase() === (line.selected_currency ?? "").toUpperCase()
  );
}

function selectedQtyForOffer(line: PurchasePlanLine, offer: PurchasePlanOffer): number {
  const shortage = Math.max(0, line.shortage_qty);
  const moq = Math.max(0, numericValue(offer.moq) ?? 0);
  return Math.max(shortage, moq, 1);
}

function canSelectOffer(line: PurchasePlanLine, offer: PurchasePlanOffer): boolean {
  const qty = selectedQtyForOffer(line, offer);
  const stock = numericValue(offer.stock);
  return Boolean(
    offer.distributor &&
    offer.unit_price != null &&
    offer.currency &&
    stock != null &&
    stock >= qty,
  );
}

export default function OverrideOfferModal({ line, onSelect, onClose }: Props) {
  if (!line) return null;

  const offers = (line.available_offers ?? []).filter(offer => !isCurrentOffer(line, offer));

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div
        className="card max-w-5xl w-full max-h-[85vh] overflow-hidden flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby="override-offer-title"
      >
        <div className="flex items-start justify-between gap-3 p-4 border-b border-border">
          <div>
            <h2 id="override-offer-title" className="text-lg font-semibold">
              Override offer
            </h2>
            <div className="text-sm text-muted">
              {line.mpn_searched} - current: {line.selected_distributor ?? "Unfilled"}
            </div>
          </div>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="overflow-auto">
          {offers.length === 0 ? (
            <div className="p-4 text-sm text-muted">
              No cached alternate offers are available for this line.
            </div>
          ) : (
            <table className="table text-sm">
              <thead>
                <tr>
                  <th>Distributor</th>
                  <th>MPN</th>
                  <th className="text-right">Stock</th>
                  <th className="text-right">Unit price</th>
                  <th>Packaging</th>
                  <th className="text-right">MOQ</th>
                  <th className="text-right">Lead time</th>
                  <th>Offer</th>
                  <th className="text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {offers.map((offer, index) => {
                  const key = `${offer.distributor ?? "unknown"}-${offer.mpn ?? "mpn"}-${index}`;
                  const isCurrent =
                    offer.distributor === line.selected_distributor &&
                    offer.unit_price === line.selected_unit_price &&
                    offer.currency === line.selected_currency;
                  return (
                    <tr key={key}>
                      <td>
                        <div className="font-medium">{offer.distributor ?? "-"}</div>
                        {isCurrent && <div className="text-xs text-muted">Current selection</div>}
                      </td>
                      <td>{offer.mpn ?? line.mpn_searched}</td>
                      <td className="text-right tabular-nums">{formatValue(offer.stock)}</td>
                      <td className="text-right tabular-nums">{formatMoney(offer.unit_price, offer.currency)}</td>
                      <td>{formatValue(offer.packaging)}</td>
                      <td className="text-right tabular-nums">{formatValue(offer.moq)}</td>
                      <td className="text-right tabular-nums">{formatLeadTime(offer.lead_time_days)}</td>
                      <td>
                        {offer.url ? (
                          <a
                            className="text-accent hover:underline"
                            href={offer.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Open
                          </a>
                        ) : "-"}
                      </td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn"
                          disabled={!canSelectOffer(line, offer)}
                          onClick={() => onSelect(line, offer)}
                        >
                          Select
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
