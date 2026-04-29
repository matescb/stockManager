# Production deployment

This guide documents how `parts.matescb.cz` is deployed: a docker-compose stack
behind the host's existing Apache 2.4 reverse proxy, with TLS issued + renewed
by `certbot --apache`. Day-to-day deploys are automated by GitHub Actions
(see `.github/workflows/ci.yml`); this document covers the **one-time**
bootstrap and the architectural shape so you can re-create or migrate it.

The dev compose (`docker-compose.yml`) is unsuitable for production: it ships
uvicorn `--reload`, the Vite dev server, and a placeholder session secret.

## Architecture

```
  internet → :80/:443 (host Apache)
                       ├─ parts.matescb.cz → 127.0.0.1:8091
                       │                    │
                       │                    ▼
                       │              docker compose:
                       │                ├─ web   (nginx + Vite dist/)
                       │                │   └─ /api/* → backend
                       │                ├─ backend (uvicorn, 2 workers)
                       │                └─ db (postgres:16-alpine)
                       └─ … (other vhosts on this host)
```

Only the `web` container publishes a host port, and only on loopback. Apache
fronts the public side. The web container's nginx handles the `/api/*` →
backend split internally so Apache only needs one ProxyPass per app — matching
the convention every other vhost on this VPS already uses.

## Prerequisites

- Docker Engine 24+ with the compose plugin (`docker compose version`).
- Apache 2.4 with `mod_proxy`, `mod_proxy_http`, `mod_rewrite`, `mod_ssl`.
- `certbot` with the `python3-certbot-apache` plugin.
- A DNS A record for the domain pointing at this host. For
  `parts.matescb.cz` this is `37.205.15.171`.

## One-time bootstrap

Done from a root shell on the VPS. The CI pipeline takes over for every
subsequent deploy.

1. **Create the deploy user with docker access.** CI logs in as this user
   and runs `docker compose up`; nothing else should run as them.

   ```bash
   useradd -m -s /bin/bash deploy
   usermod -aG docker deploy
   install -d -o deploy -g deploy -m 0755 /srv/stockmanager
   ```

2. **Clone the repo.**

   ```bash
   sudo -u deploy git clone https://github.com/matescb/stockManager.git /srv/stockmanager
   ```

3. **Generate a CI keypair.** The public key authorizes the deploy user;
   the private key goes into a GitHub Actions secret (`DEPLOY_SSH_KEY`).

   ```bash
   sudo -u deploy install -d -m 0700 /home/deploy/.ssh
   sudo -u deploy ssh-keygen -t ed25519 -f /home/deploy/.ssh/id_ed25519 \
       -N "" -C "github-actions@stockmanager"
   sudo -u deploy bash -c 'cat /home/deploy/.ssh/id_ed25519.pub \
       >> /home/deploy/.ssh/authorized_keys && \
       chmod 600 /home/deploy/.ssh/authorized_keys'
   ```

4. **Seed `.env.prod`.** Fill in real secrets — these never enter CI.

   ```bash
   sudo -u deploy cp /srv/stockmanager/deploy/.env.prod.example \
                    /srv/stockmanager/.env.prod
   sudo -u deploy chmod 600 /srv/stockmanager/.env.prod
   # Rotate POSTGRES_PASSWORD (openssl rand -base64 24) and SESSION_SECRET
   # (openssl rand -hex 32). CORS_ORIGINS is preset to the public domain.
   ```

5. **Bring up the stack.** The backend container runs `alembic upgrade head`
   on every boot, so a fresh DB migrates itself.

   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml \
       --env-file .env.prod up -d --build
   curl -fsS http://127.0.0.1:8091/api/health
   ```

6. **Add the Apache vhost.** Copy the canonical template from the repo.

   ```bash
   cp /srv/stockmanager/deploy/parts.matescb.cz.conf \
      /etc/apache2/sites-available/parts.matescb.cz.conf
   a2ensite parts.matescb.cz
   apache2ctl configtest && systemctl reload apache2
   curl -fsS -H "Host: parts.matescb.cz" http://127.0.0.1/api/health
   ```

7. **Issue the TLS cert.** Certbot edits the vhost in place to add the
   redirect and writes a sibling `parts.matescb.cz-le-ssl.conf` for :443.

   ```bash
   certbot --apache -d parts.matescb.cz \
       --non-interactive --agree-tos -m matyas.skvor@gmail.com --redirect
   curl -fsS https://parts.matescb.cz/api/health
   ```

Renewal is automated by the certbot systemd timer that ships with the Debian
package — verify with `systemctl list-timers | grep certbot`.

## CI/CD

`.github/workflows/ci.yml` defines three jobs:

- **`backend-tests`** — pytest against a postgres:16-alpine service container.
- **`web-build`** — `npm ci && npm run build` (also catches type errors).
- **`deploy`** — runs only on `push` to `main`, only after the two test jobs
  pass. SSH'es to the VPS as `deploy`, `git fetch`/`reset --hard origin/main`,
  then `docker compose ... up -d --build`. Concurrency-grouped so consecutive
  pushes queue rather than cancel.

Required repo secrets (Settings → Secrets and variables → Actions):

| Secret           | Value                                                |
|------------------|------------------------------------------------------|
| `DEPLOY_HOST`    | `37.205.15.171`                                      |
| `DEPLOY_USER`    | `deploy`                                             |
| `DEPLOY_SSH_KEY` | private key from bootstrap step 3 (full ED25519 PEM) |

## Routine operations

Tail backend logs:

```bash
docker compose -f docker-compose.prod.yml logs -f backend
```

Open a psql shell against the running DB:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec db \
    psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

Run an ad-hoc alembic command:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec backend \
    alembic current
```

## Backups

Backups are your responsibility. Two volumes need covering:

- `db_data` — postgres. `pg_dump` is sufficient for a single-host setup.
- `uploads` — lot photos / datasheets. `pg_dump` does not cover this.

```bash
# Postgres dump.
docker compose -f docker-compose.prod.yml --env-file .env.prod exec db \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    | gzip > "backup-$(date +%F).sql.gz"

# Uploads dump.
docker run --rm \
    -v stockmanager_uploads:/u \
    -v "$PWD":/out \
    alpine \
    tar czf /out/uploads-$(date +%F).tar.gz -C /u .
```

Restore the DB (DESTRUCTIVE — overwrites the existing one):

```bash
gunzip -c backup-2026-04-29.sql.gz \
    | docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db \
        psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

For point-in-time recovery, off-site replication, or retention policies look
at `pgBackRest` or `barman` — proper tools for that job; this guide does not
prescribe one.
