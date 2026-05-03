# ADR-0014: Sentry auth token must not enter Docker build context

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

Sentry source-map upload (`@sentry/cli sourcemaps upload`) needs `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`. The straightforward Docker pattern is to pass these as build ARGs into `web/Dockerfile.prod`, run the upload during `npm run build`, and forget about it.

That pattern has two problems. First, build ARGs land in the image's layer history — `docker history --no-trunc` exposes them, even if the runtime `ENV` is unset. Second, the VPS build path runs the same Dockerfile (this is a single-host deploy, no separate "CI image"). If the prod build step has the token, every VPS-side rebuild needs the token present in the deploy environment, broadening the secret surface to anyone with VPS access.

The auth token grants source-map upload (and, depending on the token's scopes, more). It belongs in CI, not on the VPS.

## Decision

Source-map upload is a CI step, not a Dockerfile step. The `web-build` job in `.github/workflows/ci.yml:285-298` runs `npx @sentry/cli sourcemaps upload` after `npm run build`, gated on push to `main`. `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, and `SENTRY_PROJECT` are GitHub Actions repository secrets.

`web/Dockerfile.prod` does **not** declare ARGs or ENVs for `SENTRY_AUTH_TOKEN` / `SENTRY_ORG` / `SENTRY_PROJECT`. `docker-compose.prod.yml` does **not** pass them as build args. The only Sentry-related ARGs in the Dockerfile are `VITE_SENTRY_DSN` and `VITE_SENTRY_TRACES_SAMPLE_RATE` (`web/Dockerfile.prod:25-32`) — the public DSN and sample rate, which the runtime SDK needs and which are not secret.

## Consequences

- **Good**: The token never touches the VPS or any image layer. Source-map upload happens once per `main` merge from CI, where the secret already lives. VPS rebuilds (e.g. on dependency change) produce a working image without the token; sourcemaps just aren't uploaded for that build, which is correct because that build wasn't the one the deployed code is correlated with.
- **Trade-offs**: A local `npm run build` on a developer machine with `SENTRY_AUTH_TOKEN` set will upload sourcemaps to Sentry — see ADR-0016 for the gate on `build.sourcemap` itself. The CI-only upload path means a failed CI run misses sourcemaps for that release.
- **What it forbids**:
  - Don't add `ARG SENTRY_AUTH_TOKEN`, `ARG SENTRY_ORG`, or `ARG SENTRY_PROJECT` to `web/Dockerfile.prod`.
  - Don't set `SENTRY_AUTH_TOKEN`/`SENTRY_ORG`/`SENTRY_PROJECT` as build args under `services.web.build.args` in `docker-compose.prod.yml`.
  - Don't move source-map upload into a Dockerfile `RUN`. It is a CI job by design.
  - Don't put the token in `.env.prod` on the VPS.

## Alternatives considered

- **BuildKit secrets (`--secret id=sentry,src=…`)** — viable, doesn't land in layer history. Rejected because the secret would still need to live on the VPS for VPS-side builds, broadening the secret surface beyond CI.
- **Upload sourcemaps from the runtime container at startup** — rejected because the runtime image must not contain the token either, and shipping it via env at runtime makes it visible to `docker inspect`.

## References

- Source: `.github/workflows/ci.yml:285-298` (CI sourcemap upload)
- Source: `web/Dockerfile.prod:25-34` (the ARGs that ARE allowed; comment says token is NOT passed)
- Rule: `CLAUDE.md:164-169` (INFRA2-010)
- Related: ADR-0016 (sourcemap emission gate)
