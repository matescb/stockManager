/**
 * Sentry init sidecar. Per the official sentry-react-sdk skill, this file
 * is imported as the very first line of `main.tsx` — Sentry has to wire
 * up its global error handlers before any other code runs so that errors
 * thrown during module load aren't lost.
 *
 * DSN comes from VITE_SENTRY_DSN, baked into the bundle at build time by
 * docker-compose.prod.yml's build args. Empty string → SDK initialises
 * but disables itself (zero events sent), which keeps dev quiet.
 */
import * as React from "react";
import * as Sentry from "@sentry/react";
import {
  createRoutesFromChildren,
  matchRoutes,
  useLocation,
  useNavigationType,
} from "react-router-dom";

const dsn = import.meta.env.VITE_SENTRY_DSN ?? "";
const tracesRaw = import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? "1.0";
const tracesSampleRate = Number.isFinite(parseFloat(tracesRaw))
  ? parseFloat(tracesRaw)
  : 1.0;
const requestHeaderDenylist = new Set(["cookie", "authorization", "x-workspace-id"]);
const requestUrlHeaders = new Set(["referer", "referrer"]);
const breadcrumbUrlKeys = new Set(["url", "from", "to"]);
const sensitiveTextPattern =
  /(["']?\b(?:password|passwd|pass|token|secret|api[_-]?key|authorization|cookie|session(?:[_-]?id)?)["']?\s*[:=]\s*["']?)([^"',&\s;}\]]+)/gi;

function stripQueryString(rawUrl: string): string {
  const fragmentIndex = rawUrl.indexOf("#");
  const beforeFragment = fragmentIndex === -1 ? rawUrl : rawUrl.slice(0, fragmentIndex);
  const fragment = fragmentIndex === -1 ? "" : rawUrl.slice(fragmentIndex);
  const queryIndex = beforeFragment.indexOf("?");
  const fragmentQueryIndex = fragment.indexOf("?");

  const cleanBeforeFragment =
    queryIndex === -1 ? beforeFragment : beforeFragment.slice(0, queryIndex);
  const cleanFragment =
    fragmentQueryIndex === -1 ? fragment : fragment.slice(0, fragmentQueryIndex);
  return `${cleanBeforeFragment}${cleanFragment}`;
}

function scrubUrlKeys(values: Record<string, unknown>, keys: ReadonlySet<string>) {
  for (const key of Object.keys(values)) {
    if (keys.has(key.toLowerCase()) && typeof values[key] === "string") {
      values[key] = stripQueryString(values[key]);
    }
  }
}

function scrubSensitiveText(value: string): string {
  return value.replace(sensitiveTextPattern, "$1[Filtered]");
}

function scrubEventStrings(event: Record<string, unknown>) {
  if (typeof event.message === "string") {
    event.message = scrubSensitiveText(event.message);
  }

  const exception = event.exception as { values?: unknown } | undefined;
  if (!exception || !Array.isArray(exception.values)) {
    return;
  }
  for (const item of exception.values) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const exceptionValue = item as Record<string, unknown>;
    for (const key of ["value", "message"]) {
      if (typeof exceptionValue[key] === "string") {
        exceptionValue[key] = scrubSensitiveText(exceptionValue[key]);
      }
    }
  }
}

Sentry.init({
  dsn,
  // Same-origin tunnel: the React SDK POSTs envelopes to our backend,
  // which forwards to Sentry's ingest endpoint. Without this, ad-blockers
  // (uBlock Origin, Brave Shields, Pi-hole) intercept the direct call
  // to *.ingest.sentry.io with ERR_BLOCKED_BY_CLIENT and we lose every
  // event from anyone running an ad-blocker. The tunnel handler lives
  // at backend/app/api/routes/sentry_tunnel.py.
  tunnel: "/api/sentry-tunnel",
  environment: import.meta.env.MODE,
  // Release identifier — set to the git SHA at build time by compose.
  // Sentry uses this to group issues per release, auto-resolve issues
  // when a fixed release ships, and pair stack traces with their
  // matching uploaded source maps.
  release: import.meta.env.VITE_APP_VERSION || undefined,
  // Per the wizard. Sends user IP + request headers; cookies are
  // redacted automatically. Flip to false for stricter data minimisation.
  sendDefaultPii: true,
  // Default-deny request bodies on any non-GET method. Mirrors the
  // backend `_scrub_event` posture (v2 teardown SEC2-005) — the prior
  // narrow allow-list ("only /api/workspaces") leaked credential-bearing
  // bodies on signup/login/invitations/parts-provider/bulk-import.
  // There is no read-only POST in the API; if one is added later,
  // attaching the body to a Sentry event still tells triage nothing
  // that isn't in URL + status_code.
  //
  // Headers: Cookie / Authorization / X-Workspace-Id are tenant- or
  // session-identifying on every method, so the header scrub applies
  // regardless of body method.
  beforeSend(event) {
    scrubEventStrings(event as unknown as Record<string, unknown>);

    const req = event.request;
    if (req) {
      if (typeof req.url === "string") {
        req.url = stripQueryString(req.url);
      }
      delete (req as Record<string, unknown>).query_string;
      if (req.headers) {
        for (const k of Object.keys(req.headers)) {
          if (requestHeaderDenylist.has(k.toLowerCase())) {
            delete (req.headers as Record<string, unknown>)[k];
          }
        }
        scrubUrlKeys(req.headers as Record<string, unknown>, requestUrlHeaders);
      }
      const method = (req.method ?? "").toUpperCase();
      if (method && method !== "GET") {
        if (req.data !== undefined) {
          delete req.data;
          (req as Record<string, unknown>).body_redacted = true;
        }
      }
    }
    for (const breadcrumb of event.breadcrumbs ?? []) {
      if (breadcrumb.data) {
        scrubUrlKeys(breadcrumb.data, breadcrumbUrlKeys);
      }
    }
    return event;
  },
  integrations: [
    // Hooks-based router integration: we use <BrowserRouter> + <Routes>
    // (not createBrowserRouter), so we hand React Router's hooks to
    // Sentry rather than wrapping a router instance.
    Sentry.reactRouterV6BrowserTracingIntegration({
      useEffect: React.useEffect,
      useLocation,
      useNavigationType,
      createRoutesFromChildren,
      matchRoutes,
    }),
    // Session Replay. The skill recommends maskAllText + blockAllMedia for
    // any app that may render sensitive data — for us that includes part
    // attachments / lot photos / customer references on a scanned bag.
    Sentry.replayIntegration({
      maskAllText: true,
      blockAllMedia: true,
    }),
  ],
  // Tracing. 1.0 captures every transaction; once we have steady traffic
  // and Sentry quota becomes a concern, drop to 0.1–0.2 in prod.
  tracesSampleRate,
  // Distributed tracing headers are only attached for requests to URLs
  // matching this list — keeps third-party calls (Mouser, DigiKey)
  // unaffected.
  tracePropagationTargets: ["localhost", /^https:\/\/parts\.matescb\.cz\/api/],
  // Session Replay sample rates.
  replaysSessionSampleRate: 0.1,
  replaysOnErrorSampleRate: 1.0,
  // Enables `Sentry.logger.*` for structured log search in Sentry.
  enableLogs: true,
});
