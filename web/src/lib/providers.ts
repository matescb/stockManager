/**
 * The single frontend registry of parts providers.
 *
 * Everything the UI needs to know about a provider — its display name,
 * whether its credentials are a key/secret pair, where to search it by
 * MPN, and the `"{provider}:"` custom-field namespace it owns — is
 * derived from `PROVIDERS` below. Adding a provider is one entry here
 * plus the backend list; see
 * [ADR-0031](../../../docs/adr/0031-primary-and-secondary-parts-providers.md).
 *
 * Mirrors `backend/app/domain/parts/provider_fields.py::KNOWN_PROVIDER_NAMES`
 * and the `Literal`s in `backend/app/domain/workspaces/schemas.py`.
 */

export type ProviderDefinition = {
  name: string;
  label: string;
  /** DigiKey authenticates with client_id + client_secret; Mouser with one key. */
  needsSecret: boolean;
  searchUrl: (mpn: string) => string;
};

export const PROVIDERS: readonly ProviderDefinition[] = [
  {
    name: "mouser",
    label: "Mouser",
    needsSecret: false,
    searchUrl: mpn => `https://www.mouser.com/c/?q=${encodeURIComponent(mpn)}`,
  },
  {
    name: "digikey",
    label: "DigiKey",
    needsSecret: true,
    searchUrl: mpn =>
      `https://www.digikey.com/en/products/result?keywords=${encodeURIComponent(mpn)}`,
  },
] as const;

export const KNOWN_PROVIDER_NAMES: readonly string[] = PROVIDERS.map(p => p.name);

const BY_NAME = new Map(PROVIDERS.map(p => [p.name, p]));

/** Display name, falling back to the raw value for anything unrecognised. */
export function providerLabel(name: string | null | undefined): string {
  if (!name) return "";
  return BY_NAME.get(name)?.label ?? name;
}

/** True when this provider needs a second credential (a client secret). */
export function providerNeedsSecret(name: string): boolean {
  return BY_NAME.get(name)?.needsSecret ?? false;
}

/** The provider's own search page for an MPN, or null if we can't build one. */
export function providerSearchUrl(
  name: string | null | undefined,
  mpn: string | null | undefined,
): string | null {
  if (!name || !mpn) return null;
  return BY_NAME.get(name)?.searchUrl(mpn) ?? null;
}
