/**
 * Locale-pinned formatting helpers.
 * All functions use "en-US" so every operator sees identical output
 * regardless of browser locale settings.
 */

const LOCALE = "en-US";

/** Format an ISO date/datetime string as YYYY-MM-DD (en-CA yields that shape). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-CA").format(d); // → YYYY-MM-DD
}

/** Format an ISO datetime string as date + 24 h time, pinned to en-US. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(LOCALE, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

/**
 * Format a currency value.
 * Uses Intl.NumberFormat so JPY auto-renders with 0 decimals, CHF/EUR/USD
 * with 2.  Falls back to `value.toFixed(2) + " " + currency` when the
 * currency code is unknown/empty or the Intl constructor throws.
 */
export function formatMoney(
  value: number,
  currency: string | null | undefined,
): string {
  if (!currency) return value.toFixed(2);
  try {
    return new Intl.NumberFormat(LOCALE, {
      style: "currency",
      currency,
    }).format(value);
  } catch {
    // RangeError for invalid ISO 4217 codes
    return `${value.toFixed(2)} ${currency}`;
  }
}
