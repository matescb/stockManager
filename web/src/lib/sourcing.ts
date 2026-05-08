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
