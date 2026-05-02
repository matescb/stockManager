# Production deployment

`parts.matescb.cz` runs on a single VPS as a docker-compose stack behind the
host's existing Apache 2.4 reverse proxy, with TLS issued + renewed by
`certbot --apache`. Day-to-day deploys are automated by GitHub Actions. This
document covers:

- [The day-to-day flow](#day-to-day-flow) — how a code change reaches prod.
- [Architecture](#architecture) — what's running where.
- [One-time bootstrap](#one-time-bootstrap) — how the VPS was set up.
- [CI/CD details](#cicd-details) — secrets, jobs, gating.
- [Operations](#operations) — logs, psql, alembic, env changes, rollback.
- [Backups](#backups).
- [Health endpoint](#health-endpoint) — response shapes for `GET /api/health`.

The dev compose (`docker-compose.dev.yml`) is unsuitable for production: it ships
uvicorn `--reload`, the Vite dev server, and will refuse to start without a
`SESSION_SECRET` set in the environment (no insecure default).

## Day-to-day flow

You make a change → push to `main` → it ships. There is no manual deploy.

```
  git push origin main
        │
        ▼
  GitHub Actions: .github/workflows/ci.yml
        ├─ backend-tests   (pytest, postgres:16 service container)
        ├─ web-build       (npm ci && npm run build)
        ├─ prod-validate   (compose config -q + buildx builds + nginx -t)
        └─ deploy          (only if all three ✅, only on push to main)
                  │
                  ▼ ssh deploy@vps
              cd /srv/stockmanager
              git fetch + reset --hard origin/main
              docker compose -f docker-compose.prod.yml \
                  --env-file .env.prod up -d --build
              docker image prune -f
                  │
                  ▼
              backend container starts:
                  alembic upgrade head        ← schema changes ship here
                  uvicorn app.main:app …
              web container is rebuilt fresh from sources.
```

Practical consequences:

- **Pull requests / feature branches** run all three non-deploy jobs
  (`backend-tests`, `web-build`, `prod-validate`). Use them as a pre-merge
  gate; nothing reaches prod until the branch is merged.
- **A new alembic migration** under `backend/alembic/versions/` ships
  automatically — no manual step. The backend container's CMD runs
  `alembic upgrade head` before uvicorn boots, so by the time the new
  workers serve traffic, the schema is at the new revision.
- **Restart window**: `docker compose up --build` recreates containers in
  place. There's a brief window where Apache returns 502 while uvicorn comes
  back up. Three mitigations reduce the impact (INFRA2-014):
  1. `stop_grace_period: 30s` + `--timeout-graceful-shutdown 25` on the
     backend: in-flight requests get up to 25 s to drain before uvicorn
     exits; Compose's 30 s SIGKILL deadline gives a 5 s safety margin.
  2. `ProxyPass … retry=2 timeout=30 connectiontimeout=5` on the Apache
     vhost: Apache retries the backend twice before surfacing the 502, and
     `Retry-After: 10` tells clients to back off rather than hammering.
  3. For long migrations use [Drain mode](#drain-mode-for-destructive-migrations)
     to show users a static maintenance page while the schema changes run.
- **Red CI** keeps prod on the previous version: the deploy job is gated
  on `needs: [backend-tests, web-build, prod-validate]` and won't start if
  any of them failed. GitHub emails on red.
- **Secrets / env changes don't go through CI.** `.env.prod` lives only on
  the VPS; rotating `SESSION_SECRET` or changing `CORS_ORIGINS` is an SSH
  task (see [Operations → Changing env vars](#changing-env-vars)).

## Architecture

```
  internet → :80/:443 (host Apache 2.4)
                       ├─ parts.matescb.cz → 127.0.0.1:8091
                       │                    │
                       │                    ▼
                       │              docker compose:
                       │                ├─ web   (nginx + Vite dist/)
                       │                │   └─ /api/* → backend
                       │                ├─ backend (uvicorn, 2 workers)
                       │                └─ db (postgres:16-alpine)
                       └─ … other vhosts (icicle.cz, odbor.matescb.cz, …)
```

Only the `web` container publishes a host port, and only on loopback. Apache
fronts the public side — TLS, redirects, access logs. The web container's
nginx handles the `/api/*` → backend split internally so Apache only needs
one ProxyPass per app, matching the convention every other vhost on this
VPS already uses.

Files involved (all version-controlled):

- `docker-compose.prod.yml` — service definitions.
- `backend/Dockerfile` — Python 3.12 slim + `pip install -e .`.
- `web/Dockerfile.prod` — multi-stage Vite build → nginx:alpine.
- `deploy/nginx-web.conf` — in-container nginx (SPA routing + `/api/` proxy).
- `deploy/parts.matescb.cz.conf` — canonical Apache vhost. The active file
  on the host (`/etc/apache2/sites-available/parts.matescb.cz.conf`) is a
  copy of this; the certbot-generated `…-le-ssl.conf` companion is **not**
  in the repo (it's owned by the renewal flow).
- `deploy/.env.prod.example` — template for `.env.prod`. The real
  `.env.prod` lives only at `/srv/stockmanager/.env.prod` on the VPS,
  chmod 600, owned by `deploy`.

## One-time bootstrap

This was done once on `vps` (root shell). It does not need to be repeated
unless you're rebuilding the host, or migrating to a fresh VPS.

### Prerequisites on the host

- Docker Engine 24+ with the compose plugin (`docker compose version`).
- Apache 2.4 with `mod_proxy`, `mod_proxy_http`, `mod_rewrite`, `mod_ssl`.
- `certbot` with the `python3-certbot-apache` plugin.
- DNS A record for `parts.matescb.cz` pointing at the VPS (`37.205.15.171`).

### Steps

1. **Create the deploy user with docker access.** CI logs in as this user
   and runs `docker compose up`; nothing else should run as them.

   ```bash
   useradd -m -s /bin/bash deploy
   usermod -aG docker deploy
   install -d -o deploy -g deploy -m 0755 /srv/stockmanager
   ```

2. **Generate the deploy user's SSH keypair.** This single keypair serves
   two purposes:
   - public key in `~/.ssh/authorized_keys` → GitHub Actions can SSH in
   - public key registered as a GitHub **Deploy Key** on the repo →
     `deploy` can `git fetch` from a private repo

   ```bash
   sudo -u deploy install -d -m 0700 /home/deploy/.ssh
   sudo -u deploy ssh-keygen -t ed25519 -f /home/deploy/.ssh/id_ed25519 \
       -N "" -C "github-actions@stockmanager"
   sudo -u deploy bash -c '
       cat /home/deploy/.ssh/id_ed25519.pub >> /home/deploy/.ssh/authorized_keys &&
       chmod 600 /home/deploy/.ssh/authorized_keys &&
       ssh-keyscan -t ed25519 github.com >> /home/deploy/.ssh/known_hosts
   '
   cat /home/deploy/.ssh/id_ed25519.pub
   ```

   Add the printed public key as a Deploy Key at
   <https://github.com/matescb/stockManager/settings/keys/new> (read-only —
   writes happen from inside GitHub, never from the VPS).

3. **Clone the repo over SSH.**

   ```bash
   sudo -u deploy git clone git@github.com:matescb/stockManager.git \
       /srv/stockmanager
   ```

4. **Seed `.env.prod`.** These secrets never enter CI.

   ```bash
   sudo -u deploy cp /srv/stockmanager/deploy/.env.prod.example \
                    /srv/stockmanager/.env.prod
   sudo -u deploy chmod 600 /srv/stockmanager/.env.prod
   # Rotate POSTGRES_PASSWORD (openssl rand -base64 24)
   # and  SESSION_SECRET   (openssl rand -hex 32)
   # CORS_ORIGINS is preset to https://parts.matescb.cz.
   ```

5. **Bring up the stack.** The backend container's entrypoint runs
   `alembic upgrade head`, so a fresh DB migrates itself.

   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml \
       --env-file .env.prod up -d --build
   curl -fsS http://127.0.0.1:8091/api/health
   # 200 → {"data":{"status":"ok","db":"ok","uploads":"ok"},...}
   # 503 → structured body; see ## Health endpoint below
   ```

6. **Add the Apache vhost.**

   ```bash
   cp /srv/stockmanager/deploy/parts.matescb.cz.conf \
      /etc/apache2/sites-available/parts.matescb.cz.conf
   a2ensite parts.matescb.cz
   apache2ctl configtest && systemctl reload apache2
   curl -fsS -H "Host: parts.matescb.cz" http://127.0.0.1/api/health
   # 200 → {"data":{"status":"ok","db":"ok","uploads":"ok"},...}
   # 503 → structured body; see ## Health endpoint below
   ```

7. **Issue the TLS cert.** Certbot edits the :80 vhost in place to add a
   redirect to :443 and writes a sibling `parts.matescb.cz-le-ssl.conf`
   under `/etc/apache2/sites-available/`.

   ```bash
   certbot --apache -d parts.matescb.cz \
       --non-interactive --agree-tos -m matyas.skvor@gmail.com --redirect
   curl -fsS https://parts.matescb.cz/api/health
   # 200 → {"data":{"status":"ok","db":"ok","uploads":"ok"},...}
   # 503 → structured body; see ## Health endpoint below
   ```

   Renewal is automated by the certbot systemd timer that ships with the
   Debian package — verify with `systemctl list-timers | grep certbot`.

8. **Add GitHub Actions secrets** so the deploy job can reach the VPS:

   | Secret                | Value                                                                         |
   |-----------------------|-------------------------------------------------------------------------------|
   | `DEPLOY_HOST`         | `37.205.15.171`                                                               |
   | `DEPLOY_USER`         | `deploy`                                                                      |
   | `DEPLOY_SSH_KEY`      | full contents of `/home/deploy/.ssh/id_ed25519` (the **private** key)         |
   | `SENTRY_AUTH_TOKEN`   | Sentry auth token with `project:write` + `project:releases` scope            |
   | `SENTRY_ORG`          | Sentry organisation slug                                                      |
   | `SENTRY_PROJECT`      | Sentry project slug                                                           |

   Set them at <https://github.com/matescb/stockManager/settings/secrets/actions>.

   **Note (INFRA2-010):** `SENTRY_AUTH_TOKEN` must live only in GitHub Actions
   secrets. It must **not** appear in `.env.prod` or as a Docker build arg —
   doing so would embed it in the layer cache. The sourcemap upload runs in the
   `web-build` CI job, after `npm run build`, gated on push to `main`.

The next push to `main` triggers the first end-to-end automated deploy.

## CI/CD details

`.github/workflows/ci.yml`. Six jobs:

- **`lockfile-drift`** (SEC2-016) — installs `uv`, runs `uv lock --check`
  (fails if `pyproject.toml` diverges from `uv.lock`), and re-exports
  `requirements.lock` to detect stale hashes. Runs on every push and PR,
  before the heavier `backend-tests` job.
- **`pip-audit`** (SEC2-016) — scans `requirements.lock` with
  `pip-audit --require-hashes` for HIGH/CRITICAL CVEs. Depends on
  `lockfile-drift` so it always audits the verified-current set.
- **`backend-tests`** — postgres:16-alpine service container, `pip install -e ".[dev]"`, `pytest -q --tb=short`. Runs on every push and PR.
- **`web-build`** — `npm ci && npm run build`, followed on `push` to `main` by a Sentry sourcemap upload step (`npx @sentry/cli sourcemaps upload`). The build's `tsc -b` step also catches TypeScript errors. Runs on every push and PR; the sourcemap upload is gated on push to `main` only. `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, and `SENTRY_PROJECT` are GitHub Actions secrets used by the upload step — they must **not** appear in `.env.prod` or `docker-compose.prod.yml` build args (INFRA2-010).
- **`prod-validate`** (INFRA2-012) — validates the prod artefacts that the two test jobs above don't exercise: (1) `docker compose -f docker-compose.prod.yml config -q` catches YAML/schema/variable errors in `docker-compose.prod.yml` (including the single-line JSON-array `command:` form that previously broke in production); (2) `docker buildx build` of `backend/Dockerfile` and `web/Dockerfile.prod` catches Dockerfile regressions; (3) `docker run nginx:alpine nginx -t` lints `deploy/nginx-web.conf`. Uses a throw-away CI env file derived from `deploy/.env.prod.example` — no real secrets are required. Runs on every push and PR.
- **`deploy`** — gated on `github.event_name == 'push' && github.ref == 'refs/heads/main'` and `needs: [backend-tests, web-build, prod-validate]`. Uses `appleboy/ssh-action@v1.0.3` to SSH in and run the pull/up/prune script. Concurrency-grouped on `ci-refs/heads/main` with `cancel-in-progress: false` so consecutive pushes queue rather than abort an in-flight `docker compose up --build`.

The deploy script body is intentionally tiny:

```bash
set -euo pipefail
cd /srv/stockmanager
git fetch --quiet origin main
git reset --hard origin/main
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker image prune -f
```

`git reset --hard` (rather than `pull`) means the deploy is always exactly
the tip of `origin/main`, even if some prior failed deploy left the working
tree dirty. `image prune -f` keeps disk usage in check across rebuilds.

If the VPS host key ever rotates (rebuild, key regeneration), pin the new
fingerprint via the action's `fingerprint:` input — capture it on the host
with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.

### Action SHA pins

All third-party actions are SHA-pinned in `ci.yml`, with a comment recording
the human-readable tag the SHA was resolved from. Bump them with:

```bash
gh api repos/actions/checkout/git/refs/tags/v4 --jq .object.sha
gh api repos/actions/setup-python/git/refs/tags/v5 --jq .object.sha
gh api repos/actions/setup-node/git/refs/tags/v4 --jq .object.sha
gh api repos/appleboy/ssh-action/git/refs/tags/v1.0.3 --jq .object.sha
```

Replace the SHA in the `uses:` line; keep the tag in the trailing comment.
Pin-bumps are normal-stream PRs (no special review gate); only bump on a
genuine version intent.

### Required status checks

Even though the `backend-tests`, `web-build`, and `prod-validate` jobs run
on every PR (`pull_request:` trigger), GitHub will still let a contributor
merge a red PR unless branch protection is configured. To make the gate
load-bearing:

1. GitHub UI → Settings → Branches → Branch protection rules → Add rule.
2. Branch name pattern: `main`.
3. Tick **Require status checks to pass before merging** and pick
   `backend-tests`, `web-build`, and `prod-validate` from the list (they
   only appear after their first successful run).
4. Optional: tick **Require branches to be up to date before merging**
   if you want a fresh-rebase requirement on top.

Without this, a PR with a red `backend-tests` check is still mergeable
via the normal GitHub UI — the auto-deploy then ships a known-broken
build to prod. Pin via this rule once and forget. (Recorded as part of
TEST-014 / issue #116; the `tests/test_ci_workflow.py` regression test
asserts the workflow shape but cannot configure repo settings.)

### Gate deploys behind a human reviewer

The `deploy` job has `environment: production` set. This gates every prod
deploy behind GitHub's environment protection rules. **This protection is
part of the shipped configuration** — it is not optional.

To configure (one-time setup if the environment was not already created):

1. GitHub UI → Settings → Environments → New environment → name `production`.
2. Add a **Required reviewers** rule and list the maintainer account
   (`matescb`). Add further accounts if the project gains contributors.
3. Optional but recommended: set a **Wait timer** of 5 minutes so a
   compromised push cannot be self-approved before the maintainer notices.

After this, every push to `main` that passes CI will pause at the deploy
step and email the listed reviewers. Approving the run resumes the SSH
deploy. Rejecting it (or letting it time out) leaves the current prod
containers untouched.

To add a reviewer later: GitHub UI → Settings → Environments →
`production` → Edit → add the account under "Required reviewers".

### SSH key rotation

The `DEPLOY_SSH_KEY` GitHub Actions secret gives the CI runner SSH access
to the VPS as the `deploy` user (who is in the `docker` group). Rotate it
on a schedule (recommended: every 6 months) or immediately after any
suspected credential compromise or contributor offboarding.

1. On the VPS, generate a new key pair:
   ```bash
   ssh-keygen -t ed25519 -f /tmp/deploy_new -N ""
   ```
2. Append the new public key to the authorised keys file:
   ```bash
   cat /tmp/deploy_new.pub >> /home/deploy/.ssh/authorized_keys
   ```
3. Update the GitHub secret:
   GitHub UI → Settings → Secrets and variables → Actions →
   `DEPLOY_SSH_KEY` → Update → paste the contents of `/tmp/deploy_new`
   (the private key).
4. Trigger a deploy (e.g. push a no-op commit to `main`). Verify it
   succeeds end-to-end via the health gate.
5. Remove the **old** public key from `authorized_keys` on the VPS.
6. Shred the temporary key files:
   ```bash
   shred -u /tmp/deploy_new /tmp/deploy_new.pub
   ```

## Operations

- **Secret rotation** — see [`docs/runbooks/secret-rotation.md`](runbooks/secret-rotation.md) for per-secret playbooks, cadence guidance, and the multi-step `WORKSPACE_SECRETS_KEY` dual-key transition procedure.

All commands below assume you're SSH'd into the VPS. If you don't have a
shell alias, `ssh root@37.205.15.171` works. Use `sudo -u deploy` on the
docker-compose commands so they run as the same user CI uses (avoids
"dubious ownership" git warnings and stray root-owned volume files).

### Tail logs

```bash
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml logs -f backend
sudo -u deploy docker compose -f docker-compose.prod.yml logs -f web
```

### psql shell

The `$POSTGRES_USER` / `$POSTGRES_DB` variables must expand **inside the
container**, not in your host shell (where they are almost certainly unset).
Use `sh -c '...'` with single quotes so the shell that receives the command
is the one inside the container, where those variables are already set by
the postgres image:

```bash
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec db sh -c 'exec psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

Or use the helper script (handles the single-quoting and `sudo -u deploy`
for you):

```bash
sudo /srv/stockmanager/deploy/db-shell.sh
```

### Ad-hoc alembic command

The autodeploy already runs `alembic upgrade head` on container start, so
this is for inspection / one-off ops, not normal upgrades.

```bash
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec backend alembic current
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec backend alembic history
```

### Debugging compose env safely

To inspect what environment variables a running container actually received
without printing the raw `.env.prod` file (which contains secrets):

```bash
# Show all env vars in the backend container — safe to read on screen
sudo -u deploy docker inspect stockmanager-backend-1 \
    | jq '.[0].Config.Env'
```

Since INFRA2-005 the backend container receives discrete `POSTGRES_*`
variables and assembles `DATABASE_URL` at runtime.  The password therefore
appears only in `POSTGRES_PASSWORD`, not inside a `DATABASE_URL=…` string.

To preview what compose *would* interpolate into a compose file without
actually starting containers, use `--no-interpolate`:

```bash
# Print the final compose YAML with all variable substitutions applied
sudo -u deploy docker compose -f docker-compose.prod.yml \
    --env-file .env.prod config

# Print the *un-interpolated* compose YAML (shows ${VAR} placeholders)
sudo -u deploy docker compose -f docker-compose.prod.yml config \
    --no-interpolate
```

`--no-interpolate` is useful when you want to audit which variables are
wired without accidentally leaking their values into terminal scroll-back.

### Changing env vars

`.env.prod` is **not** in git and **not** in CI — it lives only at
`/srv/stockmanager/.env.prod`. To change it:

```bash
sudo -u deploy $EDITOR /srv/stockmanager/.env.prod
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    up -d   # recreate affected services in place
```

`SESSION_SECRET` rotation invalidates all existing sessions (everyone gets
logged out). `POSTGRES_PASSWORD` cannot be changed by editing the env file
alone after the `db_data` volume is initialised — you have to `ALTER USER`
inside postgres first, then update the file. `CORS_ORIGINS` and the like
take effect on the next backend restart.

### Rollback

The CI deploy script is `git reset --hard origin/main`, so a rollback is a
revert commit on `main` — push it, and CI redeploys to the rolled-back
state automatically. For an emergency manual rollback when CI is broken:

```bash
sudo -u deploy git -C /srv/stockmanager fetch --quiet origin main
sudo -u deploy git -C /srv/stockmanager reset --hard <known-good-sha>
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    up -d --build
```

Note that **alembic migrations don't auto-roll-back**. If the bad commit
included a destructive migration, restore from a `pg_dump` taken before the
deploy (see [Backups](#backups)). Treat any irreversible migration as a
release-management decision: take a dump first, ship in business hours.

Always use `docker compose -f docker-compose.prod.yml` explicitly on the VPS —
the repo no longer has a bare `docker-compose.yml` default, so muscle-memory
`docker compose up -d` without a `-f` flag will error rather than silently
start the dev stack.

### Wrapper scripts in deploy/

Two helper scripts live in `deploy/` to make the common ops tasks
less error-prone:

| Script | Purpose |
|--------|---------|
| `deploy/db-shell.sh` | Open an interactive `psql` session inside the running `db` container. Reads credentials from `.env.prod` so they never expand in the host shell. |
| `deploy/db-restore.sh` | Decrypt and restore a `pg_dump` backup. Prompts for confirmation before overwriting. |

Both scripts are designed to be run as root from anywhere on the VPS:

```bash
sudo /srv/stockmanager/deploy/db-shell.sh
sudo /srv/stockmanager/deploy/db-restore.sh /path/to/key.txt /path/to/db-YYYY-MM-DD.sql.gz.age
```

### Apache vhost edits

If the canonical template at `deploy/parts.matescb.cz.conf` ever changes,
re-deploy doesn't automatically apply it (Apache config lives under
`/etc/apache2/`, not in the compose stack). Sync it manually:

```bash
cp /srv/stockmanager/deploy/parts.matescb.cz.conf \
   /etc/apache2/sites-available/parts.matescb.cz.conf
apache2ctl configtest && systemctl reload apache2
```

The `…-le-ssl.conf` companion file (`deploy/parts.matescb.cz-le-ssl.conf`)
is canonical in this repo and must also be copied to
`/etc/apache2/sites-available/` after any edit:

```bash
cp /srv/stockmanager/deploy/parts.matescb.cz-le-ssl.conf \
   /etc/apache2/sites-available/parts.matescb.cz-le-ssl.conf
apache2ctl configtest && systemctl reload apache2
```

**After a certbot re-issue:** `certbot renew` only rotates the cert files
(`SSLCertificateFile` / `SSLCertificateKeyFile`) — it does **not** rewrite
the `ProxyPass` block. However, if you ever run `certbot --apache` again
from scratch, it regenerates the ssl conf and overwrites any manual edits.
Re-copy the canonical file from the repo immediately afterwards.

### Drain mode for destructive migrations

Use this procedure when a migration is expected to take more than a few
seconds (e.g. table rewrites, large backfills) so users see a clean
"maintenance" page instead of 500/503 errors.

**Enter drain mode**

```bash
# 1. Copy the maintenance assets onto the host.
sudo cp /srv/stockmanager/deploy/maintenance.html /var/www/html/maintenance.html
sudo cp /srv/stockmanager/deploy/parts.matescb.cz.maintenance.conf \
        /etc/apache2/conf-available/parts-maintenance.conf

# 2. Enable the drop-in (serves 503 + maintenance page for all non-health requests).
sudo a2enconf parts-maintenance
sudo apache2ctl configtest && sudo systemctl reload apache2

# 3. Verify: browsing the site shows the maintenance page;
#    /api/health still returns 200.
curl -s https://parts.matescb.cz/api/health
# 200 → {"data":{"status":"ok","db":"ok","uploads":"ok"},...}
# 503 → structured body; see ## Health endpoint below
```

**Take a pre-migration snapshot**

```bash
/srv/stockmanager/deploy/backup.sh
```

**Run the migration**

```bash
# In the currently-running backend container (before the new image is built).
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec backend alembic upgrade head
```

**Deploy the new image**

```bash
cd /srv/stockmanager
sudo -u deploy git fetch --quiet origin main
sudo -u deploy git reset --hard origin/main
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    up -d --build
```

**Exit drain mode**

```bash
sudo a2disconf parts-maintenance
sudo apache2ctl configtest && sudo systemctl reload apache2
```

**Verify**

```bash
curl -s https://parts.matescb.cz/api/health
# 200 → {"data":{"status":"ok","db":"ok","uploads":"ok"},...}
# 503 → structured body; see ## Health endpoint below
```

### If you ever move TLS into the docker stack

Today certbot's Debian package reloads Apache automatically after renewal
(via `/etc/letsencrypt/renewal-hooks/deploy/apache`).  If TLS ever
terminates inside the docker stack instead (nginx in the `web` container
holds the cert), that hook no longer reloads the right thing and the
container would serve the expired cert until the next `docker compose up`.

- **Apache reload-on-renewal goes away** because there is no Apache in
  that scenario; the certbot package hook becomes a no-op.
- **Install the dormant hook** at
  `deploy/letsencrypt-deploy-hook.sh.example` as
  `/etc/letsencrypt/renewal-hooks/deploy/stockmanager-reload.sh`
  (executable, owned by root).  The script runs
  `docker compose … exec web nginx -s reload` so nginx picks up the fresh
  cert without a full container restart.
- **Validate** with
  `certbot renew --dry-run --deploy-hook /etc/letsencrypt/renewal-hooks/deploy/stockmanager-reload.sh`.

**BOM / scan-import timeouts (INFRA2-019).** Both proxy layers must agree:
the in-container nginx sets `proxy_read_timeout 5m` / `proxy_send_timeout 5m`
for `POST /api/projects/*/bom/import` and `POST /api/parts/bulk-import-from-scan`
(see `deploy/nginx-web.conf`), and **both** Apache vhosts (`:80` and `:443`)
set `timeout=300` on their `ProxyPass` directives (see
`deploy/parts.matescb.cz.conf` and `deploy/parts.matescb.cz-le-ssl.conf`).
Production HTTPS traffic terminates on the `:443` vhost, so the SSL file is
the load-bearing one for live BOM/scan-import calls; the `:80` vhost mainly
serves the HTTP→HTTPS redirect plus `/.well-known` for cert renewal. If you
change one, keep the other in sync. **The Apache vhost change is not
auto-deployed** — after merging, run the `cp` + `systemctl reload apache2`
commands above on the VPS for **both** files.

## Backups

Two volumes need covering:

- `db_data` — postgres. `pg_dump` is sufficient for a single-host setup.
- `uploads` — lot photos / datasheets. `pg_dump` does not cover this.

Backups run via the project-agnostic **vps-backup** service at
[matescb/vps-backup](https://github.com/matescb/vps-backup), cloned to
`/srv/backup/` on the VPS. stockmanager is a profile
(`profiles/stockmanager.conf`) — the runner does pg_dump, age-encrypt,
push to NAS, and GFS-prune for every project on the box.

**Pipeline**: `pg_dump | gzip | age -r $RECIPIENT` for the DB,
`tar czf - <volume> | age -r $RECIPIENT` for uploads. Each artifact is
verified (size floor + age header check), copied into a VPSfree NAS
dataset mounted at `/mnt/nas-backups/`, then promoted (hardlinked) into
`weekly/` on Sundays and `monthly/` on the 1st. GFS retention prunes
to 14 daily / 8 weekly / 6 monthly.

**Cron** (in `/etc/cron.d/vps-backup`):

```cron
30 3 * * * root /srv/backup/bin/run-backup.sh stockmanager >> /var/log/vps-backup.log 2>&1
```

**Layout on disk**:

```
/srv/backups/stockmanager/                              # local, 7-day retention
    db-2026-05-02.sql.gz.age
    assets-2026-05-02.tar.gz.age
/mnt/nas-backups/stockmanager/                          # NAS, GFS retention
    daily/db-2026-05-02.sql.gz.age
    weekly/db-2026-05-03.sql.gz.age                     # Sundays
    monthly/db-2026-06-01.sql.gz.age                    # 1st of month
```

The local copy exists for fast restores and verification; the NAS copy is
the durable one. If the VPS is destroyed, the encrypted NAS dataset is the
recovery surface (closes INFRA2-003).

The legacy script at [`deploy/backup.sh`](../deploy/backup.sh) is kept in
the repo for one cycle as a fallback; it will be removed once the new
service has run cleanly for a week.

Run a backup manually any time before a risky operation:

```bash
/srv/backup/bin/run-backup.sh stockmanager
```

Non-zero exit pings `BACKUP_HEALTHCHECK_FAIL_URL` (see below) and surfaces
in `/var/log/vps-backup.log`.

### Dead-man's-switch alerting (INFRA-006)

Cron mail is unreliable because it requires a working MTA and is often
silently lost. `backup.sh` now supports explicit ping-on-success /
ping-on-failure via two optional env vars in `.env.prod`:

| Variable | Purpose |
|---|---|
| `BACKUP_HEALTHCHECK_OK_URL` | Pinged at the end of a successful run |
| `BACKUP_HEALTHCHECK_FAIL_URL` | Pinged immediately via `trap ERR` on any non-zero exit |

Recommended service: [healthchecks.io](https://healthchecks.io) (free tier is
sufficient). Create one check, copy its ping URL into `BACKUP_HEALTHCHECK_OK_URL`,
and copy the `/fail` endpoint URL into `BACKUP_HEALTHCHECK_FAIL_URL`. This gives
two alert modes:

- **Missed-ping alert** — the check goes "Late" if the backup didn't run at all
  (cron failed, host was down, etc.).
- **Explicit failure alert** — immediate notification when the script itself errors.

Both vars default to empty; leaving them unset preserves the previous cron-mail-only
behaviour.

### Recipient key + private-key escrow

**Operator action required before first backup run:**

1. **Install `age` on the VPS** (small static binary, no runtime deps):

   ```bash
   curl -Lo /usr/local/bin/age \
       https://github.com/FiloSottile/age/releases/latest/download/age-linux-amd64
   curl -Lo /usr/local/bin/age-keygen \
       https://github.com/FiloSottile/age/releases/latest/download/age-linux-amd64
   chmod +x /usr/local/bin/age /usr/local/bin/age-keygen
   ```

   Adjust the release URL to the latest version from
   <https://github.com/FiloSottile/age/releases>.

2. **Generate a keypair on a secure, off-VPS machine:**

   ```bash
   age-keygen -o backup-key.txt
   # Public key: age1xxxx...
   ```

   The file `backup-key.txt` contains both the private key (secret) and
   the public key (safe to share).

3. **Add the public key to `.env.prod` on the VPS:**

   ```bash
   sudo -u deploy $EDITOR /srv/stockmanager/.env.prod
   # Set BACKUP_AGE_RECIPIENT=age1xxxx...  (the public key printed above)
   ```

4. **Escrow the private key off-VPS.** Store `backup-key.txt` in a secure
   location — a password manager, encrypted offline storage, or a secrets
   manager. The VPS holds only the public key; restoring backups requires
   the private key to be brought in manually.

5. **Verify the first nightly backup** by checking
   `/var/log/stockmanager-backup.log` the morning after, or run the script
   manually:

   ```bash
   /srv/stockmanager/deploy/backup.sh
   ```

### Restore (encrypted backups)

The vps-backup service ships an interactive restore script that walks the
NAS hierarchy (`daily/` → `weekly/` → `monthly/` fallback), prompts for
the off-VPS identity key, integrity-checks the artifact, and refuses to
overwrite anything without `--confirm-destructive`.

Copy your private key onto the VPS for the restore window only (e.g. via
`scp backup-key.txt v:/tmp/`), then:

```bash
# Always dry-run first — checks decrypt + gunzip integrity, then aborts:
/srv/backup/bin/restore.sh stockmanager 2026-05-02 db
# (prompts for the path to the identity file)

# Real DB restore:
/srv/backup/bin/restore.sh stockmanager 2026-05-02 db --confirm-destructive

# Same shape for the assets volume:
/srv/backup/bin/restore.sh stockmanager 2026-05-02 assets --confirm-destructive
```

After the restore window, scrub the key from the VPS:

```bash
shred -u /tmp/backup-key.txt
```

If the artifact you need is older than `RETAIN_NAS_DAILY` (14 days), the
restore script will pull it from the `weekly/` or `monthly/` tier
automatically — same command, same date argument.

For point-in-time recovery look at `pgBackRest` or `barman` — vps-backup
is daily-granular only and does not stream WAL.

## Monitoring

**Operator action required — this cannot be automated through CI.**

Configure an external uptime check on [UptimeRobot](https://uptimerobot.com)
(free tier, 5-minute interval):

| Setting         | Value                                                         |
|-----------------|---------------------------------------------------------------|
| Monitor type    | HTTPS                                                         |
| URL             | `https://parts.matescb.cz/api/health`                        |
| Method          | GET                                                           |
| Expected status | 200                                                           |
| Keyword match   | `"status":"ok"` (present in response body)                    |
| Interval        | 5 minutes                                                     |
| TLS expiry      | Enable if the plan includes it (catches cert renewal failure) |

**What this catches:** host-down, TLS-broken, Apache-crashed,
container-crashed, DB unreachable, and uploads volume not writable (any of
these causes `/api/health` to return 503). See [Health endpoint](#health-endpoint)
for the full response shapes.

**Credentials:** store the UptimeRobot login in the same secrets escrow
as other SaaS credentials (see #95 — secret rotation runbook). Do not put
them in `.env.prod` or the repo.

**Planned maintenance:** pause the monitor from the UptimeRobot dashboard
before triggering a deploy that causes a longer-than-usual restart (e.g.
a heavy migration). Resume it once `curl -fsS https://parts.matescb.cz/api/health`
returns 200 (200 body: `{"data":{"status":"ok","db":"ok","uploads":"ok"},...}`; 503
returns a structured body — see [Health endpoint](#health-endpoint)).

## Health endpoint

`GET /api/health` is the single liveness + readiness probe for the stack.
It runs two checks synchronously:

1. **Database**: executes `SELECT 1` via the SQLAlchemy engine. Any exception
   (connection refused, auth failure, timeout) marks the check failed and
   surfaces the Python exception class name in the response.
2. **Uploads volume**: calls `os.access(UPLOAD_DIR, os.W_OK)` to confirm the
   volume is mounted and the process can write to it.

Both checks must pass for a 200 response.

### 200 — healthy

```json
{
  "data": { "status": "ok", "db": "ok", "uploads": "ok" },
  "status": { "category": "ok", "message": "OK" }
}
```

### 503 — unhealthy

The `detail` dict is spread by `core/responses.py::http_exception_handler`
onto the standard `{data: null, status: {...}}` envelope. Example where the
DB is unreachable:

```json
{
  "data": null,
  "status": {
    "category": "server_error",
    "message": "service unhealthy"
  },
  "db": "error: OperationalError",
  "uploads": "ok"
}
```

Example where the uploads volume is missing or read-only (DB is fine):

```json
{
  "data": null,
  "status": {
    "category": "server_error",
    "message": "service unhealthy"
  },
  "db": "ok",
  "uploads": "not writable: /data/uploads"
}
```

The `db` field is either `"ok"` or `"error: <ExceptionClassName>"`.
The `uploads` field is either `"ok"` or `"not writable: <path>"`.

### Callers

| Caller | Notes |
|--------|-------|
| `docker-compose.prod.yml` healthcheck | Controls when the `web` container starts; determines `docker compose ps` status for the `backend` service. |
| Post-deploy CI gate | `curl /api/health` retried in the deploy script so a failed migration, missing env var, or DB outage fails the deploy job rather than returning a green CI result on a broken prod. |
| UptimeRobot external monitor | 5-minute interval; see [Monitoring](#monitoring). |
| Manual smoke | Run `curl -fsS https://parts.matescb.cz/api/health` after any operator-driven change to confirm the stack is healthy. |

## Base image pinning (INFRA2-015)

All three Docker base images are pinned by digest, not tag, so a compromised
or republished `node:20-alpine` / `nginx:alpine` / `python:3.12-slim` tag can
never silently change what we build:

| File | Stage | Image |
|------|-------|-------|
| `backend/Dockerfile` | builder | `python:3.12@sha256:…` |
| `backend/Dockerfile` | runtime | `python:3.12-slim@sha256:…` |
| `web/Dockerfile.prod` | build | `node:20-alpine@sha256:…` |
| `web/Dockerfile.prod` | runtime | `nginx:alpine@sha256:…` |

### Bumping a digest

Resolve the current manifest-list digest (multi-arch index):

```bash
curl -s "https://registry.hub.docker.com/v2/repositories/library/<image>/tags/<tag>" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['digest'])"
```

Then update the `@sha256:` line in the Dockerfile and the `# Digest pinned on`
comment. Open as a normal PR — CI's `backend-tests` and `web-build` jobs will
exercise the new image.

### Dependabot

`.github/dependabot.yml` configures weekly Dependabot PRs for the `docker`
ecosystem targeting `backend/` and `web/` directories. Dependabot will open
PRs automatically when newer digests are available. Review and merge them like
any dependency-bump PR; CI gates protect against regressions.

### Buildkit cache hygiene

The VPS build cache accumulates layers from previous builds. If you need to
evict stale layers (e.g. after a security incident or after rotating secrets
from build args), run on the VPS:

```bash
docker buildx prune -af
```

This forces a full cold rebuild on the next deploy. After INFRA2-015 landed,
sourcemaps are no longer emitted during VPS builds (only in CI where
`SENTRY_AUTH_TOKEN` is set), so the build cache no longer accumulates `.map`
files.

## Header hardening (SEC2-018)

Two surfaces were tightened to avoid advertising the stack identity:

**nginx `Server` header suppression.** uvicorn sets `Server: uvicorn` on every
response. `deploy/nginx-web.conf` now suppresses this with:

- `server_tokens off;` — prevents nginx from emitting its own version string
  in the `Server` header and in default error pages.
- `proxy_hide_header Server;` inside the `/api/` proxy block — drops the
  upstream `Server: uvicorn` header before the response reaches the client.

These directives are in the in-container nginx config that the `web` container
runs (`web/Dockerfile.prod` copies it to `/etc/nginx/conf.d/default.conf`).
They take effect on the next `docker compose up --build`.

**CI traceback-leak guard.** `backend/scripts/check_no_traceback_leaks.py`
walks `backend/app/` via the Python AST and fails if any
`HTTPException(detail=…)` argument contains traceback-related identifiers
(`traceback`, `format_exc`, `format_exception`, `exc_info`, `__class__`).
The check runs as a fast step in `.github/workflows/ci.yml` before pytest,
preventing accidental stack-trace exposure in error responses from ever
reaching `main`. The corresponding tests live in
`backend/tests/test_no_traceback_leaks.py`.
