# ADR-0019: Self-hosted Umami for product analytics, env-gated tracker

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

stockManager has Sentry for errors but no signal on what users actually do — which routes get traffic, where users drop off in the BOM/scan-import flows, which features are dead weight. Without that, the next round of refactor / cleanup decisions are guessing.

Three alternatives considered:

1. **No analytics.** Status quo. Wins on simplicity and zero data-handling burden. Loses on every product decision becoming an opinion rather than a measurement.
2. **Plausible / Google Analytics (SaaS).** Hosted, cheap, well-known. GA4 is rejected on privacy posture (cookies, fingerprinting, US data). Plausible Cloud is acceptable but couples a small operational dependency to a paid third party for what is otherwise a hobby-scale deployment.
3. **Self-hosted [Umami](https://umami.is).** Open source, no cookies, respects `Do-Not-Track`, no fingerprinting. Already running on a separate VPS at `https://stats.matescb.cz`. Adds one `<script>` tag to the SPA bundle, nothing on the backend.

## Decision

Use the existing self-hosted Umami instance.

Wire the tracker as an **env-gated loader IIFE in `web/index.html`**:

- Two new build args / env vars: `VITE_UMAMI_WEBSITE_ID`, `VITE_UMAMI_SCRIPT_URL`.
- Both default to empty in `web/Dockerfile.prod`, `docker-compose.prod.yml`, `deploy/.env.prod.example`.
- The IIFE only injects the `<script>` tag when both are non-empty.
- Empty values are first-class: dev builds, CI builds, any prod deploy that omits the env produce a bundle that does nothing — no script tag, no network call, no DOM mutation.

Both values are inlined into the SPA bundle and visible in the browser DevTools. They are **identifiers, not secrets**, and treated the same way the Sentry public DSN (`VITE_SENTRY_DSN`) is treated by ADR-0014.

## Consequences

**Good**

- Zero new backend code, zero new Python deps.
- Empty-env-disables pattern matches Sentry — one mental model for "frontend observability vendor inlined at build time".
- Self-hosted means the user's pageviews never leave my infrastructure (Umami VPS is mine; stockManager VPS is mine; both EU). Defensible to anyone asking about GDPR.
- Rotation is trivial: change UUID in `.env.prod`, redeploy. No code change.

**Trade-offs**

- The website ID UUID is in the bundle and visible to any visitor. Anyone can `curl` the tracker pretending to be the site. This is the same threat model as a Sentry public DSN — Umami treats it as identifier, not auth. If someone spams pageview pings, throttle at the Umami nginx layer.
- Tracker is blocked by most ad-blockers (`*/script.js` heuristic). Acceptable — the cost of using a generic script name to avoid being singled out is that blockers still catch it. The data is "users who don't run an ad-blocker", not "all users". Calibrate aggregate numbers accordingly.
- Adding a third observability target (after Sentry-backend and Sentry-frontend) is more env vars to forget. Mitigated by both Umami env vars being optional with safe-empty default — forgetting them in dev is harmless; forgetting them in prod is a missing-data signal, not a runtime fault.

## Alternatives rejected

- **GA4 / Google Tag Manager** — cookies, fingerprinting, US data, Google account dependency. Privacy posture incompatible with this project's user expectations.
- **Plausible Cloud** — fine product but a paid SaaS dependency for a self-hosted-everything-else stack felt mismatched. If self-hosted Umami breaks, fall back here.
- **Server-side analytics from request logs** — accurate but costly to build (would need a log shipper, an aggregation pipeline, and a UI). Umami gives all of that for one `<script>` tag.

## References

- Runbook: [`docs/runbooks/analytics-umami.md`](../runbooks/analytics-umami.md)
- Phase: [`docs/phases/12-observability-sentry.md`](../phases/12-observability-sentry.md) — "Sister system: Umami"
- Wiring: `web/index.html`, `web/Dockerfile.prod`, `docker-compose.prod.yml`, `deploy/.env.prod.example`
- PR: #319
