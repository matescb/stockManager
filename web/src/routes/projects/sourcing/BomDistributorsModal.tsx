import { useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { ExternalLink, Info, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { DataTable, type Column } from "@/components/DataTable";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { lifecycleRiskTone } from "@/lib/sourcing";
import type {
  SourcingBomLine,
  SourcingBomOffer,
  SourcingBomPriceBreak,
  SourcingRohsCompliance,
} from "./ProjectSourcingPage";

type Props = {
  open: boolean;
  onClose: () => void;
  line: SourcingBomLine | null;
  workspaceCurrency: string | null;
};

type DistributorRow = {
  id: string;
  offer: SourcingBomOffer;
  distributor: string;
  stock: number;
  availabilityText: string | null;
  unitPrice: number | null;
  currency: string | null;
  priceBreaks: { quantity: number; unitPrice: number; currency: string | null }[];
  moq: number | null;
  quantityMultiple: number | null;
  packaging: string | null;
  rohsCompliance: SourcingRohsCompliance[];
  link: string | null;
};

function numberOrNull(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function positiveMultiple(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(value) || value <= 1) return null;
  return Math.floor(value);
}

function formatNumber(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}

function formatPrice(value: number | null | undefined, currency: string | null | undefined): string {
  if (value == null) return "—";
  const formatted = value.toLocaleString(undefined, {
    maximumFractionDigits: 6,
    minimumFractionDigits: 0,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

function displayCurrency(offer: SourcingBomOffer, workspaceCurrency: string | null): string | null {
  if (offer.fx_converted === true && offer.unit_price_converted != null) {
    return offer.currency_displayed ?? workspaceCurrency ?? offer.currency ?? null;
  }
  return offer.currency_displayed ?? offer.currency ?? workspaceCurrency;
}

function displayUnitPrice(offer: SourcingBomOffer): number | null {
  if (offer.fx_converted === true && offer.unit_price_converted != null) {
    return numberOrNull(offer.unit_price_converted);
  }
  return numberOrNull(offer.unit_price);
}

function displayPriceBreaks(
  offer: SourcingBomOffer,
  currency: string | null,
): { quantity: number; unitPrice: number; currency: string | null }[] {
  const source = offer.fx_converted === true && offer.price_breaks_converted
    ? offer.price_breaks_converted
    : offer.price_breaks;

  return normalisePriceBreaks(source, currency);
}

function normalisePriceBreaks(
  breaks: SourcingBomPriceBreak[] | null | undefined,
  fallbackCurrency: string | null,
): { quantity: number; unitPrice: number; currency: string | null }[] {
  return (breaks ?? []).flatMap(priceBreak => {
    const unitPrice = numberOrNull(priceBreak.unit_price);
    if (unitPrice == null) return [];
    return [{
      quantity: priceBreak.quantity,
      unitPrice,
      currency: priceBreak.currency ?? fallbackCurrency,
    }];
  });
}

function sortedRohs(values: SourcingRohsCompliance[]): SourcingRohsCompliance[] {
  return [...values].sort((a, b) => a.region.localeCompare(b.region));
}

function RohsPills({ values }: { values: SourcingRohsCompliance[] }) {
  if (values.length === 0) return <span className="text-muted">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {sortedRohs(values).map(item => (
        <span
          key={item.region}
          className={`pill ${item.is_compliant ? "bg-success/10 text-success" : "bg-danger/10 text-danger"}`}
          title={item.description ?? undefined}
          aria-label={`${item.region}: ${item.is_compliant ? "RoHS compliant" : "RoHS non-compliant"}`}
        >
          {item.region}
        </span>
      ))}
    </div>
  );
}

function RiskPill({
  label,
  value,
  icon,
}: {
  label: string;
  value?: string | null;
  icon: ReactNode;
}) {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  return (
    <span className={`pill inline-flex items-center gap-1 ${lifecycleRiskTone(trimmed)}`} aria-label={`${label}: ${trimmed}`}>
      {icon}
      {trimmed}
    </span>
  );
}

function PriceBreaksCell({ row }: { row: DistributorRow }) {
  if (row.priceBreaks.length === 0) return <span className="text-muted">—</span>;
  return (
    <span className="flex flex-wrap justify-end gap-1">
      {row.priceBreaks.map(priceBreak => (
        <span key={`${priceBreak.quantity}:${priceBreak.unitPrice}:${priceBreak.currency ?? ""}`} className="pill font-mono">
          {priceBreak.quantity.toLocaleString()}+ {formatPrice(priceBreak.unitPrice, priceBreak.currency)}
        </span>
      ))}
    </span>
  );
}

function uniqueTrimmed(values: Array<string | null | undefined>): string[] {
  return Array.from(new Set(values.map(value => value?.trim()).filter((value): value is string => Boolean(value))))
    .sort((a, b) => a.localeCompare(b));
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(
    [
      "a[href]",
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[tabindex]:not([tabindex='-1'])",
    ].join(","),
  )).filter(element => element.getAttribute("aria-hidden") !== "true");
}

export function BomDistributorsModal({ open, onClose, line, workspaceCurrency }: Props) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    window.setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 0);

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = focusableElements(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (!dialog.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      const restoreFocus = restoreFocusRef.current;
      if (restoreFocus && document.contains(restoreFocus)) {
        restoreFocus.focus();
      }
      restoreFocusRef.current = null;
    };
  }, [open, onClose]);

  const rows = useMemo<DistributorRow[]>(() => {
    if (!line) return [];
    return line.offers.map((offer, index) => {
      const currency = displayCurrency(offer, workspaceCurrency?.trim().toUpperCase() || null);
      return {
        id: [
          offer.mpn,
          offer.distributor,
          offer.sku ?? "sku",
          index,
        ].join(":"),
        offer,
        distributor: offer.distributor,
        stock: offer.stock,
        availabilityText: offer.availability_text?.trim() || null,
        unitPrice: displayUnitPrice(offer),
        currency,
        priceBreaks: displayPriceBreaks(offer, currency),
        moq: offer.moq ?? null,
        quantityMultiple: positiveMultiple(offer.quantity_multiple),
        packaging: offer.packaging ?? null,
        rohsCompliance: offer.rohs_compliance ?? [],
        link: offer.url ?? null,
      };
    });
  }, [line, workspaceCurrency]);

  const statusPills = useMemo(() => {
    const lifecycle = uniqueTrimmed(rows.map(row => row.offer.lifecycle_risk));
    const supplyChain = uniqueTrimmed(rows.map(row => row.offer.supply_chain_risk));
    const tariffAffected = rows.some(row => row.offer.is_affected_by_tariff === true);
    return { lifecycle, supplyChain, tariffAffected };
  }, [rows]);

  const columns = useMemo<Column<DistributorRow>[]>(() => [
    {
      key: "distributor",
      header: "Distributor",
      accessor: row => row.distributor,
      render: row => row.link ? (
        <a
          href={row.link}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-accent hover:underline"
        >
          {row.distributor}
          <ExternalLink size={14} aria-hidden="true" />
        </a>
      ) : row.distributor,
    },
    {
      key: "stock",
      header: "Stock",
      accessor: row => row.stock,
      render: row => row.stock.toLocaleString(),
      align: "right",
    },
    {
      key: "availability",
      header: "Availability",
      accessor: row => row.availabilityText ?? "",
      render: row => row.availabilityText ?? <span className="text-muted">—</span>,
    },
    {
      key: "price",
      header: "Best unit price",
      accessor: row => row.unitPrice,
      render: row => formatPrice(row.unitPrice, row.currency),
      align: "right",
    },
    {
      key: "price_breaks",
      header: "Price breaks",
      accessor: row => row.priceBreaks.map(priceBreak => `${priceBreak.quantity}:${priceBreak.unitPrice}`).join(" "),
      render: row => <PriceBreaksCell row={row} />,
      align: "right",
    },
    {
      key: "moq",
      header: "MOQ",
      accessor: row => row.moq,
      render: row => formatNumber(row.moq),
      align: "right",
    },
    {
      key: "multiple",
      header: "Qty multiple",
      accessor: row => row.quantityMultiple,
      render: row => row.quantityMultiple == null ? (
        <span className="text-muted">—</span>
      ) : (
        <span className="pill" title="Order quantity should be a multiple of this value.">
          {row.quantityMultiple.toLocaleString()}
        </span>
      ),
      align: "right",
    },
    {
      key: "packaging",
      header: "Packaging",
      accessor: row => row.packaging ?? "",
      render: row => row.packaging ?? <span className="text-muted">—</span>,
    },
    {
      key: "rohs",
      header: "RoHS",
      accessor: row => row.rohsCompliance.map(item => `${item.region}:${item.is_compliant}`).join(" "),
      render: row => <RohsPills values={row.rohsCompliance} />,
    },
  ], []);

  if (!open || !line) return null;

  const totalDistributorCount = new Set(rows.map(row => row.distributor)).size;
  const stockedDistributorCount = new Set(rows.filter(row => row.stock > 0).map(row => row.distributor)).size;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bom-distributors-title"
      tabIndex={-1}
      ref={dialogRef}
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[90vh] w-full max-w-6xl flex-col rounded border border-border bg-panel shadow-lg">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-4">
          <div className="min-w-0">
            <h2 id="bom-distributors-title" className="text-base font-semibold text-text">
              {line.part_name} — {line.mpn ?? "No MPN"}
            </h2>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <PoweredByTrustedParts />
              {statusPills.lifecycle.map(value => (
                <RiskPill key={`lifecycle:${value}`} label="Lifecycle risk" value={value} icon={<ShieldCheck size={12} aria-hidden="true" />} />
              ))}
              {statusPills.supplyChain.map(value => (
                <RiskPill key={`supply:${value}`} label="Supply-chain risk" value={value} icon={<ShieldAlert size={12} aria-hidden="true" />} />
              ))}
              {statusPills.tariffAffected && (
                <span className="pill inline-flex items-center gap-1 bg-danger/10 text-danger" aria-label="Tariff-affected (US)">
                  <Info size={12} aria-hidden="true" />
                  Tariff-affected (US)
                </span>
              )}
            </div>
          </div>
          <button type="button" className="btn-ghost btn-sm" aria-label="Close" onClick={onClose} ref={closeButtonRef}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          <DataTable
            rows={rows}
            columns={columns}
            rowKey={row => row.id}
            tableId="project-sourcing-bom-distributors"
            exportFilename="sourced-bom-distributors"
            empty={<div className="text-muted">No distributor offers for this BOM line.</div>}
          />
        </div>

        <div className="border-t border-border px-4 py-3 text-sm text-muted">
          {stockedDistributorCount.toLocaleString()} distributor{stockedDistributorCount === 1 ? "" : "s"} with stock;{" "}
          {totalDistributorCount.toLocaleString()} total
        </div>
      </div>
    </div>
  );
}
