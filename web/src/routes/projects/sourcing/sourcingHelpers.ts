import { ApiError } from "@/lib/api";
import { lifecycleRiskTone, riskToneClass } from "@/lib/sourcing";
import type { RiskFlag, SourcingBomLine, SourcingBomOffer } from "./sourcingTypes";

export const legacyRiskFlags: RiskFlag[] = [
  "single_source",
  "no_authorized_stock",
  "moq_overbuy",
  "lead_time_long",
  "preferred_distributor_unmet",
  "tariff_affected",
];

export function numberOrNull(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatCount(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}

export function formatMoney(value: string | number | null | undefined, currency?: string | null): string {
  const numeric = numberOrNull(value);
  if (numeric == null) return "—";
  const formatted = numeric.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: numeric % 1 === 0 ? 0 : 2,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

export function offerDisplayUnitPrice(
  offer: SourcingBomOffer | null | undefined,
): string | number | null | undefined {
  if (!offer) return null;
  return offer.fx_converted === true && offer.unit_price_converted != null
    ? offer.unit_price_converted
    : offer.unit_price;
}

export function offerDisplayCurrency(offer: SourcingBomOffer | null | undefined): string | null | undefined {
  if (!offer) return null;
  return offer.currency_displayed ?? offer.currency;
}

export function formatLeadTime(days: number | null | undefined): string {
  if (days == null) return "—";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

export function riskLabel(flag: RiskFlag): string {
  switch (flag) {
    case "single_source":
      return "Single source";
    case "no_authorized_stock":
      return "No authorized stock";
    case "moq_overbuy":
      return "MOQ overbuy";
    case "lead_time_long":
      return "Long lead time";
    case "preferred_distributor_unmet":
      return "Preferred unmet";
    case "lifecycle_risk_present":
      return "lifecycle";
    case "supply_chain_risk_present":
      return "supply chain";
    case "tariff_affected":
      return "tariff";
    case "rohs_non_compliant":
      return "RoHS";
  }
}

export function riskClass(flag: RiskFlag): string {
  return flag === "rohs_non_compliant"
    ? "pill bg-danger/10 text-danger"
    : "pill bg-warning/10 text-warning";
}

export function riskTooltip(flag: RiskFlag): string | undefined {
  switch (flag) {
    case "lifecycle_risk_present":
      return "TrustedParts returned lifecycle risk text for this BOM line.";
    case "supply_chain_risk_present":
      return "TrustedParts returned supply-chain risk text for this BOM line.";
    case "tariff_affected":
      return "TrustedParts distributors indicated this BOM line may be affected by United States tariffs.";
    case "rohs_non_compliant":
      return "TrustedParts did not find a compliant RoHS region for this BOM line.";
    default:
      return undefined;
  }
}

export function lifecycleRiskClass(value: string): string {
  return riskToneClass(lifecycleRiskTone(value));
}

export function rohsTone(row: SourcingBomLine): "good" | "danger" | "neutral" {
  if (row.risk_flags.includes("rohs_non_compliant")) return "danger";
  const euEntries = row.offers
    .flatMap(offer => offer.rohs_compliance ?? [])
    .filter(item => item.region.trim().toLowerCase() === "eu");
  if (euEntries.length === 0) return "neutral";
  if (euEntries.some(item => item.is_compliant === false)) return "danger";
  return "good";
}

export function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

export function sourcingErrorToastMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "rate_limited":
      case "provider_rate_limited":
        return "Rate limit hit — wait a minute before sourcing again.";
      case "currency_mismatch":
        return "Sourcing returned mixed currencies. Check workspace currency settings.";
      case "plan_stale":
        return "Prices are stale. Refresh prices before continuing.";
      default:
        break;
    }
    if (error.status === 429) return "Rate limit hit — wait a minute before sourcing again.";
    return error.userMessage;
  }
  return "Failed to source BOM. Try again.";
}

export function defaultFromActiveList(saved: string | null | undefined, active: string[]): string {
  if (saved && active.includes(saved)) return saved;
  return active[0] ?? "";
}

export function distributorsFromActiveList(saved: string[] | null | undefined, active: string[]): string[] {
  if (!saved || saved.length === 0) return [];
  const intersection = saved.filter(item => active.includes(item));
  if (intersection.length > 0) return intersection;
  return active[0] ? [active[0]] : [];
}
