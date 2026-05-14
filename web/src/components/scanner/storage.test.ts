import { describe, expect, it } from "vitest";
import { scannerDevicePreferenceKey } from "./storage";

describe("scanner device preference storage", () => {
  it("scopes the remembered camera device to the workspace", () => {
    expect(scannerDevicePreferenceKey("ws-a")).toBe("ws:ws-a:scanner.deviceId");
    expect(scannerDevicePreferenceKey("ws-b")).toBe("ws:ws-b:scanner.deviceId");
  });

  it("uses a non-tenant fallback before workspace bootstrap", () => {
    expect(scannerDevicePreferenceKey(null)).toBe("ws:none:scanner.deviceId");
    expect(scannerDevicePreferenceKey(undefined)).toBe("ws:none:scanner.deviceId");
  });
});
