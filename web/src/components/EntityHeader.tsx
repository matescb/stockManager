import { ReactNode, useCallback, useState } from "react";
import ImageLightbox from "@/components/ImageLightbox";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";

export type Stat = { label: string; value: ReactNode; tone?: "default" | "danger" | "warning" | "success" };

type Props = {
  title: ReactNode;
  subtitle?: ReactNode;
  idCode?: string;
  actions?: ReactNode;
  /** Optional path-style breadcrumb rendered above the title. */
  breadcrumb?: ReactNode;
  /** Optional KPI strip rendered along the bottom of the header card. */
  stats?: Stat[];
  /**
   * Optional thumbnail rendered to the left of the title. Used by Part
   * detail to surface the provider image in the header rather than in
   * a footer Media card. Clicking it opens the full-size image in an
   * in-page `ImageLightbox`.
   */
  imageUrl?: string | null;
  /** Caption / accessible name for the lightbox; defaults to "Image". */
  imageTitle?: string;
};

const TONE_CLS: Record<NonNullable<Stat["tone"]>, string> = {
  default: "text-text",
  danger:  "text-danger",
  warning: "text-warning",
  success: "text-success",
};

export default function EntityHeader({
  title,
  subtitle,
  idCode,
  actions,
  breadcrumb,
  stats,
  imageUrl,
  imageTitle,
}: Props) {
  const safeImageUrl = isSafeHttpOrSameOriginUrl(imageUrl) ? imageUrl : null;
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const closeLightbox = useCallback(() => setLightboxOpen(false), []);

  return (
    <div className="card p-4 mb-4">
      {breadcrumb && <div className="text-xs text-muted mb-2">{breadcrumb}</div>}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3 min-w-0">
          {safeImageUrl && (
            <>
              <button
                type="button"
                onClick={() => setLightboxOpen(true)}
                className="shrink-0 block cursor-zoom-in rounded focus:outline-none focus:ring-2 focus:ring-accent/40"
                aria-label="View full image"
                aria-haspopup="dialog"
              >
                <img
                  src={safeImageUrl}
                  alt=""
                  className="h-14 w-14 object-contain rounded bg-panel"
                />
              </button>
              <ImageLightbox
                open={lightboxOpen}
                onClose={closeLightbox}
                src={safeImageUrl}
                title={imageTitle ?? "Image"}
              />
            </>
          )}
          <div className="min-w-0">
            <div className="text-lg font-semibold truncate">{title}</div>
            {subtitle && <div className="text-sm text-muted mt-0.5">{subtitle}</div>}
            {idCode && (
              <div className="mt-2 inline-block font-mono text-xs px-2 py-0.5 rounded bg-panel2 text-muted">
                {idCode}
              </div>
            )}
          </div>
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
      {stats && stats.length > 0 && (
        <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-x-8 gap-y-3">
          {stats.map(s => (
            <div key={s.label}>
              <div className="text-[10px] uppercase tracking-wider text-muted">{s.label}</div>
              <div className={`text-lg font-semibold tabular-nums ${TONE_CLS[s.tone ?? "default"]}`}>
                {s.value}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
