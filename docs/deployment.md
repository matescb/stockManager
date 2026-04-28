# Production deployment

This guide walks through a single-host deployment of the parts-inventory app
using `docker-compose.prod.yml`. The dev compose (`docker-compose.yml`) is
unsuitable for production: it ships uvicorn `--reload`, vite dev server, a
self-signed cert, and a placeholder session secret.

The prod stack:

```
  internet → :80/:443 (proxy: nginx)
                        ├─ /api/*  → backend (uvicorn, no reload, 2 workers)
                        └─ /*      → web (nginx serving Vite `dist/`)
                                  backend → db (postgres:16-alpine)
```

Only the `proxy` service publishes host ports. `backend`, `web`, and `db`
talk to each other over the docker network.

---

## Prerequisites

- Docker Engine 24+ with the compose plugin (`docker compose version`).
- A DNS A/AAAA record pointing your domain at this host.
- Inbound TCP 80 and 443 open to the internet (80 is needed for HTTP→HTTPS
  redirect and for HTTP-01 ACME challenges if you use Let's Encrypt).
- A place to put TLS material: either a sidecar that obtains it for you, or
  files you drop into `./deploy/certs/`.

---

## Step 1 — Configure environment

```bash
git clone <your-fork-or-this-repo>
cd stockManager

cp deploy/.env.prod.example .env.prod
$EDITOR .env.prod
```

Things you must change in `.env.prod`:

- `SESSION_SECRET` — rotate it. The default placeholder is not a secret.

  ```bash
  openssl rand -hex 32
  ```

- `POSTGRES_PASSWORD` — strong random value. Once the `db_data` volume is
  initialized this cannot be changed without rotating the role inside
  Postgres, so pick well the first time:

  ```bash
  openssl rand -base64 24
  ```

- `CORS_ORIGINS` — set this to the public origin(s) that will host the SPA,
  e.g. `https://parts.example.com`. Multiple origins are comma-separated.

`POSTGRES_USER` / `POSTGRES_DB` can stay at their defaults.

---

## Step 2 — TLS certificates

Pick one of the two paths below. The proxy expects the cert and key at
`/etc/nginx/certs/fullchain.pem` and `/etc/nginx/certs/privkey.pem`, which is
bind-mounted from `./deploy/certs/` by `docker-compose.prod.yml`.

### Option A — bring your own certs (simplest)

If you already have a wildcard cert, an internal CA, or you generated certs
out-of-band:

```bash
mkdir -p deploy/certs
cp /path/to/fullchain.pem deploy/certs/fullchain.pem
cp /path/to/privkey.pem  deploy/certs/privkey.pem
chmod 600 deploy/certs/privkey.pem
```

Renewal is on you; reload nginx after replacing the files:

```bash
docker compose -f docker-compose.prod.yml exec proxy nginx -s reload
```

### Option B — Let's Encrypt via a sidecar

Two lightweight options, neither prescribed:

- **`nginx-proxy/acme-companion`** — pairs with `nginx-proxy/nginx-proxy` and
  obtains/renews certs based on container labels. Replace the `proxy` service
  entirely with the `nginx-proxy` + `acme-companion` pair, drop this repo's
  `deploy/nginx.conf`, and label `web` / `backend` with `VIRTUAL_HOST`,
  `LETSENCRYPT_HOST`, etc. Lightest setup if you don't want to manage nginx.

- **Caddy** — single binary that handles TLS issuance + renewal automatically
  with one short Caddyfile. Replace the `proxy` service with `caddy:alpine`
  and a Caddyfile that proxies `/api/*` to `backend:8000` and `/*` to
  `web:80`. Easiest to write; you lose the explicit nginx config.

Either way, you'll typically remove the bind-mount of `./deploy/certs/` and
replace it with a named volume that the ACME container writes into.

---

## Step 3 — Bring it up

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

What this does:

- Builds the `backend` image (`backend/Dockerfile`).
- Builds the `web` image as a multi-stage Vite build → nginx:alpine
  (`web/Dockerfile.prod`).
- Pulls `postgres:16-alpine` and `nginx:alpine`.
- Starts the stack detached, with `restart: unless-stopped` on every service.
- The `backend` container runs `alembic upgrade head` before booting uvicorn,
  so a fresh DB will be migrated on first start.

Check status:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend
```

---

## Step 4 — Verify

```bash
curl -fsS https://<your-domain>/api/health
```

You should get a 2xx JSON response. If you used a self-signed cert, add `-k`
just to confirm the proxy is reachable, then fix the cert chain. From a
browser, visit `https://<your-domain>/` and sign up; the SPA should load and
the session cookie should round-trip.

---

## Postgres backup & restore

Backups are your responsibility. The simplest, no-extra-deps approach:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec db \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    | gzip > "backup-$(date +%F).sql.gz"
```

Restore (DESTRUCTIVE — confirms over the existing DB):

```bash
gunzip -c backup-2026-04-28.sql.gz \
    | docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db \
        psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

For anything more serious — point-in-time recovery, retention policies,
off-site replication — look at `pgBackRest` or `barman`. They're proper
tools for the job; this guide does not prescribe a particular one.

Don't forget the `uploads` volume — `pg_dump` doesn't cover the lot photos /
attachments stored on disk under `/data/uploads`. A periodic
`docker run --rm -v stockmanager_uploads:/u -v "$PWD":/out alpine \
    tar czf /out/uploads-$(date +%F).tar.gz -C /u .` handles it.

---

## Upgrades

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

Alembic runs automatically on backend container boot, so any new revisions
under `backend/alembic/versions/` are applied before uvicorn starts. There is
no separate migration step.

If a release introduces a destructive migration, take a `pg_dump` first.

---

## Known gap: session cookie `secure` flag

The auth cookie set by the backend (see
`backend/app/api/routes/auth.py::_set_session_cookie`) is currently created
**without** `secure=True`. In dev that's fine — there is no TLS. In prod the
proxy terminates TLS and forwards plain HTTP to the backend, so the backend
itself never sees an HTTPS connection and won't set `secure` on its own.

The cookie is already `httpOnly` and `samesite=lax`, so the most common
attack surface (JS exfiltration, third-party CSRF) is mitigated. The
remaining risk is a man-in-the-middle on a non-HTTPS network surface — for
which TLS termination at the proxy already protects every transit hop you
control.

To close the gap fully, either:

1. Add an env var (e.g. `SESSION_COOKIE_SECURE=1`) read by
   `_set_session_cookie` and set it in `.env.prod`, or
2. Hardcode `secure=True` when `APP_ENV=prod`.

Both are small follow-up patches in the backend; this deployment guide
intentionally treats the gap as documentation, not a code change.

---

## Useful one-liners

Tail backend logs:

```bash
docker compose -f docker-compose.prod.yml logs -f backend
```

Open a psql shell against the running DB:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec db \
    psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

Reload nginx after editing `deploy/nginx.conf`:

```bash
docker compose -f docker-compose.prod.yml exec proxy nginx -t \
    && docker compose -f docker-compose.prod.yml exec proxy nginx -s reload
```

Run an ad-hoc alembic command:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec backend \
    alembic current
```
