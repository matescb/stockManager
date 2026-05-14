# Phase 12 — Observability (Sentry on both runtimes)

Audience: engineer

Wires `@sentry/react` on the frontend and `sentry-sdk[fastapi]` on
the backend, routes browser envelopes through a same-origin tunnel
so ad-blockers don't drop them, and uploads source maps from CI so
stack traces de-minify.

## Why

- A single-VPS deploy with auto-deploy on `main` and no staging
  environment needs an external eye on production. Server logs
  alone don't tell you when the React bundle crashes in someone's
  browser.
- Direct browser → `*.ingest.sentry.io` is intercepted by uBlock
  Origin, Brave Shields, and Pi-hole, with no fallback. Same-origin
  ingress keeps event delivery reliable.
- Minified stack traces are unreadable; source-map upload has to
  happen in CI (where `SENTRY_AUTH_TOKEN` lives), not in the Docker
  build (where it must not leak into the runtime image).

## What shipped

- **Backend init** — `_init_sentry()` in `backend/app/main.py:62-99`.
  DSN from `SENTRY_DSN` env (empty → no-op).
  `FastApiIntegration` + `StarletteIntegration` registered.
  Prod refuses to boot without an explicit
  `SENTRY_TRACES_SAMPLE_RATE`; `deploy/.env.prod.example` pins `0.05`.
  `before_send` scrubber at `backend/app/main.py:23` strips the
  `Cookie`, `Authorization`, and `X-Workspace-Id` headers and drops
  the request body on workspace settings PATCH/switch endpoints.
- **Frontend init** — `web/src/instrument.ts`, imported as the very
  first line of `main.tsx` per the official sentry-react-sdk skill so
  global error handlers wire up before module-load errors can be
  thrown. `@sentry/react` with React Router v6 hooks-based browser
  tracing plus a matching `beforeSend` scrubber. Prod web builds refuse
  to build without `VITE_SENTRY_TRACES_SAMPLE_RATE`; Session Replay is
  wired with `maskAllText` + `blockAllMedia` but defaults to `0.0`
  sample rates unless explicitly enabled.
- **`/api/sentry-tunnel`** — same-origin proxy in
  `backend/app/api/routes/sentry_tunnel.py`. Forwards envelopes to
  the configured DSN's host + project ID only (host allow-list).
  Rate-limited to `60/min/IP` (Sec CRIT-5) and body-capped via a
  streaming read (`SENTRY_TUNNEL_MAX_BYTES`, default 200 KiB) so an
  oversize body is rejected before the full payload buffers in RAM.
  Wired in `main.py:382` with the slowapi exempt list at `:264`.
- **CI source-map upload** — `web-build` job in
  `.github/workflows/ci.yml:298` runs `npx @sentry/cli sourcemaps
  upload …` after `npm run build`, gated on push to `main`.
  `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` are GitHub
  Actions secrets only.
- **Release tagging** — 12-char short SHA baked into the bundle as
  `VITE_APP_VERSION` (the Sentry `release` tag). Derived once in the
  "Set release name" step of the `web-build` job and reused for the
  sourcemap upload, so bundle and sourcemap always share the same
  release tag (PR #293, issue #283). Sentry groups issues per release
  and auto-resolves them when the next release deploys.
- **Hidden source maps only in CI** — `web/vite.config.ts` gates
  `build.sourcemap` on `SENTRY_AUTH_TOKEN` presence (INFRA2-015);
  VPS builds without the token produce no `.map` files.
  `find -name '*.map' -delete` in `web/Dockerfile.prod` is
  belt-and-braces for edge-case local builds.
- **Structured log audit** — `enableLogs: true` is on in
  `web/src/instrument.ts:105-106`, but AUD-070 found no
  `Sentry.logger.*` call sites in `web/src` or `backend/app`.
  `web/.eslintrc.cjs` rejects future `Sentry.logger.*` calls that pass
  sensitive identifiers such as tokens, passwords, cookies, sessions,
  DSNs, or workspace IDs.

## Invariants introduced

- **`SENTRY_AUTH_TOKEN` must not enter the Docker build context.**
  Source-map upload is a CI-only step (INFRA2-010). Do not add the
  token back to `web/Dockerfile.prod` ARG/ENV or
  `docker-compose.prod.yml` build args. See `CLAUDE.md` — "Sentry
  auth token must not enter the Docker build context".
- **The tunnel host allow-list is non-negotiable.** Without it,
  `/api/sentry-tunnel` is an open egress to anywhere Sentry-shaped.
  The allow-list is pinned to `SENTRY_DSN` + `VITE_SENTRY_DSN`.
- **PII scrubbing runs in `before_send` on both runtimes.** Cookies,
  auth headers, workspace-id headers, and workspace-settings request
  bodies never reach Sentry. Tested at
  `backend/tests/test_sentry_scrubber.py` (TODO(verify): exact test
  filename).
- **`Sentry.logger.*` calls must not receive secrets.** If structured
  logs are added, pass only redacted scalar metadata. The frontend
  lint guard in `web/.eslintrc.cjs` blocks common sensitive identifier
  names at review time.
- **Empty DSN → SDK no-op.** Dev environments stay quiet; no events,
  no network egress.
- **Prod Sentry sampling is explicit.** Backend and frontend traces are
  pinned to `0.05` in `deploy/.env.prod.example`; compose must not add
  `:-0.0` fallbacks for trace sample rates. Session Replay remains
  opt-in at `0.0` by default. See
  [`ADR-0026`](../adr/0026-sentry-sampling-and-replay.md).

## Sister system: Umami (product/usage signal)

Sentry covers errors. Umami covers product/usage — pageviews and SPA
route changes — with a self-hosted instance at `https://stats.matescb.cz`
(separate VPS service from stockManager). Wiring is documented in
[`docs/runbooks/analytics-umami.md`](../runbooks/analytics-umami.md) and
the choice of self-hosted-Umami over Plausible / GA4 / no-analytics is
in [`docs/adr/0019-umami-self-hosted-analytics.md`](../adr/0019-umami-self-hosted-analytics.md).

Tracker is gated by `VITE_UMAMI_WEBSITE_ID` + `VITE_UMAMI_SCRIPT_URL`
build env (per the same Vite-ARG-inlined pattern Sentry uses); both
empty → no script tag, no network call. Privacy posture: no cookies,
respects DNT, no PII.

## Things deferred

- Backend Session Replay — only the frontend can ship replays today.
- A dedicated alerts policy — alert routes live in Sentry's UI, not
  in repo config.

## References

- Backend init: `backend/app/main.py:23-99`.
- Tunnel route: `backend/app/api/routes/sentry_tunnel.py`.
- Frontend init: `web/src/instrument.ts`, `web/src/main.tsx` (first
  import).
- CI: `.github/workflows/ci.yml` — `web-build` job, sourcemap step.
- Vite gate: `web/vite.config.ts` (`build.sourcemap` on
  `SENTRY_AUTH_TOKEN`).
- Hard invariants: `CLAUDE.md` — Sentry auth token, sourcemaps in
  CI only.
- Notable follow-up PRs: #174 (sourcemap upload moved out of the
  Docker build context — token never enters the image).
- Changelog: `CHANGELOG.md` — "Observability — Sentry on both
  runtimes".
- Related phase: [Phase 13](13-prod-hardening-infra2.md) (digest
  pinning + sourcemap gating ride together as INFRA2-015).
