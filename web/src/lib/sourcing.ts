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

export function lifecycleRiskTone(value: string | null | undefined): string {
  const normalized = value?.trim().toLowerCase() ?? "";
  if (normalized.startsWith("active")) return "bg-success/10 text-success";
  if (normalized.includes("nrnd") || normalized.includes("not recommended")) {
    return "bg-warning/10 text-warning";
  }
  if (
    normalized.includes("obsolete") ||
    normalized.includes("eol") ||
    normalized.includes("end of life") ||
    normalized.includes("last time buy") ||
    normalized.includes("ltb")
  ) {
    return "bg-danger/10 text-danger";
  }
  // SX-1: TPS-7 lifecycle vocabulary takes precedence over generic TrustedParts risk levels.
  if (normalized.includes("low")) return "bg-success/10 text-success";
  if (normalized.includes("medium") || normalized.includes("moderate")) return "bg-warning/10 text-warning";
  if (normalized.includes("high") || normalized.includes("severe")) return "bg-danger/10 text-danger";
  return "bg-panel2 text-muted";
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
