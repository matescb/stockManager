import { describe, expect, it } from "vitest";
import {
  isCatalogKey,
  isReservedKey,
  isSpecKey,
  providerNamespaceOf,
  stripProviderNamespace,
  PROVIDER_RESERVED_KEYS,
} from "../providerCatalog";
import { PROVIDERS, providerLabel, providerNeedsSecret } from "../providers";

describe("providerCatalog reserved keys", () => {
  it("matches backend provider-reserved custom field keys", () => {
    expect(PROVIDER_RESERVED_KEYS).toEqual(["image_url", "datasheet_url", "source_url"]);
  });

  it("keeps provider metadata out of specs", () => {
    for (const key of PROVIDER_RESERVED_KEYS) {
      expect(isReservedKey(key)).toBe(true);
      expect(isSpecKey(key)).toBe(false);
    }
  });
});

describe("provider namespaces derive from the registry", () => {
  it("recognises every registered provider without a second list", () => {
    for (const { name } of PROVIDERS) {
      const key = `${name}:Lead time`;
      expect(providerNamespaceOf(key)).toBe(name);
      expect(stripProviderNamespace(key)).toBe("Lead time");
      // Namespaced rows are catalog data by definition — Sourcing tab,
      // never the user's Specs tab.
      expect(isCatalogKey(key)).toBe(true);
      expect(isSpecKey(key)).toBe(false);
    }
  });

  it("does not treat an ordinary colon as a namespace", () => {
    // Mirrors the backend pin: only a KNOWN_PROVIDER_NAMES prefix counts,
    // so a genuine upstream spec keeps belonging to the primary.
    expect(providerNamespaceOf("Vref:max")).toBeNull();
    expect(stripProviderNamespace("Vref:max")).toBe("Vref:max");
    expect(isSpecKey("Vref:max")).toBe(true);
  });

  it("is the single source for provider display metadata", () => {
    expect(PROVIDERS.map(p => p.name)).toEqual(["mouser", "digikey"]);
    expect(providerLabel("mouser")).toBe("Mouser");
    expect(providerLabel("digikey")).toBe("DigiKey");
    // Unknown names fall back to the raw value rather than blanking out.
    expect(providerLabel("octopart")).toBe("octopart");
    expect(providerNeedsSecret("digikey")).toBe(true);
    expect(providerNeedsSecret("mouser")).toBe(false);
  });
});
