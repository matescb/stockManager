import { useMemo } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, ImageOff, X } from "lucide-react";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatQuantity } from "@/lib/format";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import { providerLabel } from "@/lib/providers";
import type { Part, StorageLocation } from "@/types";

/**
 * The parts-list preview pane — "is this the part I meant?" without
 * leaving the list.
 *
 * ## The field set, and what is deliberately missing
 *
 * The complaint this pane answers is *overcrowding*, so a preview that
 * reprints the whole part page would be a worse part page. It carries
 * seven things, and every one of them earns its line:
 *
 *  - **Image, name, manufacturer, MPN, type.** Identity. This is the
 *    "did I click the right row" answer, and it has to be a self-contained
 *    card rather than a pointer back at the row.
 *  - **On hand** (red below the low-stock threshold) and **Reserved**
 *    (only when non-zero, since it is a hidden column by default).
 *  - **Where it is.** The top three storage locations by quantity. This
 *    is the one field here that appears in *no* column of the table, and
 *    on a warehouse floor it is the whole question.
 *  - **Two links.** Open full page, and Add stock.
 *
 * Left out on purpose: description (prose, and the reason to open the
 * full page), footprint and category (already columns), threshold,
 * attrition, internal P/N, datasheet, provider refresh state. Each would
 * cost a line and answer a question nobody asks of a preview.
 *
 * ## Paint first, hydrate after
 *
 * `GET /parts` returns whole part objects, not a list projection, so the
 * row the user just clicked already holds everything above except the
 * storage breakdown. That row is handed in as `fallbackRow` and used as
 * TanStack's `placeholderData`, so the pane paints on the click and the
 * fetch only ever *corrects* it. A preview that spins on every selection
 * is worse than the navigation it replaced.
 *
 * The three queries all reuse keys the full part pages already use
 * (`part` / `part…stock` from `PartLayout` and `PartStock`, `storage`
 * from half the app), so browsing the preview warms the detail page and
 * vice versa.
 */

type PartStockResponse = {
  total_on_hand: number;
  rows: { storage_location_id: string | null; lot_id: string | null; quantity: number }[];
};

/** How many storage locations to name before collapsing to "+N more". */
const MAX_STORAGE_ROWS = 3;

type Props = {
  partId: string;
  /** The list row for `partId`, when the loaded pages happen to hold it. */
  fallbackRow: Part | null;
  onClose: () => void;
};

export default function PartPreviewPane({ partId, fallbackRow, onClose }: Props) {
  const partQuery = useQuery({
    queryKey: useWsKey("part", partId),
    queryFn: ({ signal }) => api.get<Part>(`/parts/${partId}`, { signal }),
    // The whole point: render the row we already hold, then correct it.
    placeholderData: fallbackRow ?? undefined,
  });
  const stockQuery = useQuery({
    queryKey: useWsKey("part", partId, "stock"),
    queryFn: ({ signal }) => api.get<PartStockResponse>(`/parts/${partId}/stock`, { signal }),
  });
  const storageQuery = useQuery({
    queryKey: useWsKey("storage"),
    queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }),
  });

  // `placeholderData` already resolves to the row while the fetch is in
  // flight; the extra `?? fallbackRow` covers a *failed* fetch, where
  // showing the row we have beats showing an error over data we hold.
  const part = partQuery.data ?? fallbackRow ?? null;

  const storageNames = useMemo(
    () => new Map((storageQuery.data ?? []).map((s) => [s.id, s.name] as const)),
    [storageQuery.data],
  );

  // Ledger rows are per (location, lot); the pane wants per location.
  const locations = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of stockQuery.data?.rows ?? []) {
      const key = row.storage_location_id ?? "";
      totals.set(key, (totals.get(key) ?? 0) + row.quantity);
    }
    return Array.from(totals, ([id, quantity]) => ({ id, quantity }))
      .filter((r) => r.quantity !== 0)
      .sort((a, b) => b.quantity - a.quantity);
  }, [stockQuery.data]);

  const shownLocations = locations.slice(0, MAX_STORAGE_ROWS);
  const hiddenLocationCount = locations.length - shownLocations.length;

  if (!part) {
    return (
      <PaneShell label="Part preview" onClose={onClose}>
        <p className="text-sm text-muted">
          {partQuery.isError ? "Could not load this part." : "Loading…"}
        </p>
      </PaneShell>
    );
  }

  const safeImageUrl = isSafeHttpOrSameOriginUrl(part.image_url) ? part.image_url : null;
  const onHand = part.on_hand ?? 0;
  const threshold = part.low_stock_report_quantity;
  const isLow = threshold != null && onHand < threshold;
  const reserved = part.reserved ?? 0;

  return (
    <PaneShell label={`Preview of ${part.name}`} onClose={onClose}>
      <div className="flex items-start gap-3">
        {safeImageUrl ? (
          <img
            src={safeImageUrl}
            alt=""
            loading="lazy"
            className="h-14 w-14 shrink-0 rounded bg-panel object-contain"
          />
        ) : (
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded bg-panel2/40 text-muted">
            <ImageOff size={18} />
          </div>
        )}
        <div className="min-w-0">
          <h2 className="card-title break-words">{part.name}</h2>
          <p className="mt-1 text-xs text-muted break-words">
            {part.manufacturer || "Unknown manufacturer"}
            {part.mpn && <span className="ml-2 font-mono">{part.mpn}</span>}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        <span className="pill">{part.part_type}</span>
        {part.linked_provider && (
          <span className="pill bg-accent/15 text-accent">
            {providerLabel(part.linked_provider)}
          </span>
        )}
        {part.archived_at && <span className="pill bg-danger/20 text-danger">archived</span>}
      </div>

      <div>
        <h3 className="section-title">Stock</h3>
        {/* Every quantity here goes through `formatQuantity` — never
            `parseInt` / `| 0` / `Math.floor`, which would turn a 12.5 m
            reel into 12. The unit argument is omitted because the parts
            wire format does not carry `unit_of_measure` yet; when it
            does, it is a second argument at these call sites and
            `formatQuantity` keeps suppressing the default `pcs`. */}
        <div className="mt-1 flex items-baseline gap-6">
          <Stat
            label="On hand"
            value={formatQuantity(onHand)}
            className={isLow ? "text-danger" : undefined}
          />
          {reserved > 0 && (
            <Stat label="Reserved" value={formatQuantity(reserved)} className="text-warning" />
          )}
        </div>
      </div>

      <div>
        <h3 className="section-title">Where it is</h3>
        <div className="mt-1 space-y-0.5 text-sm">
          {stockQuery.isError ? (
            <p className="text-muted">Storage breakdown unavailable.</p>
          ) : stockQuery.isPending ? (
            <p className="text-muted">Loading…</p>
          ) : shownLocations.length === 0 ? (
            <p className="text-muted">Not stocked anywhere.</p>
          ) : (
            <>
              {shownLocations.map((row) => (
                <div key={row.id} className="flex items-baseline justify-between gap-3">
                  <span className="truncate">
                    {row.id ? storageNames.get(row.id) ?? "Unknown location" : "No location"}
                  </span>
                  <span className="shrink-0 tabular-nums">{formatQuantity(row.quantity)}</span>
                </div>
              ))}
              {hiddenLocationCount > 0 && (
                <p className="text-xs text-muted">
                  +{hiddenLocationCount} more location{hiddenLocationCount === 1 ? "" : "s"}
                </p>
              )}
            </>
          )}
        </div>
      </div>

      <div className="mt-auto flex flex-wrap gap-2 pt-1">
        <Link to={`/parts/${part.id}/info`} className="btn-primary btn-sm">
          Open full page <ExternalLink size={12} />
        </Link>
        <Link to={`/parts/${part.id}/add`} className="btn btn-sm">
          Add stock
        </Link>
      </div>
    </PaneShell>
  );
}

/**
 * The pane chrome.
 *
 * A `<aside>` landmark with an accessible name, **not** a dialog: the
 * whole point is to browse the list while it is open, so it must not
 * trap focus, must not steal focus on selection, and must not be
 * `aria-modal`. `Modal.tsx` is the right component for the opposite
 * case; this is deliberately not it.
 *
 * `hidden xl:flex` mirrors `usePartPreview`'s breakpoint check. The hook
 * already refuses to select below `xl`, so this is belt-and-braces for
 * the frame between a resize and the re-render. The pane holds at 320px
 * through `xl` and only widens at `2xl` — at `xl` the category rail and
 * the table are already sharing 768px, so a wider pane there comes
 * straight out of the table.
 */
function PaneShell({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <aside
      aria-label={label}
      data-testid="part-preview-pane"
      className="card sticky top-4 hidden max-h-[calc(100vh-2rem)] w-80 shrink-0 flex-col gap-4 self-start overflow-y-auto p-4 xl:flex 2xl:w-96"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="section-title">Preview</span>
        <button
          type="button"
          className="btn-ghost btn-sm -mr-1 -mt-1"
          onClick={onClose}
          aria-label="Close preview"
        >
          <X size={14} />
        </button>
      </div>
      {children}
    </aside>
  );
}

function Stat({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${className ?? ""}`}>{value}</div>
    </div>
  );
}
