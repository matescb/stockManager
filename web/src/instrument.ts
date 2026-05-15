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
const tracesSampleRate = parseSampleRate(
  import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE,
  "VITE_SENTRY_TRACES_SAMPLE_RATE",
  import.meta.env.PROD ? "required" : "default-zero",
);
const replaysSessionSampleRate = parseSampleRate(
  import.meta.env.VITE_SENTRY_REPLAYS_SESSION_SAMPLE_RATE,
  "VITE_SENTRY_REPLAYS_SESSION_SAMPLE_RATE",
  "default-zero",
);
const replaysOnErrorSampleRate = parseSampleRate(
  import.meta.env.VITE_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE,
  "VITE_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE",
  "default-zero",
);
const requestHeaderDenylist = new Set([
  "cookie",
  "authorization",
  "x-api-key",
  "x-workspace-id",
]);
const requestUrlHeaders = new Set(["referer", "referrer"]);
const breadcrumbUrlKeys = new Set(["url", "from", "to"]);
const transactionUrlKeys = new Set(["url", "from", "to", "http.url"]);
const sensitiveTextPattern =
  /(["']?\b(?:password|passwd|pass|token|secret|api[_-]?key|authorization|cookie|session(?:[_-]?id)?)["']?\s*[:=]\s*["']?)([^"',&\s;}\]]+)/gi;
type MutableRequest = {
  url?: string;
  query_string?: unknown;
  headers?: Record<string, unknown>;
  method?: string;
  data?: unknown;
  body_redacted?: boolean;
};

function parseSampleRate(
  raw: string | undefined,
  envName: string,
  mode: "required" | "default-zero",
): number {
  if (raw === undefined || raw.trim() === "") {
    if (mode === "required") {
      throw new Error(`${envName} is required for production Sentry init`);
    }
    return 0.0;
  }
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0.0 || value > 1.0) {
    throw new Error(`${envName} must be a number between 0.0 and 1.0`);
  }
  return value;
}

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

function scrubRequest(req: MutableRequest | undefined, { redactNonGetBody }: { redactNonGetBody: boolean }) {
  if (!req) return;
  if (typeof req.url === "string") {
    req.url = stripQueryString(req.url);
  }
  delete req.query_string;
  if (req.headers) {
    for (const k of Object.keys(req.headers)) {
      if (requestHeaderDenylist.has(k.toLowerCase())) {
        delete req.headers[k];
      }
    }
    scrubUrlKeys(req.headers, requestUrlHeaders);
  }
  const method = (req.method ?? "").toUpperCase();
  if (redactNonGetBody && method && method !== "GET" && req.data !== undefined) {
    delete req.data;
    req.body_redacted = true;
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

    scrubRequest(event.request as MutableRequest | undefined, { redactNonGetBody: true });
    for (const breadcrumb of event.breadcrumbs ?? []) {
      if (breadcrumb.data) {
        scrubUrlKeys(breadcrumb.data, breadcrumbUrlKeys);
      }
    }
    return event;
  },
  beforeSendTransaction(event) {
    if (typeof event.transaction === "string") {
      event.transaction = stripQueryString(event.transaction);
    }
    scrubRequest(event.request as MutableRequest | undefined, { redactNonGetBody: false });
    if (event.tags) {
      scrubUrlKeys(event.tags as Record<string, unknown>, transactionUrlKeys);
    }
    const trace = event.contexts?.trace as { data?: Record<string, unknown> } | undefined;
    if (trace?.data) {
      scrubUrlKeys(trace.data, transactionUrlKeys);
    }
    for (const span of event.spans ?? []) {
      if (span.data) {
        scrubUrlKeys(span.data as Record<string, unknown>, transactionUrlKeys);
      }
      if (typeof span.description === "string") {
        span.description = stripQueryString(span.description);
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
  // Tracing is explicit in prod. docker-compose.prod.yml and the
  // Dockerfile both fail closed if this build-time env is missing.
  tracesSampleRate,
  // Distributed tracing headers are only attached for requests to URLs
  // matching this list — keeps third-party calls (Mouser, DigiKey)
  // unaffected.
  tracePropagationTargets: ["localhost", /^https:\/\/parts\.matescb\.cz\/api/],
  // Session Replay is opt-in. Defaults are 0.0 so DOM capture only turns
  // on after an explicit operator decision and ADR update.
  replaysSessionSampleRate,
  replaysOnErrorSampleRate,
  // Enables `Sentry.logger.*` for structured log search in Sentry.
  enableLogs: true,
});
