# Infrastructure Teardown

Scope: Docker, compose, Dockerfiles, CI, deploy, nginx/Apache/TLS, backups, observability.
Date: 2026-05-01.
Existing review IDs covered/extended: INFRA-001..INFRA-006.

This is a single-VPS, no-staging, auto-deploy-from-`main` setup. The user rates infra 3/10. The findings below are the gap between "it stays up most of the time" (true) and "it survives a bad day" (not yet). Findings are ordered Critical -> Low. Existing IDs in `docs/claude-review-issues.md` are extended where relevant.

## Infrastructure Issues

### INFRA2-001: No automated database backup before destructive deploys

Severity: **Critical**

Evidence:

- `.github/workflows/ci.yml:111-125` deploy script: `git reset --hard origin/main` then `docker compose ... up -d --build`. No `pg_dump` step before the rebuild.
- `docs/deployment.md:336-337` ("alembic migrations don't auto-roll-back ... restore from a `pg_dump` taken before the deploy") names manual dumps as the only safety net.
- `deploy/backup.sh` runs only on cron at 03:30 (`docs/deployment.md:365`); a daytime deploy can run up to ~24 h after the last backup.
- The backend container's `command:` runs `alembic upgrade head` on every boot (`docker-compose.prod.yml:87`), so a destructive migration applies automatically with no manual gate.

Impact:

A destructive migration merged to `main` ships to prod within minutes, with the most recent on-disk `pg_dump` potentially up to 24 h stale. Rollback per `docs/deployment.md` is "revert + push, restore from backup if migration was destructive" — both steps lose data committed since the last cron run.

Fix instruction:

Add a pre-deploy `pg_dump` step inside the SSH deploy script before `docker compose up`. Pipe to `/srv/backups/stockmanager/pre-deploy-$(date +%FT%H%M)-$(git rev-parse --short HEAD).sql.gz`, abort the deploy on dump failure, and prune older pre-deploy dumps separately from the nightly retention. Document this in `docs/deployment.md` so manual rollbacks know which artefact to grab.

### INFRA2-002: Deploy has no post-up health gate; CI is green even when prod is broken

Severity: **Critical**

Evidence:

- `.github/workflows/ci.yml:111-125` ends after `docker compose up -d --build` and `docker image prune -f`. There is no `curl -fsS https://parts.matescb.cz/api/health`, no migration-failure detector, no container-state assertion.
- `backend/app/main.py:172-174` `/api/health` returns `{"data":{"status":"ok"}}` unconditionally; it does not touch Postgres or the upload volume. So even a real check from outside would only verify uvicorn started.
- Cross-ref `INFRA-001`. The prod compose has no backend or web healthcheck (`docker-compose.prod.yml:33-117`), so `docker compose up -d` returns success the moment the container starts, regardless of whether `alembic upgrade head` succeeded or uvicorn crashed on import.
- `web` `depends_on: [backend]` (`docker-compose.prod.yml:112-113`) lacks `condition: service_healthy` (no healthcheck to wait on anyway), so nginx happily starts and 502s while the backend is dead.

Impact:

A failed migration, missing env var, or import-time exception leaves prod returning 5xx while GitHub Actions reports a green deploy. Nobody knows until a user reports it. CI's only signal of "the deploy worked" is "ssh-action exited 0".

Fix instruction:

(1) Implement `INFRA-001`'s fix to the `/api/health` endpoint — `SELECT 1` against the DB and `os.access(UPLOAD_DIR, os.W_OK)` against the uploads volume. (2) Add Docker healthchecks to backend and web in `docker-compose.prod.yml`, gated on `curl -fsS http://127.0.0.1:8000/api/health` and `curl -fsS http://127.0.0.1:80/` respectively. (3) Append a final `for i in 1..30; do curl -fsS https://parts.matescb.cz/api/health && break; sleep 2; done; curl -fsS https://parts.matescb.cz/api/health` step to the SSH deploy script that exits non-zero on failure.

### INFRA2-003: No off-host backup; single VPS loss = total data loss

Severity: **Critical**

Evidence:

- `deploy/backup.sh:20` writes only to `/srv/backups/stockmanager/` on the same VPS. No `rsync`, `restic`, `aws s3 cp`, `b2 sync`, or even `rclone` step.
- `docs/deployment.md:355` documents two artefacts (`db-*.sql.gz`, `uploads-*.tar.gz`) with 30-day retention, both local.
- The DigitalOcean / VPS provider's snapshot policy is not part of the repo and is not documented in `docs/deployment.md`.
- Cross-ref `INFRA-006` on backup alerting; this is the orthogonal "where do they live" gap.

Impact:

A VPS-level loss event — provider account compromise, accidental destroy, ransomware on `/srv/`, disk failure faster than a snapshot, root filesystem corruption — wipes both prod and every backup at the same time. RPO becomes "since the last manual `git push`" for source and "infinite" for the database.

Fix instruction:

Add a step to `deploy/backup.sh` that uploads `db-${TS}.sql.gz` and `uploads-${TS}.tar.gz` to an off-host destination — encrypted at rest (`age` or `gpg --symmetric` with a key escrowed separately), with object-lock or versioning to defeat compromised-host deletes. Set retention on the remote at >= 30 days. Document the bucket/credential rotation in `docs/deployment.md`. Verify quarterly with a restore drill against a throwaway docker-compose stack.

### INFRA2-004: `WORKSPACE_SECRETS_KEY` is not passed to the backend container

Severity: **Critical**

Evidence:

- `docker-compose.prod.yml:36-52` backend `environment:` block omits `WORKSPACE_SECRETS_KEY`.
- `deploy/.env.prod.example:43` defines the variable with an empty default.
- `backend/app/core/config.py:39` defaults `WORKSPACE_SECRETS_KEY: str = ""`.
- `backend/app/core/secrets.py:40-63` falls back to a hard-coded `_DEV_DEFAULT_KEY = b"OXmO1Y_-zTtTJ_NXxL5RQqGsbwI3wQAOJ-V_M5HH4_o="` (committed in the repo) when the env is empty, logging a one-shot warning.
- Verified with `docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod.example config`: the backend `environment:` block has no `WORKSPACE_SECRETS_KEY` key.
- Cross-ref `SEC-001` — same root cause, infra side is the wiring.

Impact:

Production is currently encrypting workspace provider API keys, provider API secrets, and Scandit license keys with a Fernet key that ships in the public repo. Anyone with access to a DB dump (legitimate backup, replica, leak, the off-host backup added in `INFRA2-003`) can trivially decrypt every workspace's third-party credentials. The mitigation in PR #25 (encryption at rest) is effectively no-op until this env wiring is done.

Fix instruction:

(1) Add `WORKSPACE_SECRETS_KEY: ${WORKSPACE_SECRETS_KEY}` to the backend `environment:` block in `docker-compose.prod.yml`. (2) In `backend/app/core/config.py` (or a startup hook in `app/main.py`), raise on import when `APP_ENV == "prod" and not WORKSPACE_SECRETS_KEY` so a future prod-config drift fails fast instead of silently re-encrypting under the dev key. (3) Generate a fresh Fernet key, set it in `/srv/stockmanager/.env.prod`, and rotate every workspace's provider credentials and scanner license key — they were stored under the dev key and must be assumed compromised.

### INFRA2-005: Sensitive `.env.prod` is interpolated into compose without scoping; password ends up in `DATABASE_URL`

Severity: **High**

Evidence:

- `docker-compose.prod.yml:37` builds `DATABASE_URL` by inline-interpolating `${POSTGRES_PASSWORD}`. Verified with `docker compose ... config`: the rendered backend env contains `DATABASE_URL: postgresql+psycopg://stockmgr:replace-me-...@db:5432/stockmgr`.
- The same value is in `docker inspect` output for the backend container, in the `docker compose config` output any operator runs to debug, and in any `docker events` / `journalctl -u docker` line that mentions container env.
- `docs/deployment.md:286,310` instructs operators to copy-paste `--env-file .env.prod` flags interactively, which renders the same plaintext to `~/.bash_history` if HISTCONTROL doesn't filter it.

Impact:

The Postgres role password is recoverable from `docker inspect`, from any debug `docker compose config` run, from container restart events in syslog, and likely from operator shell history. It is not "at rest" anywhere — it is "alongside the running container metadata", which a non-root operator with `docker` group access can read.

Fix instruction:

Stop interpolating `POSTGRES_PASSWORD` into `DATABASE_URL`. Pass `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST` as separate env vars to the backend (or just `POSTGRES_PASSWORD` plus a fixed-form URL template the app assembles in `config.py`). Document in `docs/deployment.md` that operators must use `docker compose config --no-interpolate` or read the env file directly when debugging, and add `HISTCONTROL=ignorespace` plus a leading space to the documented commands.

### INFRA2-006: Container logs grow unbounded — Docker default JSON driver, no rotation

Severity: **High**

Evidence:

- `docker-compose.prod.yml` sets no `logging:` block on any service. Default is `json-file` with no `max-size` / `max-file`.
- Backend logs are `prod`-formatted JSON-per-line (`backend/app/core/logging.py`) and intend to capture every state-change event — login, logout, build consume, order receive, attachment upload (per the module docstring). On a real production load that's MB/day to GB/year.
- nginx access logs go to stdout from the `nginx:alpine` runtime stage and are similarly uncapped.
- The deploy SSH user is in the `docker` group; `/var/lib/docker/containers/<id>/<id>-json.log` lives on the VPS root filesystem.

Impact:

A noisy week — 5xx spike, log-injecting client, broken loop logging on every request — fills `/var/lib/docker/`, which on a small VPS shares the root filesystem. Docker becomes unable to write logs, the kernel may EIO into containers, and `docker compose up` during the auto-deploy can fail mid-rebuild. Recovery requires SSH and manual log truncation.

Fix instruction:

Add `logging: {driver: json-file, options: {max-size: "10m", max-file: "5"}}` to all three services in `docker-compose.prod.yml`. Better, set the same defaults in `/etc/docker/daemon.json` so they apply repo-wide and any future side-car container inherits them. Aim for 50 MB ceiling per container; bump per service if real traffic justifies more.

### INFRA2-007: Apache vhost has no HTTPS hardening committed; certbot edits are not in version control

Severity: **High**

Evidence:

- `deploy/parts.matescb.cz.conf` defines only the `:80` vhost. It has no `Header always set Strict-Transport-Security`, no `Header always set X-Content-Type-Options`, no `Header always set Referrer-Policy`, no `SSLProtocol`, no `SSLCipherSuite`.
- `docs/deployment.md:182-185` notes that certbot writes `…-le-ssl.conf` and that file is "owned by certbot's renewal flow, not this repo".
- That means HSTS, modern-TLS-only ciphers, OCSP stapling, and security headers are governed by whatever the host's `/etc/letsencrypt/options-ssl-apache.conf` happens to be — invisible to code review and unchanged across deploys.
- nginx in the web container (`deploy/nginx-web.conf`) sets none of these headers either, on the assumption Apache will. So if Apache stops setting them (config drift, certbot template change, vhost reorder) the SPA goes naked.

Impact:

(1) HSTS may not be set, so a downgrade attack on first visit (or an attacker-controlled WiFi) can MITM the login. (2) X-Content-Type-Options absence makes `INFRA-003`'s SVG-as-image risk worse. (3) TLS protocol/cipher policy is unaudited; if certbot ships TLS 1.0/1.1 enabled by default after a system upgrade, no PR shows it. (4) Cookie `secure` flag depends on `APP_ENV=="prod"` (per `CLAUDE.md`) but won't help if HSTS isn't asserting the browser must use HTTPS.

Fix instruction:

Commit the canonical `:443` vhost (with HSTS, `SSLProtocol -all +TLSv1.2 +TLSv1.3`, modern `SSLCipherSuite`, and the security header set) under `deploy/parts.matescb.cz-le-ssl.conf` and document that operators must `cp` it over the certbot-generated file post-issuance. Mirror the same security headers in `deploy/nginx-web.conf` so the defence is layered. Add `Header always set Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"` and submit to the HSTS preload list.

### INFRA2-008: Backend Dockerfile leaves build toolchain in the runtime image; no multi-stage; runtime is not minimal

Severity: **High**

Evidence:

- `backend/Dockerfile:15-20` installs `build-essential`, `libpq-dev`, `curl`, `gosu` and never removes them. Image is single-stage.
- `backend/Dockerfile:22-23` runs `pip install -e .` from `pyproject.toml` directly, with no separate "deps" layer cached against `pyproject.toml` changes — `COPY . .` after means any source change invalidates the entire dep install. Cross-ref `INFRA-004` (no lockfile).
- Image runs from `python:3.12-slim` plus the toolchain on top. Likely 800MB+ when 250MB is achievable.
- No `pip install --no-deps` step, no `--require-hashes`, no SBOM generation.

Impact:

(1) CVEs in `build-essential` (gcc, binutils) and `libpq-dev` follow the prod image around forever, even though they're only needed for `psycopg` C extension build. (2) Image bloat slows the daily `docker compose up --build` rebuild and chews disk on the VPS — relevant given there's no log-rotation cap (`INFRA2-006`). (3) Slow rebuild widens the deploy outage window per `docs/deployment.md:53` ("~5–10 s window where Apache returns 502"). (4) `docker image prune -f` can't reclaim the bloat between deploys because each successful build is the new "current".

Fix instruction:

Convert `backend/Dockerfile` to multi-stage: `FROM python:3.12 AS builder` installs `build-essential libpq-dev`, runs `pip wheel --wheel-dir=/wheels -r requirements.lock`; final `FROM python:3.12-slim` installs only `libpq5 gosu curl` runtime libs and `pip install --no-index --find-links=/wheels`. Move to a hash-pinned lockfile (`uv pip compile` or `pip-tools`) per `INFRA-004`. Target image size <300MB.

### INFRA2-009: Compose has no resource limits; one bad request can OOM the whole VPS

Severity: **High**

Evidence:

- `docker-compose.prod.yml` declares no `mem_limit`, `cpus`, `pids_limit`, or `deploy.resources.limits` on any of `db`, `backend`, `web`.
- `BE-001` / `BE-003` (concurrency races) and `SEC-007` (unbounded BOM base64) all increase the chance of a memory-blow-up scenario in normal operation.
- The VPS hosts other vhosts per `docs/deployment.md:74-77` ("other vhosts (icicle.cz, odbor.matescb.cz, ...)"). They share the same kernel and the same RAM.

Impact:

A 100MB BOM upload (`SEC-007`), a runaway query plan, or a Sentry-tunnel forwarding loop can balloon the backend Python process until the kernel OOM-killer fires. Without resource limits, the OOM-killer picks the largest target — frequently `postgres` — and takes down both stockmanager *and* the unrelated vhosts that share the VPS. There's no isolation budget between tenants.

Fix instruction:

Set `mem_limit: 1g`, `cpus: "1.5"`, `pids_limit: 512` on `backend`; `mem_limit: 2g` on `db`; `mem_limit: 256m` on `web`. Tune from observed peaks. Add `oom_kill_disable: false` explicitly. This buys per-container OOM behaviour and makes the kernel kill *only* the offending container, not its neighbours on the host.

### INFRA2-010: Sentry build token persists in Docker layer cache on the VPS

Severity: **High**

Evidence:

- `docker-compose.prod.yml:108` passes `SENTRY_AUTH_TOKEN: ${SENTRY_AUTH_TOKEN:-}` as a build `args` value.
- `web/Dockerfile.prod:31,38` accepts `ARG SENTRY_AUTH_TOKEN=` then re-exports `ENV SENTRY_AUTH_TOKEN=$SENTRY_AUTH_TOKEN` in the *build* stage. Even though only `dist/` is copied to the runtime stage, the build stage's layers stay in the local builder cache.
- `docker image prune -f` (`.github/workflows/ci.yml:125`) prunes *dangling* images, not the buildkit cache — token-bearing layers persist in `/var/lib/docker/buildkit/` until manually purged.
- `docker history` on a cached intermediate (or any operator shelling in with `docker` group access) reveals the token.
- Cross-ref `INFRA-003` — the issue is acknowledged in claude-review-issues but not yet fixed.

Impact:

The Sentry auth token has source-map upload + project-write permissions. A snapshot of the VPS, a `docker save` for offline analysis, or any operator with docker-group membership recovers the token. Rotation is not part of any documented runbook.

Fix instruction:

Move source-map upload to CI (run `npx @sentry/wizard` or call `sentry-cli` after `npm run build` in the `web-build` job, with the token from a GitHub-Actions secret — never a build arg into Docker). Or use BuildKit secrets: `RUN --mount=type=secret,id=sentry_token` in the Dockerfile and `secrets:` block in compose. Either way, drop `ARG SENTRY_AUTH_TOKEN` and `ENV SENTRY_AUTH_TOKEN` from `web/Dockerfile.prod`.

### INFRA2-011: Backend Dockerfile leaves USER unset; runs as root through the chown shim

Severity: **High**

Evidence:

- `backend/Dockerfile:27-35` says "USER is intentionally NOT set here" with the rationale that compose's `command:` runs `chown ... && exec gosu appuser ...` (`docker-compose.prod.yml:87`).
- The chown-then-gosu pattern works only when the compose `command:` is the actual runtime entrypoint. The Dockerfile's default `CMD` (`backend/Dockerfile:35`) does include the chown+gosu shim, so direct `docker run` is also covered — but a future contributor running ad-hoc `docker compose run backend bash` ends up as root inside the container.
- More structurally: this only drops privileges *after* root has already run an arbitrary `chown -R` against a host-bind-mounted path. If `UPLOAD_DIR` is ever pointed at an unintended host path (operator typo, future bind-mount config drift), root in the container will recursively chown a host directory to UID 1000 before dropping. There's no defensive whitelist.
- `cap_drop`, `read_only`, `security_opt: [no-new-privileges]` are not set on any service in `docker-compose.prod.yml`.

Impact:

(1) Container escapes a privilege check away from root on the VPS — defence in depth is one less layer than it should be. (2) Ad-hoc shells end up as root and risk poisoning the bind-mounted `uploads` volume's ownership. (3) No `read_only` root filesystem means a write-anywhere RCE has the run of `/`.

Fix instruction:

Move the chown to a one-time `docker volume`-init job (e.g. a separate compose service that runs once with `restart: no`), then set `USER appuser` in the Dockerfile's runtime stage and drop the gosu trampoline. Add to each service: `security_opt: [no-new-privileges:true]`, `read_only: true` (with explicit `tmpfs:` for `/tmp` and the upload dir), `cap_drop: [ALL]`, `cap_add: [CHOWN, NET_BIND_SERVICE]` only as needed. Verify with `docker inspect <ctr> | jq '.[0].HostConfig.{ReadonlyRootfs,SecurityOpt,CapDrop,CapAdd}'`.

### INFRA2-012: CI does not validate prod compose or build prod images

Severity: **High**

Evidence:

- `.github/workflows/ci.yml` has `backend-tests` (pytest) and `web-build` (`npm ci && npm run build` against the dev Vite config). No job runs `docker compose -f docker-compose.prod.yml config`, no job runs `docker build -f web/Dockerfile.prod`, no job runs `docker build -f backend/Dockerfile`.
- A typo in `docker-compose.prod.yml` (e.g. the `command:` JSON-array breakage flagged in `CLAUDE.md` — already happened once) is caught only at deploy time on the VPS.
- A breaking change in `web/Dockerfile.prod` — say, a `COPY` path that doesn't exist — first surfaces as a failed build during the SSH deploy, with `git reset --hard origin/main` already done.
- Cross-ref `INFRA-005` — same finding, kept here for the extended scope (prod nginx config validation, `docker compose config` parse).

Impact:

CI's "green" signal does not actually test the artefact that ships. The deploy job is the integration test, and its failure mode is "rolled-back filesystem on VPS, half-broken containers, nobody notices because there's no health gate (`INFRA2-002`)".

Fix instruction:

Add a `prod-validate` job to `.github/workflows/ci.yml` that: (1) `cp deploy/.env.prod.example .env.prod.ci`, (2) `docker compose -f docker-compose.prod.yml --env-file .env.prod.ci config -q`, (3) `docker buildx build --load -f backend/Dockerfile backend/`, (4) `docker buildx build --load -f web/Dockerfile.prod .`, (5) `nginx -t -c <(envsubst < deploy/nginx-web.conf)` to lint the nginx config, (6) optionally start the stack with `docker compose up -d` and `curl -fsS` `/api/health` then teardown. Make the deploy job depend on it.

### INFRA2-013: Workflow `permissions:` block is repo-wide-read but per-job overrides aren't restated

Severity: **Medium**

Evidence:

- `.github/workflows/ci.yml:19-20` sets `permissions: contents: read`. Good — that's the dangerous default fixed.
- However, `appleboy/ssh-action@…` (`ci.yml:103`) takes a private SSH key from `${{ secrets.DEPLOY_SSH_KEY }}` — that secret has full shell access to the VPS as the `deploy` user, who is in the `docker` group, which is functionally root. There's no environment protection rule (`environment: production` is set per `ci.yml:99`, but no required-reviewer is documented as actually configured; `docs/deployment.md:250-264` lists this as "Optional").
- A compromised `actions/checkout` SHA, a malicious dependency in the test path, or a maintainer typo in the workflow YAML (e.g. `pull_request_target` instead of `pull_request`) gives an attacker the ability to deploy arbitrary code to prod. SHA-pinning of third-party actions (`.github/workflows/ci.yml:45,47,68,70,103`) helps; it does not eliminate the risk.

Impact:

The single most powerful credential in the system — VPS root-equivalent SSH — is gated only on "merge to main passes CI". Any CI compromise == full prod compromise.

Fix instruction:

Configure the GitHub-side `production` environment with required reviewers (per `docs/deployment.md:250-264`) and **make it not optional**. Or split: keep auto-deploy for `web-only` changes (no migration) and require human approval when `backend/alembic/versions/` is touched. Add `permissions:` overrides on the `deploy` job to be explicit (`contents: read`, no other scopes). Consider rotating the deploy SSH key on a schedule.

### INFRA2-014: Single uvicorn worker correctly documented but no graceful-shutdown / preload story

Severity: **Medium**

Evidence:

- `docker-compose.prod.yml:87` runs `uvicorn ... --workers 1`. The `--workers 1` choice is correctly justified in the comment block — `slowapi`'s in-memory bucket is per-process. Good and intentional.
- However: no `--timeout-graceful-shutdown` is set. uvicorn defaults to 30s but compose sends `SIGTERM` then `SIGKILL` after the container's stop_grace_period, which defaults to 10s — shorter than uvicorn's graceful timeout, so in-flight requests get cut.
- `docs/deployment.md:53-56` acknowledges "~5-10s window where Apache returns 502 while uvicorn comes back up" but there's no mitigation (zero-downtime rollout, `proxy_next_upstream` on the Apache side, or even a `Retry-After` story).
- No `--preload`-equivalent (uvicorn doesn't have one; gunicorn does). On a `--reload`-disabled prod the boot is single-shot, but a slow `alembic upgrade head` (large migration) holds the deploy in 502 the whole time with no way to surface that to the deploy script.

Impact:

Every deploy 502s real users for ~10s, and a slow migration extends that arbitrarily without warning. In-flight POSTs at the moment of redeploy can be cut between request and response ("did my receive go through?"). No safety net for long migrations.

Fix instruction:

(1) Set `stop_grace_period: 30s` on the backend service so uvicorn's graceful shutdown actually completes. (2) In the Apache vhost, set `ProxyPass / http://127.0.0.1:8091/ retry=2 timeout=30 connectiontimeout=5` plus `Header always set Retry-After "10"` on 502 responses to keep clients from giving up. (3) For destructive migrations, document a "drain mode" runbook: pre-deploy `cp deploy/maintenance.html /var/www/html/maintenance.html` + an Apache rewrite rule, run migration interactively, then re-enable.

### INFRA2-015: `web/Dockerfile.prod` ships sourcemaps in the build cache; runtime nginx serves no security headers

Severity: **Medium**

Evidence:

- `web/Dockerfile.prod:57-58` deletes `*.map` files from the runtime stage — good. But the build stage's `dist/*.map` is in the buildkit cache (same risk surface as `INFRA2-010`).
- `deploy/nginx-web.conf` sets no security response headers: no `X-Content-Type-Options: nosniff`, no `Referrer-Policy`, no `Content-Security-Policy`, no `X-Frame-Options: DENY`, no `Permissions-Policy`. Cross-ref `SEC-003` (provider-asset SVGs) — `nosniff` is the second line of defence and it's missing.
- The nginx image is `nginx:alpine` (good, small), but base-image pinning is by tag, not digest (`web/Dockerfile.prod:46`). A future Alpine CVE/regression slips in on the next rebuild with no PR.

Impact:

(1) No CSP — XSS via `SEC-003` (or any future flaw) gets full DOM access including `document.cookie` and the Sentry tunnel. (2) No `nosniff` — IE/Edge legacy MIME-sniffing fallback can interpret a non-image asset as scripts. (3) No `X-Frame-Options` — clickjacking against any logged-in user. (4) `nginx:alpine` un-digested means the image content is moving under the build cache and CVE assessment is impossible without rebuilding.

Fix instruction:

Add to `deploy/nginx-web.conf` a top-level `add_header X-Content-Type-Options "nosniff" always;`, `add_header Referrer-Policy "strict-origin-when-cross-origin" always;`, `add_header X-Frame-Options "DENY" always;`, `add_header Permissions-Policy "camera=(self), microphone=()" always;` (the camera self is needed for the scanner), and a tight `add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' https://*.ingest.sentry.io;"` once the SDK origins are inventoried. Pin nginx by digest: `FROM nginx:alpine@sha256:<digest>`.

### INFRA2-016: Backups are not encrypted and not verified

Severity: **Medium**

Evidence:

- `deploy/backup.sh:36-39` writes `db-${TS}.sql.gz` — gzip, no encryption.
- `deploy/backup.sh:46-50` writes `uploads-${TS}.tar.gz` — same.
- The script does no `pg_restore --list` smoke-test, no `gunzip -t` integrity check, no size-floor sanity check, no row-count compare against the source.
- Cross-ref `INFRA-006` (alerting depends on cron mail) — extends with the actually-restorable question.

Impact:

(1) When `INFRA2-003`'s off-host upload is added, plaintext customer data goes to a third-party object store. (2) Silent corruption in a `pg_dump` (truncated due to OOM, partial pipe, broken connection) is invisible until the day it's needed for restore.

Fix instruction:

(1) Pipe the dump through `age -r <recipient-pubkey>` (or `gpg --symmetric --cipher-algo AES256 --batch --passphrase-file ...`) before writing to disk. Escrow the recipient key in a separate place from the VPS. (2) Add a verification step: `gunzip -t db-${TS}.sql.gz`, plus a daily `pg_restore --list` against a tmpfs-backed Postgres to prove the dump is replayable. (3) Add a size-floor check (`[ $(stat -c%s ...) -gt 1000000 ]`) so a truncated 0-byte dump fails the script.

### INFRA2-017: Dev compose ships uvicorn `--reload` and is documented but enforcement that prod uses prod compose is operator-trust

Severity: **Medium**

Evidence:

- `docker-compose.yml:38` runs `uvicorn ... --reload` and bind-mounts `./backend:/app`.
- `docs/deployment.md:15-17` warns "The dev compose (`docker-compose.yml`) is unsuitable for production: it ships uvicorn `--reload`, the Vite dev server, and a placeholder session secret".
- Nothing prevents an operator from running `docker compose up -d` (default file = `docker-compose.yml`) on the VPS by reflex. The CI deploy script correctly uses `-f docker-compose.prod.yml`, but `docs/deployment.md`'s manual rollback instructions (`docs/deployment.md:326-332`) also use `-f docker-compose.prod.yml` — discipline, not enforcement.
- The dev compose `docker-compose.yml:24` falls back `SESSION_SECRET: ${SESSION_SECRET:-dev-secret-change-me}`. If invoked on prod by accident, sessions are signed with `dev-secret-change-me` — trivially forgeable.

Impact:

A deploy operator under stress (3am rollback) types `docker compose up -d` from muscle memory, and the VPS now runs the dev stack with a known SESSION_SECRET. The risk is "operator confusion under pressure", not "a bug ships".

Fix instruction:

Rename `docker-compose.yml` to `docker-compose.dev.yml` and add a `Makefile`/`bin/` target (`make dev-up`, `make prod-up`) so the file selection is explicit. Or drop the fallback `${SESSION_SECRET:-dev-secret-change-me}` and instead require `SESSION_SECRET` be set (compose will fail with a clear error if unset). Either way, make accidentally running dev on prod fail loud.

### INFRA2-018: `docker-compose.prod.yml` `web` service `depends_on` lacks `condition`

Severity: **Medium**

Evidence:

- `docker-compose.prod.yml:112-113` declares `depends_on: [backend]` for the web service. No `condition: service_healthy`.
- Combined with no backend healthcheck (`INFRA-001`), the web container can start, accept HTTP, and proxy `/api/*` to a backend that hasn't bound port 8000 yet → 502 immediately on deploy until the backend's `alembic upgrade head` finishes.
- Cross-ref `INFRA2-014` (graceful shutdown) — same window, different cause.

Impact:

The deploy outage window is wider than necessary. Apache can't help — the web container is already up, so its health check (if any) reports healthy.

Fix instruction:

Once `INFRA-001`'s backend healthcheck is in place, change to `depends_on: backend: {condition: service_healthy}` so docker-compose actually waits. Apache will keep serving the previous web container until docker swaps them (compose does swap-on-up by default in v2).

### INFRA2-019: nginx `proxy_read_timeout 60s` is too short for BOM imports / scan imports

Severity: **Medium**

Evidence:

- `deploy/nginx-web.conf:28` sets `proxy_read_timeout 60s` on the `/api/` location.
- BOM import endpoints (BE) operate on a base64 payload that can encode tens of thousands of rows, with per-row DB writes and provider lookups. `SEC-007` documents the unbounded payload concern; even with a sensible cap, a 5000-row BOM will easily exceed 60s on a small VPS.
- The user's `CHANGELOG.md`-flagged scan-to-import flow is similarly long-tailed.
- Apache's vhost (`deploy/parts.matescb.cz.conf:23`) also has no `ProxyTimeout` directive, defaulting to 60s — same problem one layer up.

Impact:

A medium-sized BOM upload returns 504 Gateway Timeout from nginx (or Apache) while the backend is still happily writing rows. The user doesn't know whether to retry (creating duplicate work) or wait. State is half-applied because there's no idempotency token at the route layer either.

Fix instruction:

Bump `proxy_read_timeout` and Apache's `ProxyTimeout` to 5m for `/api/parts/bom/*` and `/api/scan/*` paths specifically, leave 60s on everything else (use a dedicated `location /api/parts/bom/` block in nginx). Pair with `SEC-007`'s row-cap fix so timeouts can't be used as a "block until OOM" attack.

### INFRA2-020: TLS cert renewal hook does not reload the docker-compose stack; not an issue today, latent

Severity: **Low**

Evidence:

- `docs/deployment.md:191-193` — certbot renewal is automated by the systemd timer that ships with the Debian package.
- The renewal modifies `/etc/letsencrypt/live/parts.matescb.cz/`. Apache reads it via `SSLCertificateFile` and reloads automatically (Debian's certbot ships an Apache deploy hook).
- The docker-compose stack does not consume the cert (TLS termination is at Apache), so this is fine — but the comment in `deploy/parts.matescb.cz.conf:11` calls out that the `…-le-ssl.conf` file is auto-managed and not in the repo. If someone later moves TLS into nginx-in-the-web-container, the renewal hook will not restart it.

Impact:

Latent — only matters if TLS termination is ever moved into the docker stack. Calling it out so a future migration doesn't ship without a renewal hook.

Fix instruction:

If/when TLS moves inside docker, write a certbot deploy-hook at `/etc/letsencrypt/renewal-hooks/deploy/stockmanager-reload.sh` that runs `docker compose -f /srv/stockmanager/docker-compose.prod.yml exec web nginx -s reload`. Test with `certbot renew --dry-run --deploy-hook ...`.

### INFRA2-021: No external uptime monitoring; outages are detected by users

Severity: **Low**

Evidence:

- The repo has Sentry for error reporting (`backend/app/main.py:50-73`). Sentry catches errors that the running process itself reports — it does not catch "the process is dead", "the VPS is unreachable", or "TLS expired".
- `docs/deployment.md` documents no external monitor — no UptimeRobot, no BetterUptime, no Pingdom, no Apache-side `mod_status` ping target.
- `INFRA-006` (backup alerting) and `INFRA2-002` (deploy health gate) cover internal signals; this is the missing external one.

Impact:

A VPS-level outage (host reboot, Apache crash, network egress block, DNS misconfiguration, expired TLS for a different vhost taking down the SSL listener) is detected by the first user who reports it. RTO starts at "user noticed and pinged the operator", not "a robot paged the operator".

Fix instruction:

Add an external HTTPS uptime check against `https://parts.matescb.cz/api/health` from a free tier (UptimeRobot, BetterUptime, Healthchecks.io). 5-minute interval, alert via email or push. Document the URL + login in `docs/deployment.md`.

### INFRA2-022: No documented secret-rotation runbook; rotation cadence undefined

Severity: **Low**

Evidence:

- `docs/deployment.md:312-318` documents *how* to change env vars; it does not say *when* to.
- `SESSION_SECRET`, `POSTGRES_PASSWORD`, `WORKSPACE_SECRETS_KEY`, `SENTRY_AUTH_TOKEN`, `SENTRY_DSN`, `VITE_SENTRY_DSN`, and the `DEPLOY_SSH_KEY` (in GitHub secrets) all live forever unless an incident forces a rotation.
- `WORKSPACE_SECRETS_KEY` rotation is especially ugly — losing it makes every encrypted credential unrecoverable (see `backend/app/core/secrets.py:25`). The path is "decrypt-with-old, re-encrypt-with-new, in a maintenance window" and that runbook does not exist.

Impact:

When (not if) one of these is leaked — a stale operator account, a compromised laptop, a contractor who left — there is no playbook. Under pressure operators rotate the wrong thing or break the app rotating the right thing.

Fix instruction:

Add a `docs/runbooks/secret-rotation.md` with one section per secret: how to generate a new one, where to put it, what process needs restart, what user-visible side-effect is (e.g. SESSION_SECRET rotation logs everyone out). For `WORKSPACE_SECRETS_KEY`, document the dual-key rotation: temporarily accept old + new, re-encrypt batch-by-batch, drop old. Adopt a rotation cadence — annual for app secrets, immediately on operator role change for SSH/CI secrets.

## Coverage gaps

- `docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod.example config` ran successfully and confirmed the rendered backend env (no `WORKSPACE_SECRETS_KEY`, plaintext password in `DATABASE_URL`).
- The actual `/srv/stockmanager/.env.prod` on the VPS is not in this repo and was not inspected — file mode (chmod 600), ownership (`deploy:deploy`), and the live `WORKSPACE_SECRETS_KEY` value are taken from `docs/deployment.md` claims, not verified.
- `backend/Dockerfile` and `web/Dockerfile.prod` were read but **not built** — image size estimates are extrapolations from the apt-get / npm install lines, not measured.
- The certbot-generated `…-le-ssl.conf` companion file is intentionally not in the repo (`docs/deployment.md:91-94`); its TLS protocol/cipher/HSTS posture was not auditable from this teardown — only the lack of a committed canonical version was. `INFRA2-007`'s severity assumes worst-case (default Debian options); could be one rung lower if the live config turns out to be hardened.
- The host Apache vhost is not the one in `deploy/parts.matescb.cz.conf` after first certbot run (`docs/deployment.md:182-184`); the live `:443` config was not inspected.
- Backup script execution was not test-run — `pg_dump` failure modes (e.g. on a replicated read-only standby) are inferred.
- GitHub repo-side configuration (branch protection rules, required reviewers on the `production` environment, secret scanning, Dependabot config) was not inspected — no API access from this teardown.
- VPS provider snapshot policy (DigitalOcean / Hetzner / etc.) is outside the repo and was not assessed — `INFRA2-003` assumes no provider-side snapshot exists, which may be wrong but cannot be checked from here.
