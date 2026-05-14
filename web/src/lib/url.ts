const SAME_ORIGIN_BASE = "https://stockmanager.local";

function hasDisallowedUrlChars(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 0x20 || code === 0x7f) return true;
  }
  return false;
}

export function isSafeHttpUrl(value: string | null | undefined): value is string {
  if (typeof value !== "string" || value === "" || hasDisallowedUrlChars(value)) {
    return false;
  }

  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export function isSafeSameOriginPath(value: string | null | undefined): value is string {
  if (
    typeof value !== "string" ||
    value === "" ||
    hasDisallowedUrlChars(value) ||
    !value.startsWith("/") ||
    value.startsWith("//")
  ) {
    return false;
  }

  try {
    const url = new URL(value, SAME_ORIGIN_BASE);
    return url.origin === SAME_ORIGIN_BASE;
  } catch {
    return false;
  }
}

export function isSafeHttpOrSameOriginUrl(value: string | null | undefined): value is string {
  return isSafeHttpUrl(value) || isSafeSameOriginPath(value);
}
