/**
 * Distinguishes "provider catalog" rows (stock, pricing, lead time,
 * lifecycle, packaging, distributor P/Ns) from genuine parametric specs
 * (resistance, capacitance, tolerance, voltage…).
 *
 * Both kinds land as `custom_fields(source='provider')` today — the
 * provider lookups in `backend/app/domain/parts/providers/{mouser,
 * digikey}.py` emit them as a single `specs[]` array. Classifying by
 * key here, rather than tagging at write time, means the matcher list
 * is the only thing to update when we add a new provider field, and we
 * don't need a DB migration to re-categorise historical rows.
 *
 * Reserved keys (`image_url`, `datasheet_url`, `source_url`) are provider
 * metadata and are NOT in either tab.
 */
import { KNOWN_PROVIDER_NAMES } from "./providers";

const CATALOG_LITERAL_KEYS = new Set<string>([
  // Availability
  "In stock (qty)",
  "Lead time",
  "Lifecycle",
  "End of life",
  "Discontinued",
  "Marketplace",
  "Backorder allowed",
  // Compliance
  "RoHS",
  "REACH",
  "HTS code",
  "ECCN",
  // Distributor / packaging metadata
  "Packaging",
  "Mouser P/N",
  "DigiKey P/N",
  "Series",
]);

// Which providers can own a `"{provider}:"` namespace comes from the one
// registry in `lib/providers.ts` — the regex is built from it rather than
// spelled out again, so adding a provider needs no edit here.
const PROVIDER_NAMESPACE_RE = new RegExp(`^(${KNOWN_PROVIDER_NAMES.join("|")}):`);

// Pricing rows look like "Unit price (1+)", "Unit price (10+)", etc.
const CATALOG_REGEX_KEYS: RegExp[] = [
  /^Unit price \(\d+\+\)$/,
  // Everything a SECONDARY provider writes is namespaced, and all of it
  // is catalog data — it belongs on Sourcing, never in the user's Specs
  // tab. The backend writes these keys; see the provider_fields module.
  PROVIDER_NAMESPACE_RE,
];

/** `"mouser:Resistance"` → `"mouser"`; an un-namespaced key → null. */
export function providerNamespaceOf(key: string): string | null {
  return PROVIDER_NAMESPACE_RE.exec(key)?.[1] ?? null;
}

/** `"mouser:Resistance"` → `"Resistance"`; leaves other keys alone. */
export function stripProviderNamespace(key: string): string {
  return key.replace(PROVIDER_NAMESPACE_RE, "");
}

export const PROVIDER_RESERVED_KEYS = ["image_url", "datasheet_url", "source_url"] as const;

const RESERVED_KEYS = new Set<string>(PROVIDER_RESERVED_KEYS);

export function isReservedKey(key: string): boolean {
  return RESERVED_KEYS.has(key);
}

export function isCatalogKey(key: string): boolean {
  if (CATALOG_LITERAL_KEYS.has(key)) return true;
  return CATALOG_REGEX_KEYS.some(re => re.test(key));
}

/**
 * Spec-tab keys are "everything that isn't reserved AND isn't catalog".
 * Reserved ones don't render in either tab; catalog ones move to Sourcing.
 */
export function isSpecKey(key: string): boolean {
  return !isReservedKey(key) && !isCatalogKey(key);
}
