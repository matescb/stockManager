import { toast } from "sonner";

type Props = {
  /** Headline. Says what this token is and that it is shown once. */
  title: string;
  /** The one-time-visible URL. */
  url: string;
  /** Clears the caller's token state; the value is unrecoverable after. */
  onDismiss: () => void;
};

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Copied to clipboard.");
  } catch {
    toast.error("Could not copy — your browser may not support clipboard access.");
  }
}

/**
 * SEC2-008 copy-once banner.
 *
 * The workspace settings page mints two kinds of catalog token (the workspace
 * token and per-recipient tokens) and the backend returns the plaintext for
 * either exactly once. Both banners were written out longhand, near-verbatim,
 * ~80 lines apart — so a fix to one silently missed the other. One component,
 * two callers.
 */
export function CopyOnceTokenBanner({ title, url, onDismiss }: Props) {
  return (
    <div className="card border-warning bg-warning/10 p-3 space-y-2">
      <div className="text-xs font-semibold text-warning">{title}</div>
      <div className="flex gap-2 items-center">
        <input className="input flex-1 font-mono text-xs" readOnly value={url} aria-label={title} />
        <button
          className="btn-primary"
          type="button"
          onClick={() => {
            copyToClipboard(url);
            onDismiss();
          }}
        >
          Copy &amp; dismiss
        </button>
        <button className="btn" type="button" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
