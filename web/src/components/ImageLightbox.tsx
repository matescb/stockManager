import { useEffect, useRef, useState } from "react";
import { ExternalLink, ImageOff, X } from "lucide-react";
import { Modal } from "@/components/Modal";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";

type Props = {
  open: boolean;
  onClose: () => void;
  /** Full-size image URL. Unsafe URLs (non-http, protocol-relative) render nothing. */
  src: string | null | undefined;
  /** Accessible name of the dialog and caption above the image. */
  title: string;
  /** Alt text for the image; defaults to `title`. */
  alt?: string;
  /**
   * Where "Open original" points. Defaults to `src`; pass `null` to hide
   * the link (e.g. when the only URL forces a download).
   */
  originalHref?: string | null;
};

/**
 * Lock page scroll while a lightbox is open. The scrollbar's width is
 * added back as right padding so the page behind the overlay does not
 * jump sideways when the scrollbar disappears.
 */
function useBodyScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return undefined;
    const { body, documentElement } = document;
    const previousOverflow = body.style.overflow;
    const previousPaddingRight = body.style.paddingRight;
    const scrollbarWidth = window.innerWidth - documentElement.clientWidth;
    body.style.overflow = "hidden";
    if (scrollbarWidth > 0) {
      const current = parseFloat(window.getComputedStyle(body).paddingRight) || 0;
      body.style.paddingRight = `${current + scrollbarWidth}px`;
    }
    return () => {
      body.style.overflow = previousOverflow;
      body.style.paddingRight = previousPaddingRight;
    };
  }, [active]);
}

/**
 * In-page full-size image viewer built on `Modal`, so it inherits Esc /
 * backdrop close, the focus trap and focus return to the trigger. The
 * image sits on a white tile so transparent provider PNGs stay legible in
 * dark theme.
 */
export default function ImageLightbox({
  open,
  onClose,
  src,
  title,
  alt,
  originalHref,
}: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  useBodyScrollLock(open);

  const safeSrc = isSafeHttpOrSameOriginUrl(src) ? src : null;
  const href = originalHref === undefined ? safeSrc : originalHref;
  const safeHref = isSafeHttpOrSameOriginUrl(href) ? href : null;
  const failed = safeSrc !== null && failedSrc === safeSrc;

  return (
    <Modal
      open={open && safeSrc !== null}
      onClose={onClose}
      title={title}
      initialFocusRef={closeRef}
      className="card flex max-w-[95vw] flex-col gap-2 p-3"
    >
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{title}</span>
        {safeHref && (
          <a
            href={safeHref}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          >
            Open original <ExternalLink size={12} aria-hidden="true" />
          </a>
        )}
        <button
          ref={closeRef}
          type="button"
          className="btn-ghost btn-sm"
          onClick={onClose}
          aria-label="Close image"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      {failed ? (
        <div className="flex h-48 w-72 max-w-full flex-col items-center justify-center gap-2 rounded bg-panel2 text-sm text-muted">
          <ImageOff size={20} aria-hidden="true" />
          Image failed to load.
        </div>
      ) : (
        <div className="flex items-center justify-center rounded bg-white p-2">
          <img
            src={safeSrc ?? undefined}
            alt={alt ?? title}
            className="max-h-[80vh] max-w-full object-contain"
            onError={() => setFailedSrc(safeSrc)}
          />
        </div>
      )}
    </Modal>
  );
}
