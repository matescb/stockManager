# Phase 13 — Prod hardening (INFRA2-* wave)

Audience: engineer

The production-deployment hardening sweep: container runs as a
non-root user, base images are digest-pinned, the dev-time
`gosu`/`chown` boot path becomes a one-shot init service, off-host
encrypted backups land via `vps-backup`, and CI gains an `npm audit`
gate alongside the existing `pip-audit`.

## Why

- The first prod deploy revealed a stack of footguns that local-dev
  Docker glosses over: root-in-container, mutable base-image tags,
  on-host backups, secrets bleeding into build args, the
  YAML-folded-scalar trap that bucketed every client by the docker
  bridge IP. Each item below was a real prod incident or a near-miss
  found in code review.
- Backups had to leave the host (CRIT — single-disk failure on the
  VPS would erase the only copy) and had to be encrypted at rest on
  the destination NAS.

## What shipped

- **Container runs as `appuser` UID 1000** —
  `backend/Dockerfile` sets `USER appuser` in the runtime stage. The
  one-shot **`backend-init`** service (`docker-compose.prod.yml`)
  runs as root with `command: ["sh","-c","chown -R 1000:1000
  /data"]`, `restart: no`, fixing ownership of the `uploads` named
  volume before exiting cleanly. `backend` declares
  `depends_on: backend-init: condition: service_completed_successfully`.
  This replaced an in-process `gosu` boot. See `CLAUDE.md` — "Things
  that have bitten us — `backend-init` one-shot service".
- **Base images digest-pinned (INFRA2-015)** — `backend/Dockerfile`
  and `web/Dockerfile.prod` use `FROM image@sha256:<digest>`.
  Digests are rotated weekly by Dependabot
  (`.github/dependabot.yml`). Manual bump procedure in
  `docs/deployment.md` — "Base image pinning". Do not loosen to a
  bare tag. See `CLAUDE.md`.
- **Sourcemaps emitted only in CI (INFRA2-015)** —
  `web/vite.config.ts` gates `build.sourcemap` on
  `SENTRY_AUTH_TOKEN` presence; VPS builds skip them entirely. See
  [Phase 12](12-observability-sentry.md).
- **Sentry auth token never enters the Docker build context
  (INFRA2-010)** — source-map upload is CI-only; the token is a
  GitHub Actions secret. `CLAUDE.md` calls this out explicitly.
- **YAML-folded-scalar trap closed** — `docker-compose.prod.yml`'s
  backend `command:` is a single-line JSON-array exec form. Folded
  scalar (`>`) preserved newlines and `--proxy-headers
  --forwarded-allow-ips=*` ran as a separate failing shell command,
  which silently bucketed every client by the docker bridge IP
  under slowapi. See `CLAUDE.md` — "Things that have bitten us".
- **Graceful-shutdown ordering (INFRA2-014)** — uvicorn runs with
  `--timeout-graceful-shutdown 25` against
  `stop_grace_period: 30s`. The 5s headroom prevents Compose's
  SIGKILL during a clean drain. See `CLAUDE.md`.
- **Off-host encrypted backups via `vps-backup` (INFRA2-003)** —
  the project-agnostic [matescb/vps-backup](https://github.com/matescb/vps-backup)
  service runs `pg_dump` + an assets tar through `age -r`, pushes
  the result to a VPSfree NAS over NFS at `/mnt/nas-backups/`, and
  GFS-prunes (14d / 8w / 6m). Local fallback retained 7 days. Cron
  in `/etc/cron.d/vps-backup` at `03:30` daily. Restore drill
  validated end-to-end on 2026-05-02. Pre-deploy `pg_dump` lives in
  CI via `deploy/predeploy-dump.sh` (INFRA2-001). Detailed in
  `docs/deployment.md` — "Backups".
- **CI supply-chain gates** —
  - `pip-audit` (SEC2-016) on `requirements.lock` —
    `.github/workflows/ci.yml:47-66`.
  - `lockfile-drift` (SEC2-016) — `uv lock --check`.
  - **`npm audit`** gate on the web bundle (TODO(verify): exact
    job name in `.github/workflows/ci.yml`).
  - `prod-validate` (INFRA2-012) — `docker compose … config -q`,
    `docker buildx build` of both Dockerfiles, `nginx -t` against
    `deploy/nginx-web.conf`. Runs on every push and PR.

## Invariants introduced

- **Don't reintroduce `gosu` or a root-prefixed `command:` in the
  backend service.** The `backend-init` pattern is what replaced it.
- **Don't loosen the `@sha256:` pin on a base image.** Bump via the
  procedure in `docs/deployment.md` — "Base image pinning".
- **`--timeout-graceful-shutdown` must stay ≥ 5s below
  `stop_grace_period`.**
- **Backups go off-host before they go to NAS.** `vps-backup` writes
  encrypted artefacts; the host never holds the only copy.
- **`SENTRY_AUTH_TOKEN`, NAS keys, and signing material live in
  GitHub Actions secrets or on the VPS, never in the build context.**
  See [`docs/runbooks/secret-rotation.md`](../runbooks/secret-rotation.md).

## Things deferred

- Concurrent-run lock on the backup script (INFRA2-014 sub-item) —
  low priority while cron is the only caller and runs are 24h apart.
- Multi-replica backend (would need a Redis-backed slowapi store —
  see `CLAUDE.md`).
- Staging environment — still none; `main` deploys straight to prod
  after the required-reviewer gate.

## References

- Dockerfiles: `backend/Dockerfile`, `web/Dockerfile.prod`.
- Compose: `docker-compose.prod.yml`.
- CI: `.github/workflows/ci.yml`.
- Deployment doc: `docs/deployment.md` — sections "Base image
  pinning (INFRA2-015)", "Backups", "Header hardening (SEC2-018)",
  "BOM / scan-import timeouts (INFRA2-019)".
- Runbook: `docs/runbooks/secret-rotation.md`.
- External: [matescb/vps-backup](https://github.com/matescb/vps-backup).
- Hard invariants: `CLAUDE.md` — "Things that have bitten us — don't
  undo".
- Related phase: [Phase 12](12-observability-sentry.md).
