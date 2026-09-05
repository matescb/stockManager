import { Link, Navigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";

/**
 * `/c/:code` — the scan landing page (Track A1).
 *
 * A printed label's QR points here. This page does one thing: ask the
 * backend what object the code names, then send the browser to that
 * object's detail page. It renders no chrome of its own on the happy
 * path — a scan should feel like it opened the part, not like it visited
 * an interstitial.
 *
 * Why a redirect rather than rendering the object inline: every entity
 * type already has a detail route with its own tabs, breadcrumbs and
 * deep-link semantics. Duplicating that here would be a second surface
 * to keep in sync, and the URL after a scan should be the object's
 * canonical URL so it can be bookmarked and shared.
 *
 * The redirect is `replace`, so Back returns to wherever the user was
 * before the scan instead of landing on the resolver again and bouncing
 * straight forward.
 *
 * Sits inside `<Gate />`: an unauthenticated scan goes to /login and
 * `state.from` brings it back here afterwards.
 */

/** Detail route for each codeable entity type. */
const DETAIL_PATH: Record<string, (id: string) => string> = {
  part: id => `/parts/${id}/info`,
  lot: id => `/lots/${id}/info`,
  storage_location: id => `/storage/${id}/info`,
  order: id => `/orders/${id}`,
  build: id => `/builds/${id}`,
};

type ResolvedCode = {
  code: string;
  entity_type: string;
  entity_id: string;
};

function CodeNotFound({ code }: { code: string }) {
  return (
    <div className="card p-6 max-w-xl space-y-3">
      <div className="flex items-start gap-3">
        <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" />
        <div>
          <h1 className="text-lg font-semibold">Code not found</h1>
          <p className="text-sm text-muted mt-1">
            Nothing in this workspace is labelled{" "}
            <span className="font-mono">{code}</span>. It may belong to a
            different workspace, or the object may have been deleted.
          </p>
        </div>
      </div>
      <div className="flex gap-2">
        <Link to="/parts" className="btn btn-primary">
          Go to parts
        </Link>
        <Link to="/storage" className="btn">
          Go to storage
        </Link>
      </div>
    </div>
  );
}

export default function CodeResolve() {
  const { code = "" } = useParams<{ code: string }>();

  const query = useQuery({
    queryKey: useWsKey("code", code),
    // `enabled` guards the degenerate `/c/` case; react-router will not
    // normally match it, but a query with an empty path is worse than
    // no query at all.
    enabled: code.length > 0,
    queryFn: ({ signal }) =>
      api.get<ResolvedCode>(`/codes/${encodeURIComponent(code)}`, { signal }),
  });

  const { isError, error, refetch, isFetching, data } = query;

  // 404 is the expected failure — an unknown, mistyped, or foreign
  // code — and gets its own copy rather than a generic error banner.
  // Everything else (network blip, 5xx) is transient and offers Retry.
  const isNotFound = error instanceof ApiError && error.status === 404;
  if (isNotFound) return <CodeNotFound code={code} />;

  if (data) {
    const toPath = DETAIL_PATH[data.entity_type];
    // An entity_type the server knows and this build doesn't means the
    // frontend is older than the backend. Say so plainly rather than
    // navigating somewhere wrong.
    if (!toPath) return <CodeNotFound code={code} />;
    return <Navigate to={toPath(data.entity_id)} replace />;
  }

  if (isError) {
    return (
      <div className="card p-4 flex items-start gap-3 text-sm max-w-xl">
        <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" />
        <div className="flex-1">
          <div className="font-medium text-text">Couldn't look up that code.</div>
          <div className="text-muted mt-0.5">
            {error instanceof ApiError ? error.userMessage : "Unknown error"}
          </div>
        </div>
        <button
          type="button"
          className="btn"
          disabled={isFetching}
          onClick={() => void refetch()}
        >
          {isFetching ? "Retrying…" : "Retry"}
        </button>
      </div>
    );
  }

  return <div className="p-6 text-muted text-sm">Opening…</div>;
}
