import { describe, expect, it } from "vitest";
import { isReservedKey, isSpecKey, PROVIDER_RESERVED_KEYS } from "../providerCatalog";

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
