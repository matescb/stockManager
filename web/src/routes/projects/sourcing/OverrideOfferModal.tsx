import { DataTable, type Column } from "@/components/DataTable";
import { Modal } from "@/components/Modal";
import { isSafeHttpUrl } from "@/lib/url";
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

type OfferRow = {
  key: string;
  offer: PurchasePlanOffer;
  isCurrent: boolean;
};

export default function OverrideOfferModal({ line, onSelect, onClose }: Props) {
  if (!line) return null;

  const rows: OfferRow[] = (line.available_offers ?? [])
    .map((offer, index) => ({
      key: [
        offer.distributor ?? "unknown",
        offer.mpn ?? "mpn",
        offer.url ?? "no-url",
        String(offer.unit_price ?? "no-price"),
        index,
      ].join("-"),
      offer,
      isCurrent: isCurrentOffer(line, offer),
    }));

  const columns: Column<OfferRow>[] = [
    {
      key: "distributor",
      header: "Distributor",
      accessor: row => row.offer.distributor ?? "",
      render: row => (
        <div>
          <div className="font-medium">{row.offer.distributor ?? "-"}</div>
          {row.isCurrent ? <div className="text-xs text-muted">Current selection</div> : null}
        </div>
      ),
    },
    {
      key: "mpn",
      header: "MPN",
      accessor: row => row.offer.mpn ?? line.mpn_searched,
      render: row => row.offer.mpn ?? line.mpn_searched,
    },
    {
      key: "stock",
      header: "Stock",
      accessor: row => numericValue(row.offer.stock),
      render: row => formatValue(row.offer.stock),
      align: "right",
    },
    {
      key: "unit_price",
      header: "Unit price",
      accessor: row => numericValue(row.offer.unit_price),
      render: row => formatMoney(row.offer.unit_price, row.offer.currency),
      align: "right",
    },
    {
      key: "packaging",
      header: "Packaging",
      accessor: row => row.offer.packaging ?? "",
      render: row => formatValue(row.offer.packaging),
    },
    {
      key: "moq",
      header: "MOQ",
      accessor: row => numericValue(row.offer.moq),
      render: row => formatValue(row.offer.moq),
      align: "right",
    },
    {
      key: "lead_time",
      header: "Lead time",
      accessor: row => row.offer.lead_time_days,
      render: row => formatLeadTime(row.offer.lead_time_days),
      align: "right",
    },
    {
      key: "offer",
      header: "Offer",
      accessor: row => row.offer.url ?? "",
      render: row => {
        const safeOfferUrl = isSafeHttpUrl(row.offer.url) ? row.offer.url : null;
        return safeOfferUrl ? (
          <a
            className="text-accent hover:underline"
            href={safeOfferUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open
          </a>
        ) : "-";
      },
    },
    {
      key: "action",
      header: "Action",
      headerLabel: "Action",
      render: row => (
        <button
          type="button"
          className="btn"
          disabled={row.isCurrent || !canSelectOffer(line, row.offer)}
          onClick={() => onSelect(line, row.offer)}
        >
          {row.isCurrent ? "Current" : "Select"}
        </button>
      ),
      align: "right",
    },
  ];

  return (
    <Modal
      open={Boolean(line)}
      onClose={onClose}
      title="Override offer"
      className="card max-w-5xl w-full max-h-[85vh] overflow-hidden flex flex-col"
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
        {rows.length === 0 ? (
          <div className="p-4 text-sm text-muted">
            No cached alternate offers are available for this line.
          </div>
        ) : (
          <DataTable
            rows={rows}
            columns={columns}
            rowKey={row => row.key}
            tableId="purchase-plan-override-offers"
            exportFilename="override-offers"
            searchPlaceholder="Search offers..."
          />
        )}
      </div>
    </Modal>
  );
}
