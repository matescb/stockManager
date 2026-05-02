/**
 * RouteSkeleton (FE2-022)
 *
 * Lightweight loading placeholders shown while a lazy-loaded route chunk
 * is being fetched. Uses existing Tailwind utility classes (`card`,
 * `table`) — no new design tokens.
 *
 * Variants:
 *  - "table"  → header + toolbar bar + a few skeleton rows (Orders, Builds,
 *               Projects lists, Reports)
 *  - "form"   → header bar + a couple of field-height blocks (create pages)
 */

interface RouteSkeletonProps {
  variant?: "table" | "form";
}

const pulse = "animate-pulse bg-panel2 rounded";

export function RouteSkeleton({ variant = "table" }: RouteSkeletonProps) {
  if (variant === "form") {
    return (
      <div className="p-6 flex flex-col gap-6 max-w-xl">
        {/* Page title */}
        <div className={`${pulse} h-6 w-48`} />
        {/* Fields */}
        {[1, 2, 3].map(i => (
          <div key={i} className="flex flex-col gap-1">
            <div className={`${pulse} h-3 w-24`} />
            <div className={`${pulse} h-8 w-full`} />
          </div>
        ))}
        {/* Submit button */}
        <div className={`${pulse} h-8 w-24`} />
      </div>
    );
  }

  // "table" variant (default)
  return (
    <div className="p-6 flex flex-col gap-4">
      {/* Toolbar / title row */}
      <div className="flex items-center justify-between">
        <div className={`${pulse} h-6 w-40`} />
        <div className={`${pulse} h-8 w-24`} />
      </div>
      {/* Table card */}
      <div className="card overflow-hidden">
        {/* Header row */}
        <div className="flex gap-4 px-3 py-2 border-b border-border">
          {[60, 120, 80, 100].map(w => (
            <div key={w} className={`${pulse} h-3`} style={{ width: w }} />
          ))}
        </div>
        {/* Body rows */}
        {[1, 2, 3, 4, 5].map(i => (
          <div key={i} className="flex gap-4 px-3 py-2.5 border-b border-border last:border-0">
            {[60, 120, 80, 100].map(w => (
              <div key={w} className={`${pulse} h-3`} style={{ width: w }} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
