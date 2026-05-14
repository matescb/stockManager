import { ApiError } from "@/lib/api";
import type {
  PurchasePlan,
  PurchasePlanLine,
  PurchasePlanOffer,
  PurchasePlanOrderOverride,
} from "./purchasePlanTypes";

export const STALE_MS = 10 * 60 * 1000;

export function numberOrNull(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatMoney(value: string | number | null | undefined, currency?: string | null): string {
  const numeric = numberOrNull(value);
  if (numeric == null) return "-";
  const formatted = numeric.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: numeric % 1 === 0 ? 0 : 2,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

export function formatLeadTime(days: number | null | undefined): string {
  return days == null ? "-" : days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

export function refreshedLabel(plan: PurchasePlan): string {
  if (!plan.last_refreshed_at) return "Not refreshed yet";
  const ageMs = Date.now() - new Date(plan.last_refreshed_at).getTime();
  const minutes = Math.max(0, Math.floor(ageMs / 60000));
  return `Refreshed ${minutes} min ago`;
}

export function isRefreshFresh(plan: PurchasePlan): boolean {
  return !!plan.last_refreshed_at && Date.now() - new Date(plan.last_refreshed_at).getTime() <= STALE_MS;
}

export function extendedCost(line: PurchasePlanLine): number | null {
  const unit = numberOrNull(line.selected_unit_price);
  if (unit == null || line.selected_qty == null) return null;
  return unit * line.selected_qty;
}

export function selectedQtyForOffer(line: PurchasePlanLine, offer: PurchasePlanOffer): number {
  return Math.max(Math.max(0, line.shortage_qty), Math.max(0, numberOrNull(offer.moq) ?? 0), 1);
}

export function unitPriceForOffer(offer: PurchasePlanOffer, qty: number): string | number | null {
  const breaks = (offer.price_breaks ?? [])
    .map(priceBreak => ({
      quantity: numberOrNull(priceBreak.quantity),
      unitPrice: priceBreak.unit_price,
    }))
    .filter((priceBreak): priceBreak is { quantity: number; unitPrice: string | number } =>
      priceBreak.quantity != null &&
      priceBreak.quantity >= 1 &&
      priceBreak.unitPrice != null &&
      priceBreak.unitPrice !== "",
    )
    .sort((a, b) => a.quantity - b.quantity);
  if (breaks.length === 0) return offer.unit_price ?? null;
  let selected = breaks[0];
  for (const candidate of breaks) {
    if (candidate.quantity > qty) break;
    selected = candidate;
  }
  return selected.unitPrice;
}

export function purchasePlanOverrideMatchesOffer(
  line: PurchasePlanLine,
  override: PurchasePlanOrderOverride,
  offer: PurchasePlanOffer,
): boolean {
  if ((offer.distributor ?? "").toLowerCase() !== override.selected_distributor.toLowerCase()) return false;
  const stock = numberOrNull(offer.stock);
  if (stock == null || stock < override.selected_qty) return false;
  const moq = numberOrNull(offer.moq);
  if (moq != null && override.selected_qty < moq) return false;
  if (override.selected_qty < line.shortage_qty) return false;
  if ((offer.currency ?? "").toUpperCase() !== override.selected_currency.toUpperCase()) return false;
  const unitPrice = unitPriceForOffer(offer, override.selected_qty);
  return unitPrice != null && numberOrNull(unitPrice) === numberOrNull(override.selected_unit_price);
}

export function recomputePlanFromLines(plan: PurchasePlan, lines: PurchasePlanLine[]): PurchasePlan {
  const distributors = new Set<string>();
  let estTotal = 0;
  let hasCost = false;
  let worstLeadTime: number | null = null;
  let unfilledCount = 0;
  for (const line of lines) {
    if (line.selected_distributor) {
      distributors.add(line.selected_distributor);
    } else {
      unfilledCount += 1;
    }
    const cost = extendedCost(line);
    if (cost != null) {
      estTotal += cost;
      hasCost = true;
    }
    if (line.selected_lead_time_days != null) {
      worstLeadTime = Math.max(worstLeadTime ?? 0, line.selected_lead_time_days);
    }
  }
  return {
    ...plan,
    lines,
    distributors_used: [...distributors].sort((a, b) => a.localeCompare(b)),
    est_total_cost: hasCost ? estTotal : plan.est_total_cost,
    worst_lead_time_days: worstLeadTime,
    unfilled_count: unfilledCount,
  };
}

export function groupLines(lines: PurchasePlanLine[]) {
  const groups = new Map<string, PurchasePlanLine[]>();
  for (const line of lines) {
    if (!line.selected_distributor) continue;
    const current = groups.get(line.selected_distributor) ?? [];
    current.push(line);
    groups.set(line.selected_distributor, current);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export function summaryCurrency(plan: PurchasePlan): string | null {
  return plan.lines.find(line => line.selected_currency)?.selected_currency ?? plan.currency_code ?? null;
}

export function purchasePlanActionErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  switch (error.code) {
    case "sourcing.plan_stale":
      return "Prices are stale. Refresh prices before creating draft orders.";
    case "sourcing.currency_mismatch":
      return "Sourcing returned mixed currencies. Check workspace currency settings.";
    case "rate_limited":
    case "sourcing.provider_rate_limited":
      return "Rate limit hit — wait a minute before trying again.";
    default:
      return error.userMessage || fallback;
  }
}
