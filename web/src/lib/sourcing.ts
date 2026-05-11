export type SourcingPriceBreak = {
  quantity: number;
  unit_price: number;
};

export type BestUnitPrice = {
  unitPrice: number;
  breakQty: number;
};

export function bestUnitPriceAtQty(
  priceBreaks: readonly SourcingPriceBreak[],
  qty: number,
): BestUnitPrice | null {
  if (!Number.isFinite(qty) || qty < 1) return null;
  const targetQty = Math.floor(qty);

  let best: BestUnitPrice | null = null;
  for (const priceBreak of priceBreaks) {
    const breakQty = Math.floor(priceBreak.quantity);
    if (
      Number.isFinite(breakQty) &&
      Number.isFinite(priceBreak.unit_price) &&
      breakQty >= 1 &&
      breakQty <= targetQty &&
      (best === null || breakQty > best.breakQty)
    ) {
      best = { unitPrice: priceBreak.unit_price, breakQty };
    }
  }
  return best;
}

export function extendedPrice(
  priceBreaks: readonly SourcingPriceBreak[],
  qty: number,
): number | null {
  const best = bestUnitPriceAtQty(priceBreaks, qty);
  if (best === null) return null;
  return best.unitPrice * Math.floor(qty);
}

export type RiskTone = "good" | "low-warning" | "warning" | "danger" | "neutral";

export const RISK_TONE_CLASSES: Record<RiskTone, string> = {
  good: "bg-success/10 text-success",
  "low-warning": "bg-success/30 text-success",
  warning: "bg-warning/10 text-warning",
  danger: "bg-danger/10 text-danger",
  neutral: "bg-panel2 text-muted",
};

export function riskToneClass(tone: RiskTone): string {
  return RISK_TONE_CLASSES[tone];
}

export function lifecycleRiskTone(value: string | null | undefined): RiskTone {
  const normalized = value?.trim().toLowerCase() ?? "";
  // First match wins: TPS-7 lifecycle keywords outrank generic TrustedParts risk levels,
  // then Low-Med canonical labels, Low/Med/High labels, and finally descriptive fallbacks.
  if (normalized.startsWith("active")) return "good";
  if (normalized.includes("nrnd") || normalized.includes("not recommended")) {
    return "warning";
  }
  if (
    normalized.includes("obsolete") ||
    normalized.includes("eol") ||
    normalized.includes("end of life") ||
    normalized.includes("last time buy") ||
    normalized.includes("ltb")
  ) {
    return "danger";
  }
  if (normalized.includes("low-med") || normalized.includes("low/med")) return "low-warning";
  if (normalized.includes("low")) return "good";
  if (normalized.includes("medium") || /\bmed\b/.test(normalized) || normalized.includes("moderate")) return "warning";
  if (normalized.includes("high") || normalized.includes("severe")) return "danger";
  if (
    normalized.includes("may be special order") ||
    normalized.includes("limited stock") ||
    normalized.includes("long lead times")
  ) {
    return "low-warning";
  }
  return "neutral";
}

export function lifecycleRiskRank(value: string | null | undefined): number {
  const normalized = value?.trim().toLowerCase() ?? "";
  if (!normalized) return 3;
  if (
    normalized.includes("obsolete") ||
    normalized.includes("eol") ||
    normalized.includes("end of life") ||
    normalized.includes("last time buy") ||
    normalized.includes("ltb")
  ) {
    return 0;
  }
  if (normalized.includes("nrnd") || normalized.includes("not recommended")) return 1;
  if (normalized.startsWith("active")) return 2;
  return 2.5;
}
