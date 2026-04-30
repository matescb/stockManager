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
  // Per the wizard. Sends user IP + request headers; cookies are
  // redacted automatically. Flip to false for stricter data minimisation.
  sendDefaultPii: true,
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
