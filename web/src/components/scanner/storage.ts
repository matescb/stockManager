export const SCANNER_DEVICE_PREF_SUFFIX = "scanner.deviceId";
export const LEGACY_SCANNER_DEVICE_PREF_KEY = SCANNER_DEVICE_PREF_SUFFIX;

export function scannerDevicePreferenceKey(
  workspaceId: string | null | undefined,
): string {
  return `ws:${workspaceId ?? "none"}:${SCANNER_DEVICE_PREF_SUFFIX}`;
}

export function readScannerDevicePreference(
  workspaceId: string | null | undefined,
): string | undefined {
  const key = scannerDevicePreferenceKey(workspaceId);
  const current = localStorage.getItem(key) || undefined;
  if (!workspaceId) {
    return current;
  }

  const legacy = localStorage.getItem(LEGACY_SCANNER_DEVICE_PREF_KEY);
  if (legacy === null) {
    return current;
  }

  if (!current && legacy) {
    localStorage.setItem(key, legacy);
  }
  localStorage.removeItem(LEGACY_SCANNER_DEVICE_PREF_KEY);
  return current || legacy || undefined;
}
