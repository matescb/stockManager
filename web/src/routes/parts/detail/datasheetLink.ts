import { isSafeHttpUrl, isSafeSameOriginPath } from "@/lib/url";
import type { Attachment } from "@/types";

const ASSET_PREFIX = "/api/parts/assets/";

/**
 * Append `?name=<safe>` to an asset URL so the backend serves it with
 * `Content-Disposition: …; filename="<safe>.<ext>"`, and the user's Save As
 * dialog shows a readable filename instead of the content hash.
 *
 * Only applies to our own asset paths; remote URLs are returned as-is.
 */
export function withDownloadName(url: string, name: string | null | undefined): string {
  if (!url || !url.startsWith(ASSET_PREFIX)) return url;
  const safe = (name || "datasheet").replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 80);
  if (!safe) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}name=${encodeURIComponent(safe)}`;
}

export type DatasheetLink =
  | {
      kind: "local";
      /** Same-origin URL of the stored copy. */
      href: string;
      fileName: string | null;
      sizeBytes: number | null;
      /** Upstream URL the copy was fetched from, when it is a safe http(s) URL. */
      sourceUrl: string | null;
    }
  | { kind: "external"; href: string };

/**
 * Decide where the part's "Datasheet" link points.
 *
 * The datasheet fetcher (`backend/app/domain/parts/services/datasheets.py`)
 * never rewrites the `datasheet_url` custom field — it stays the upstream
 * URL — and registers the stored PDF as a part attachment with
 * `file_type='datasheet'`. So a local copy exists iff such an attachment
 * does. The attachments list is newest-first, so the first match wins.
 *
 * Fallbacks, in order: a `datasheet_url` that already is a same-origin path
 * (legacy rows localised to `/api/parts/assets/…`), then the upstream URL, then nothing.
 */
export function resolveDatasheetLink(
  attachments: readonly Attachment[] | undefined,
  datasheetUrl: string | null | undefined,
  downloadName: string | null | undefined,
): DatasheetLink | null {
  const upstream = isSafeHttpUrl(datasheetUrl) ? datasheetUrl : null;
  const stored = attachments?.find(a => a.file_type === "datasheet");
  if (stored) {
    const href = `/api/attachments/${encodeURIComponent(stored.id)}/download`;
    if (isSafeSameOriginPath(href)) {
      return {
        kind: "local",
        href,
        fileName: stored.file_name || null,
        sizeBytes: stored.size_bytes ?? null,
        sourceUrl: upstream,
      };
    }
  }
  if (isSafeSameOriginPath(datasheetUrl)) {
    return {
      kind: "local",
      href: withDownloadName(datasheetUrl, downloadName),
      fileName: null,
      sizeBytes: null,
      sourceUrl: null,
    };
  }
  if (upstream) return { kind: "external", href: upstream };
  return null;
}
