/**
 * Locale-pinned formatting helpers.
 * All functions use "en-US" so every operator sees identical output
 * regardless of browser locale settings.
 */

const LOCALE = "en-US";

// ---------------------------------------------------------------------
// Quantities — units-of-measure track, step 4.
//
// Migration 0074 widened every quantity column to `Numeric(18, 6)` and
// gave parts and ledger rows a unit code; step 2 made the server carry
// exact `Decimal` end to end and put quantities on the wire through
// `backend/app/domain/_quantity.py::quantity_out` (whole → JSON int,
// fractional → JSON float). Nothing accepts fractional *input* yet.
//
// What was still missing on this side is a single place that renders a
// quantity. Before this, quantities were interpolated raw — `{row.qty}`
// — which has two failure modes the moment a part is measured rather
// than counted:
//
//   1. **No unit.** A bare "12.5" is ambiguous and a bare "12" is wrong.
//   2. **Float artifacts.** JSON numbers are IEEE-754 doubles, so a
//      quantity that survived exact `Decimal` arithmetic all the way
//      through the server can still render as `0.30000000000000004`
//      once the browser adds two of them.
//
// `formatQuantity` is the one seam that fixes both. Every quantity the
// UI shows should go through it.
// ---------------------------------------------------------------------

/**
 * Storage scale of every quantity column server-side (`Numeric(18, 6)`).
 * Rendering rounds to this and no further: six decimal places is exactly
 * what the database can hold, so trimming here can never hide a digit
 * the server actually stored.
 */
const QUANTITY_SCALE_DP = 6;

/**
 * The unit code the server defaults parts and ledger rows to — the
 * mirror of `_quantity.DEFAULT_UNIT`. Suppressed on screen by default;
 * see `quantityUnitSuffix`.
 */
export const DEFAULT_QUANTITY_UNIT = "pcs";

export type FormatQuantityOptions = {
  /**
   * Render the unit even when it is the default `pcs`. Off by default —
   * see `quantityUnitSuffix` for why, and `docs/frontend/quantities.md`
   * for the one surface that turns it on.
   */
  alwaysShowUnit?: boolean;
  /** Rendered for a null / undefined / non-finite quantity. Default `""`. */
  fallback?: string;
};

/**
 * Render a quantity's *number* — no unit, no thousands separators.
 *
 * Two properties, both load-bearing:
 *
 * - **A whole quantity renders without a decimal tail.** The server
 *   stores `Numeric(18, 6)`, so twelve pieces come back as twelve, not
 *   `12.000000`; padding to the column scale would be noise on every
 *   row of every table in the app.
 * - **A fractional quantity renders exactly.** Rounding to the column's
 *   own scale first is what removes binary-float artifacts: 0.1 + 0.2
 *   is `0.30000000000000004` as a double and `0.3` here.
 *
 * This rounds to six decimal places. That is deliberately **not** an
 * integer coercion — `parseInt`, `| 0`, `~~` and `Math.floor` all
 * silently turn a 12.5 m bag into 12, which is a wrong number an
 * operator would then act on. Rounding to the scale the column can
 * hold cannot lose a stored digit.
 *
 * No grouping separators: a quantity is routinely read back against a
 * printed bag label or typed into an integer-only input, and `10,000`
 * matches neither.
 */
export function formatQuantityNumber(value: number): string {
  if (!Number.isFinite(value)) return "";
  // `toFixed` goes exponential above 1e21. `Numeric(18, 6)` tops out at
  // twelve integer digits, so that is unreachable for a stored quantity;
  // the guard just keeps the output a plain number for anything else.
  if (Math.abs(value) >= 1e21) return String(value);
  const [whole, decimals = ""] = value.toFixed(QUANTITY_SCALE_DP).split(".");
  const fraction = decimals.replace(/0+$/, "");
  // Whole at storage scale. `-0` happens when a value rounds to zero
  // from below (e.g. -1e-9); it is zero, and it should read as zero.
  if (fraction === "") return whole === "-0" ? "0" : whole;
  return `${whole}.${fraction}`;
}

/**
 * The unit text to show next to a quantity, or `""` for none.
 *
 * **`pcs` is suppressed on screen.** Discrete counts are the overwhelming
 * default — today they are the *only* case, since the unit is not yet
 * user-settable — so spelling it out would put a redundant " pcs" on
 * every quantity in every table while adding nothing an operator does
 * not already assume. Suppressing it also means a measured unit reads as
 * the exception it is: "12" is twelve of something countable, and
 * "12.5 m" is unmistakably metres.
 *
 * The exception is print. The pick list (#901) is paper carried away
 * from the screen, where the reader has no page context to infer from,
 * so it opts in via `alwaysShowUnit` and keeps showing "12 pcs".
 */
export function quantityUnitSuffix(
  unit: string | null | undefined,
  alwaysShowUnit = false,
): string {
  const trimmed = (unit ?? "").trim();
  if (!trimmed) return "";
  if (!alwaysShowUnit && trimmed.toLowerCase() === DEFAULT_QUANTITY_UNIT) return "";
  return trimmed;
}

/**
 * Render a quantity with its unit — the single helper every quantity
 * display in the app goes through.
 *
 * ```ts
 * formatQuantity(12)              // "12"
 * formatQuantity(12.5, "m")       // "12.5 m"
 * formatQuantity(12, "pcs")       // "12"      — pcs suppressed on screen
 * formatQuantity(12, "pcs", { alwaysShowUnit: true })  // "12 pcs"
 * ```
 *
 * Inside a `DataTable`, put this in the column's `render` and leave the
 * `accessor` returning the raw number: sort, search and CSV export all
 * read the accessor, so a formatted string there would sort "10 m"
 * before "9 m" and export text where a spreadsheet wants a number.
 * See `docs/frontend/quantities.md`.
 */
export function formatQuantity(
  value: number | string | null | undefined,
  unit?: string | null,
  options?: FormatQuantityOptions,
): string {
  const { alwaysShowUnit = false, fallback = "" } = options ?? {};
  if (value == null || value === "") return fallback;
  // `Number` (not `parseFloat`) so trailing garbage is rejected outright
  // rather than silently yielding a prefix of the intended quantity.
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  const rendered = formatQuantityNumber(numeric);
  const suffix = quantityUnitSuffix(unit, alwaysShowUnit);
  return suffix ? `${rendered} ${suffix}` : rendered;
}

/**
 * Render a quantity inside an English sentence — "Added 12 units",
 * "Added 12.5 m".
 *
 * Prose needs a noun where a table cell does not. When the unit is the
 * default (or unknown) the noun is the English "unit"/"units", which is
 * what the activity timeline has always said; when the part is measured,
 * the unit code replaces the noun entirely, because "12.5 metres units"
 * is not a sentence.
 */
export function formatQuantityPhrase(
  value: number | string | null | undefined,
  unit?: string | null,
): string {
  const suffix = quantityUnitSuffix(unit);
  if (suffix) return formatQuantity(value, unit);
  const rendered = formatQuantity(value);
  if (rendered === "") return "";
  return `${rendered} ${rendered === "1" ? "unit" : "units"}`;
}

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
