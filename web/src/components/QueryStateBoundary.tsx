import { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { ApiError } from "@/lib/api";

/**
 * Render-time error fallback for list pages.
 *
 * Pre-fix, a failed list query just resolved to `data === undefined`
 * and the page rendered as if the workspace were empty (FE2-001).
 * Wrapping the page body in this boundary surfaces a banner when
 * `query.isError` is true, with a Retry button that calls
 * `query.refetch()`.
 *
 * 401 is handled centrally — the QueryCache `onError` in main.tsx
 * fires the auth bus, which redirects to /login. We don't render the
 * banner for 401 because the redirect is happening anyway and a
 * flash of "couldn't load" would confuse the user mid-bounce.
 */
type QueryLike = {
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
  isFetching: boolean;
};

export default function QueryStateBoundary({
  query,
  resourceLabel,
  children,
}: {
  query: QueryLike;
  resourceLabel: string;
  children: ReactNode;
}) {
  const is401 = query.error instanceof ApiError && query.error.status === 401;
  if (query.isError && !is401) {
    const msg = query.error instanceof Error ? query.error.message : "Unknown error";
    return (
      <div className="card p-4 flex items-start gap-3 text-sm">
        <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" />
        <div className="flex-1">
          <div className="font-medium text-text">Couldn't load {resourceLabel}.</div>
          <div className="text-muted mt-0.5">{msg}</div>
        </div>
        <button
          type="button"
          className="btn"
          disabled={query.isFetching}
          onClick={() => query.refetch()}
        >
          {query.isFetching ? "Retrying…" : "Retry"}
        </button>
      </div>
    );
  }
  return <>{children}</>;
}
