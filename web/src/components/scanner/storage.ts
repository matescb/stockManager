export const SCANNER_DEVICE_PREF_SUFFIX = "scanner.deviceId";

export function scannerDevicePreferenceKey(
  workspaceId: string | null | undefined,
): string {
  return `ws:${workspaceId ?? "none"}:${SCANNER_DEVICE_PREF_SUFFIX}`;
}
