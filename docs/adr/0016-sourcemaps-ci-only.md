# ADR-0016: Sourcemaps emitted in CI only

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

Vite can emit sourcemaps in three modes: `false` (none), `true` (visible — `//# sourceMappingURL=…` comment in the JS), and `"hidden"` (emitted to disk, no inline reference). For Sentry to symbolicate stack traces, sourcemaps must exist and be uploaded — but the served bundle must not link to them, otherwise anyone with a browser devtools opens the original source.

The naive setup ("always emit hidden sourcemaps") works for CI but is wasteful on the VPS: every redeploy rebuilds the image, and the build cache fills with `.map` files that the runtime image then has to scrub. Worse, if the scrub regex misses one (e.g. a vendor chunk hash format Vite invents next year), the original source ships to prod.

The cleanest gate is "only emit sourcemaps when the upload step is going to run", which on this stack means "when `SENTRY_AUTH_TOKEN` is set" — and per ADR-0014, the token is only set in CI.

## Decision

`web/vite.config.ts:21-73` reads `SENTRY_AUTH_TOKEN` from the environment. If present, `build.sourcemap` is `"hidden"` (emit but don't reference). If absent, `build.sourcemap` is `false` (don't emit at all). VPS-side builds run without the token, so they produce no `.map` files; the build cache stays clean.

`web/Dockerfile.prod:55` keeps `RUN find /usr/share/nginx/html -name '*.map' -delete` as belt-and-braces against an edge-case local build that ships with sourcemaps anyway (e.g. a developer who set `SENTRY_AUTH_TOKEN` and then ran `docker build` directly).

## Consequences

- **Good**: VPS rebuilds are fast and produce a clean image. CI builds (which have the token) emit hidden sourcemaps that the next CI step uploads to Sentry. The runtime image never serves a `.map` file regardless of how it was built.
- **Trade-offs**: A developer who runs `npm run build` locally with `SENTRY_AUTH_TOKEN` set and then deploys that image directly would emit sourcemaps and ship them — caught by the Dockerfile `find -delete`. The deploy path goes through CI, so this is theoretical.
- **What it forbids**:
  - Don't set `build.sourcemap: "hidden"` unconditionally in `web/vite.config.ts`. Re-introduces the "every VPS build emits sourcemaps" waste.
  - Don't remove the `find -name '*.map' -delete` line in `web/Dockerfile.prod`. It's the belt-and-braces.
  - Don't switch `"hidden"` to `true` (visible). The `//# sourceMappingURL` comment would land in the served JS even with the file deleted, producing 404s in the browser console (and, worse, exposing the path scheme).
  - Don't emit sourcemaps without uploading them. The token's presence is the gate.

## Alternatives considered

- **Always emit hidden sourcemaps, always run the scrub** — rejected because the scrub is a denylist that depends on Vite's filename conventions; a future Vite change could produce a `.map`-less filename and bypass it.
- **Build sourcemaps to a separate `dist-maps/` directory and never copy it into the runtime image** — viable, but more configuration than the env-gate. The `SENTRY_AUTH_TOKEN`-presence gate doubles as documentation: "if you have the upload token, you wanted maps; otherwise you didn't".

## References

- Source: `web/vite.config.ts:17-73`
- Source: `web/Dockerfile.prod:48-55` (the belt-and-braces find)
- Rule: `CLAUDE.md:176-180` (INFRA2-015)
- Related: ADR-0014 (auth token out of build context)
