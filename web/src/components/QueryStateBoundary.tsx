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
export type QueryLike = {
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
  isFetching: boolean;
};

function is401(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.userMessage;
  if (err instanceof Error) return err.message;
  return "Unknown error";
}

export default function QueryStateBoundary({
  query,
  resourceLabel,
  children,
}: {
  query: QueryLike;
  resourceLabel: string;
  children: ReactNode;
}) {
  if (query.isError && !is401(query.error)) {
    return (
      <div className="card p-4 flex items-start gap-3 text-sm">
        <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" />
        <div className="flex-1">
          <div className="font-medium text-text">Couldn't load {resourceLabel}.</div>
          <div className="text-muted mt-0.5">{errorMessage(query.error)}</div>
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

/**
 * Inline error pill for panels that mix an action surface with a query
 * (e.g. AttachmentsPanel, PartSettings, BuildCreate). Unlike
 * `QueryStateBoundary`, this does NOT short-circuit the surrounding
 * subtree — it only renders in place of the data block, leaving
 * whatever action UI surrounds it interactive.
 *
 * Returns `null` when the query has no error or when the error is a
 * 401 (handled by the global auth bus in main.tsx — same rule as
 * `QueryStateBoundary`).
 */
export function InlineQueryError({
  query,
  label,
  className,
}: {
  query: QueryLike;
  label: string;
  className?: string;
}) {
  if (!query.isError || is401(query.error)) return null;
  return (
    <div
      role="alert"
      className={`card p-2 text-sm flex items-center gap-2 border-danger/40 ${className ?? ""}`}
    >
      <AlertTriangle size={14} className="text-danger shrink-0" />
      <span className="flex-1">
        <span className="font-medium">Couldn't load {label}.</span>{" "}
        <span className="text-muted">{errorMessage(query.error)}</span>
      </span>
      <button
        type="button"
        className="btn btn-sm"
        disabled={query.isFetching}
        onClick={() => query.refetch()}
      >
        {query.isFetching ? "Retrying…" : "Retry"}
      </button>
    </div>
  );
}
