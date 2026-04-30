/**
 * Parser for the 2D Data Matrix codes Mouser and DigiKey print on
 * component bags. They follow MIL-STD-130N / ANSI MH10.8.2 — a header
 * `[)>` plus separator-delimited records, each prefixed with a Data
 * Identifier code:
 *
 *   1P   manufacturer part number   (the field we actually want)
 *   30P  alternate manufacturer P/N (DigiKey)
 *   P    distributor part number
 *   Q    quantity
 *   1K   purchase order number
 *   K    customer reference
 *   10D  date code (YYWW)
 *   1T   lot/batch
 *   1S   serial number
 *   13Z  arbitrary
 *
 * **Why this is harder than it looks.** The format spec uses ASCII
 * control characters as field separators (RS=0x1e, GS=0x1d, EOT=0x04,
 * FS=0x1c). Real-world scanners are wildly inconsistent about preserving
 * those: some pass them through verbatim (best case), some replace them
 * with printable substitutes like `#` or `]`, some omit them entirely
 * leaving fields concatenated.
 *
 * The parser tries three passes, in order:
 *  1. Split on real or substitute separators.
 *  2. If that yields a single chunk, fall back to **inline DI scanning**
 *     — locate known DI prefixes by regex and slice between them.
 *  3. If neither pass found a Data Identifier and the input has no `[)>`
 *     header, treat the whole string as a plain MPN (1D-barcode case).
 */

export type BagCode = {
  mpn: string;
  quantity?: number;
  distributorPn?: string;
  /** Manufacturer name as printed on the bag (1V). Useful as a sanity
   *  check against the provider's `manufacturer` field. */
  manufacturer?: string;
  /** Date code (10D or 9D), typically YYWW. Stored verbatim — Mouser
   *  and DigiKey both use the YYWW form. */
  dateCode?: string;
  /** Manufacturer's lot/batch identifier (1T). */
  lotBatch?: string;
  /** Serial number (1S) — only present on serialized parts. */
  serial?: string;
  /** Order / PO / line / invoice references — handy for traceability.
   *  Mouser populates K (customer ref / web order) + 14K (line item) +
   *  11K (invoice). DigiKey uses 1K (PO) + similar variants. */
  customerRef?: string;
  poNumber?: string;
  lineItem?: string;
  invoiceRef?: string;
  raw: string;
};

/**
 * Synthesise a human-readable lot name from whatever traceability
 * fields the bag carried. Returns null when neither lot nor date code
 * is present (no lot row should be created in that case).
 */
export function bagLotName(b: BagCode): string | null {
  const parts: string[] = [];
  if (b.lotBatch) parts.push(`Lot ${b.lotBatch}`);
  if (b.dateCode) parts.push(`DC ${b.dateCode}`);
  return parts.length ? parts.join(" · ") : null;
}

/**
 * Synthesise a stock-entry comment that pins this batch to the actual
 * purchase. The order references are what makes "trace this part back
 * to its source PO" possible months later.
 */
export function bagComments(b: BagCode): string | null {
  const parts: string[] = [];
  if (b.customerRef) parts.push(`Order ${b.customerRef}`);
  if (b.poNumber) parts.push(`PO ${b.poNumber}`);
  if (b.lineItem) parts.push(`line ${b.lineItem}`);
  if (b.invoiceRef) parts.push(`invoice ${b.invoiceRef}`);
  return parts.length ? parts.join(" · ") : null;
}

// Separator characters we'll split on. Includes:
//   \x1c FS   \x1d GS   \x1e RS   \x1f US   \x04 EOT
// plus the literal text placeholders some libraries write ("{GS}",
// "<FNC1>"). We deliberately do NOT include printable chars like "#",
// "]" or "^" — those occur as legitimate content in distributor codes
// (Mouser's customer-reference field commonly contains "#", e.g.
// "K#44861 A #44920"). When a scanner munges the real separators, the
// inline-DI fallback below recovers the fields without false splits.
const SEPARATOR_RE = /\{GS\}|<FNC1>|[\x1c\x1d\x1e\x1f\x04]/g;

/**
 * Some decoders (notably ZXing-C++ in its default HRI text mode) emit the
 * printable "Control Pictures" Unicode block (U+2400..U+243F) instead of
 * the raw ASCII control characters they represent. So a real GS (0x1D)
 * comes through as U+241D ("Symbol for GS") which renders as `␝` and is
 * not a control character at all — our SEPARATOR_RE doesn't see it, the
 * field separators land inside the "value" half of each DI, and we end
 * up sending an MPN like `98266-0897␝` to the provider lookup.
 *
 * Map the relevant pictograms back to their ASCII counterparts before any
 * other parsing step runs so the rest of the pipeline works uniformly,
 * regardless of which decoder produced the input.
 */
function normalizeControlPictures(s: string): string {
  return s
    .replace(/␄/g, "\x04")  // Symbol for EOT   → EOT
    .replace(/␜/g, "\x1c")  // Symbol for FS    → FS
    .replace(/␝/g, "\x1d")  // Symbol for GS    → GS
    .replace(/␞/g, "\x1e")  // Symbol for RS    → RS
    .replace(/␟/g, "\x1f")  // Symbol for US    → US
    // ZXing also substitutes printable ASCII space (0x20) with U+2420
    // ("Symbol for Space"). Without this, parsed values keep the
    // pictograph — e.g. customerRef shows up as "#44861␠A␠#44920".
    .replace(/␠/g, " ");
}

// Recognized Data Identifiers. Order matters for inlineSplit (longest
// prefix wins so "1P" is preferred over "P", "30P" over "P", etc.).
const DI_LIST = [
  "30P", "11K", "14K", "1P", "1K", "10D", "13Z", "1T", "1S", "1V",
  "9D", "4L", "P", "K", "Q", "T",
];

function findDI(field: string): { di: string; value: string } | null {
  const f = field.trim();
  for (const di of DI_LIST) {
    if (f.startsWith(di)) {
      const value = f.slice(di.length).trim();
      if (value) return { di, value };
    }
  }
  return null;
}

/**
 * When separators are missing entirely, recover field boundaries by
 * regex-finding known DI prefixes. Anchors at start of string or after
 * a non-alphanumeric character to avoid false matches inside an MPN.
 */
function inlineSplit(s: string): string[] {
  const alt = DI_LIST.join("|");
  const re = new RegExp(`(?:^|(?<=[^A-Z0-9]))(?:${alt})`, "g");
  const starts: number[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) starts.push(m.index);
  if (starts.length === 0) return [s];
  const out: string[] = [];
  for (let i = 0; i < starts.length; i++) {
    const a = starts[i];
    const b = i + 1 < starts.length ? starts[i + 1] : s.length;
    const piece = s.slice(a, b).trim();
    if (piece) out.push(piece);
  }
  return out;
}

function stripHeader(s: string): string {
  // Standard form is "[)>" + RS + "06" + GS, but scanners sometimes drop
  // the control chars; tolerate both. Also tolerate a trailing separator
  // immediately after "06".
  return s
    .replace(/^\[\)>[\x1e]?06[\x1d]?/, "")
    .replace(/^\[\)>06/, "");
}

/**
 * Stable hash of the raw bag code, used to recognise the same physical
 * bag when it's scanned twice. Normalises pictograms first so a code
 * decoded by ZXing (with its U+241D/U+241E/U+2420 substitutions) hashes
 * identically to the same code decoded by Scandit (with raw 0x1d/0x1e/0x20).
 *
 * Returns null when the input has no scannable content — the caller
 * shouldn't dedup on an empty signature (that would block every restock).
 */
export async function bagSignature(raw: string): Promise<string | null> {
  const normalised = normalizeControlPictures((raw ?? "").trim());
  if (!normalised) return null;
  // Web Crypto isn't on every legacy stack; degrade silently.
  if (!globalThis.crypto?.subtle) return null;
  const bytes = new TextEncoder().encode(normalised);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
}

export function parseBagCode(raw: string): BagCode {
  const input = normalizeControlPictures((raw ?? "").trim());
  if (!input) return { mpn: "", raw };

  const stripped = stripHeader(input);

  // Pass 1 — split on separators.
  let chunks = stripped.split(SEPARATOR_RE).map(c => c.trim()).filter(Boolean);

  // Pass 2 — if pass 1 didn't produce multiple fields, try inline DI scan.
  if (chunks.length <= 1) {
    chunks = inlineSplit(stripped);
  }

  // Pass 3 — if no DI was ever found and there's no header marker, the
  // input is almost certainly just the MPN itself (plain 1D barcode).
  const anyDi = chunks.some(c => findDI(c) !== null);
  if (!anyDi && !input.startsWith("[)>")) {
    return { mpn: input, raw };
  }

  const out: BagCode = { mpn: "", raw };

  for (const chunk of chunks) {
    const f = findDI(chunk);
    if (!f) continue;
    switch (f.di) {
      case "1P":
        out.mpn = f.value;
        break;
      case "30P":
      case "P":
        if (!out.distributorPn) out.distributorPn = f.value;
        break;
      case "Q": {
        const n = parseInt(f.value, 10);
        if (Number.isFinite(n) && n > 0) out.quantity = n;
        break;
      }
      case "1V":
        out.manufacturer = f.value;
        break;
      case "10D":
      case "9D":
        // Prefer 10D (per ANSI MH10.8.2) but accept 9D as fallback.
        if (!out.dateCode) out.dateCode = f.value;
        break;
      case "1T":
        out.lotBatch = f.value;
        break;
      case "1S":
        out.serial = f.value;
        break;
      case "K":
        out.customerRef = f.value;
        break;
      case "1K":
        out.poNumber = f.value;
        break;
      case "14K":
        out.lineItem = f.value;
        break;
      case "11K":
        out.invoiceRef = f.value;
        break;
      // 4L (country of origin), 13Z (arbitrary), T are intentionally
      // not surfaced — niche enough to keep out of the UI.
    }
  }

  // No 1P found in any field but a header was present → fall back to the
  // raw input rather than silently dropping the scan. The provider lookup
  // will most likely return "no match" and the operator can correct.
  if (!out.mpn) out.mpn = input;

  return out;
}
