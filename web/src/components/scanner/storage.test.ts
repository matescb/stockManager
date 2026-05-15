import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  LEGACY_SCANNER_DEVICE_PREF_KEY,
  readScannerDevicePreference,
  scannerDevicePreferenceKey,
} from "./storage";

describe("scanner device preference storage", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    });
  });

  it("scopes the remembered camera device to the workspace", () => {
    expect(scannerDevicePreferenceKey("ws-a")).toBe("ws:ws-a:scanner.deviceId");
    expect(scannerDevicePreferenceKey("ws-b")).toBe("ws:ws-b:scanner.deviceId");
  });

  it("uses a non-tenant fallback before workspace bootstrap", () => {
    expect(scannerDevicePreferenceKey(null)).toBe("ws:none:scanner.deviceId");
    expect(scannerDevicePreferenceKey(undefined)).toBe("ws:none:scanner.deviceId");
  });

  it("migrates the stale global key on first workspace-scoped read", () => {
    localStorage.clear();
    localStorage.setItem(LEGACY_SCANNER_DEVICE_PREF_KEY, "camera-legacy");

    expect(readScannerDevicePreference("ws-a")).toBe("camera-legacy");
    expect(localStorage.getItem("ws:ws-a:scanner.deviceId")).toBe("camera-legacy");
    expect(localStorage.getItem(LEGACY_SCANNER_DEVICE_PREF_KEY)).toBeNull();
  });

  it("keeps an existing workspace value while sweeping the stale global key", () => {
    localStorage.clear();
    localStorage.setItem(LEGACY_SCANNER_DEVICE_PREF_KEY, "camera-legacy");
    localStorage.setItem("ws:ws-a:scanner.deviceId", "camera-workspace");

    expect(readScannerDevicePreference("ws-a")).toBe("camera-workspace");
    expect(localStorage.getItem("ws:ws-a:scanner.deviceId")).toBe("camera-workspace");
    expect(localStorage.getItem(LEGACY_SCANNER_DEVICE_PREF_KEY)).toBeNull();
  });
});
