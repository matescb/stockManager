# Secret rotation runbook

Audience: engineer / on-call

This runbook covers every long-lived secret used by the stockManager production
stack.  Follow it any time you rotate a secret (planned cadence, personnel
change, or suspected leak).

Related runbooks: see `docs/deployment.md` for general VPS operations and the
`Changing env vars` section for the mechanics of editing `.env.prod`.

---

## 1. Secret inventory

| Secret | Location | Used by | Blast radius if leaked | Escrow location | Rotation cadence |
|--------|----------|---------|------------------------|-----------------|------------------|
| `SESSION_SECRET` | `.env.prod` on VPS | backend (session signing) | Attacker can forge login sessions | Operator password manager alongside `WORKSPACE_SECRETS_KEY` | Annual; on personnel change; on leak |
| `PASSWORD_PEPPER` | `.env.prod` on VPS | backend password hashing | Helps verify guessed passwords if DB hashes are leaked; losing/changing it blocks login for peppered hashes | Operator password manager alongside `SESSION_SECRET` | Annual only with planned password reset / migration; immediately on leak |
| `POSTGRES_PASSWORD` | `.env.prod` on VPS | backend (DB connection), `db` service init | Full DB read/write access from inside the docker network | Operator password manager | Annual; on personnel change; on leak |
| `WORKSPACE_SECRETS_KEY` | `.env.prod` on VPS | backend (`core/secrets.py`) | Decrypts every stored provider API key / secret / scanner licence key for every workspace | Operator password manager — **escrow is mandatory; losing it is unrecoverable** | Annual; on personnel change; on leak |
| `SENTRY_DSN` | `.env.prod` on VPS | backend SDK init | Attacker can inject fake events; exposes project DSN URL | Sentry project settings page | Annual; on personnel change; on leak |
| `VITE_SENTRY_DSN` | `.env.prod` on VPS (baked into SPA at build time) | frontend Sentry SDK | Same as above; DSN is semi-public in the built JS bundle | Sentry project settings page | Annual; on personnel change; on leak |
| `SENTRY_AUTH_TOKEN` | GitHub Actions secret (`SENTRY_AUTH_TOKEN`) | CI `web-build` job (sourcemap upload) | Can upload sourcemaps / releases to Sentry project; scoped to `project:write` + `project:releases` | GitHub Actions secrets page | Annual; on personnel change; on leak |
| `SENTRY_ORG` / `SENTRY_PROJECT` | GitHub Actions secrets | CI `web-build` job | Low-risk identifiers; no auth on their own | N/A | On org/project rename |
| `DEPLOY_SSH_KEY` | GitHub Actions secret (`DEPLOY_SSH_KEY`) | CI `deploy` job (SSH into VPS) | Can run arbitrary commands on VPS as the `deploy` user (docker socket access = effectively root) | Operator password manager | Annual; on personnel change; on leak |
| `DEPLOY_HOST_FINGERPRINT` | GitHub Actions secret (`DEPLOY_HOST_FINGERPRINT`) | CI `deploy` job (SSH host-key pin) | Wrong value blocks deploys; stale value after host-key rotation blocks deploys; leaked value is public metadata only | GitHub Actions secrets page | On VPS host-key rotation or VPS migration |
| `DEPLOY_HOST` / `DEPLOY_USER` | GitHub Actions secrets | CI `deploy` job | Low-risk identifiers | N/A | On VPS migration |
| `BACKUP_AGE_RECIPIENT` keypair | Per issue #90 | Backup encryption (future) | Backup archives unreadable without private key | See issue #90 runbook | On personnel change |
| `UPTIMEROBOT_*` credentials | Per issue #94 | Uptime monitoring (future) | Attacker can silence alerts | UptimeRobot account settings | On personnel change; on leak |

---

## 2. Per-secret playbooks

### 2.1 `SESSION_SECRET`

**Effect of rotation:** all active user sessions are immediately invalidated —
every logged-in user is logged out.  No data loss.

**Steps:**

1. SSH into the VPS.
2. Generate a new secret:
   ```bash
   openssl rand -hex 32
   ```
3. Edit `.env.prod`:
   ```bash
   sudo -u deploy $EDITOR /srv/stockmanager/.env.prod
   # Replace the SESSION_SECRET= line with the new value.
   ```
4. Restart the backend to pick up the new value:
   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       up -d backend
   ```
5. Verify the backend is healthy:
   ```bash
   curl -fsS https://parts.matescb.cz/api/health
   ```

---

### 2.2 `PASSWORD_PEPPER`

**Initial bootstrap:** run `make bootstrap-pepper` once on the VPS after
`.env.prod` exists and before the first prod boot. The command prints the
generated value and requires an explicit escrow confirmation before writing it
to `.env.prod`. Store it in the operator password manager alongside
`SESSION_SECRET`. CI deploys fail closed if this value is missing or still set
to the template placeholder.

**Effect of rotation:** passwords already rehashed with the old pepper will no
longer verify. Do not rotate this like a routine stateless secret unless you are
also running a planned password-reset campaign or a purpose-built migration.

**Steps for suspected leak:**

1. Treat the database password hashes as exposed; the pepper no longer provides
   its intended second factor.
2. Force a password reset for all users before replacing the value.
3. Generate a new pepper:
   ```bash
   openssl rand -hex 32
   ```
4. Edit `.env.prod` and replace `PASSWORD_PEPPER=<OLD>` with the new value.
5. Restart every backend/cron service with the normal env-change procedure.

---

### 2.3 `POSTGRES_PASSWORD`

**Effect of rotation:** brief downtime (~5 s) while the backend container
restarts.  The Postgres volume password must be changed **inside Postgres
first** — editing `.env.prod` alone will cause the backend to fail to connect
on next restart.

**Steps:**

1. SSH into the VPS.
2. Open a psql shell:
   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec db psql -U stockmgr stockmgr
   ```
3. Generate a new password (run this in a separate terminal so you can paste):
   ```bash
   openssl rand -base64 24
   ```
4. In the psql shell, change the role password (replace `<NEW>` with the
   generated value):
   ```sql
   ALTER USER stockmgr PASSWORD '<NEW>';
   \q
   ```
5. Edit `.env.prod` and update `POSTGRES_PASSWORD=<NEW>`.
6. Restart the backend (the `db` service itself does not need restarting — its
   init password is only used when the volume is created for the first time):
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       up -d backend
   ```
7. Confirm connectivity:
   ```bash
   curl -fsS https://parts.matescb.cz/api/health
   ```

---

### 2.4 `SENTRY_DSN` / `VITE_SENTRY_DSN`

**Effect of rotation:** no outage.  In-flight events still land on Sentry
(the old DSN stays valid until explicitly revoked).  The frontend DSN is baked
into the SPA bundle at build time, so a new deploy is needed to push the new
frontend DSN.

**Steps:**

1. In the Sentry UI, go to **Project Settings → Client Keys (DSN)** for the
   relevant project (backend project for `SENTRY_DSN`; frontend project for
   `VITE_SENTRY_DSN`).
2. Generate a new DSN (or rotate the existing key).
3. Revoke the old DSN once the new one is live.
4. SSH into the VPS and edit `.env.prod`:
   ```bash
   sudo -u deploy $EDITOR /srv/stockmanager/.env.prod
   # Update SENTRY_DSN= and/or VITE_SENTRY_DSN= with the new value.
   ```
5. Restart backend (for `SENTRY_DSN`):
   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       up -d backend
   ```
6. For `VITE_SENTRY_DSN`: push a no-op commit to `main` to trigger a CI
   rebuild (the frontend DSN is baked at `npm run build` time):
   ```bash
   git commit --allow-empty -m "chore: rebuild frontend with new Sentry DSN"
   git push origin main
   ```
7. Confirm events are arriving in the Sentry project dashboard.

---

### 2.5 `SENTRY_AUTH_TOKEN`

**Note (INFRA2-010):** this token lives **only** in GitHub Actions secrets.  It
must **never** appear in `.env.prod` or as a Docker build arg — doing so would
embed it in the layer cache.

**Effect of rotation:** no outage.

**Steps:**

1. In the Sentry UI, go to **User/Org Settings → Auth Tokens** and create a
   new token with scopes `project:write` and `project:releases`.
2. Go to
   <https://github.com/matescb/stockManager/settings/secrets/actions> and
   update `SENTRY_AUTH_TOKEN` with the new value.
3. Revoke the old token in Sentry.
4. Verify by pushing a no-op commit to `main` and confirming the `web-build`
   CI job completes the sourcemap upload step without errors.

---

### 2.6 `DEPLOY_SSH_KEY`

**Effect of rotation:** CI cannot deploy until both the GitHub Actions secret
and the VPS `authorized_keys` are updated.  Do both changes before the next
push to `main`.

**Steps:**

1. SSH into the VPS.
2. Generate a new keypair as the `deploy` user:
   ```bash
   sudo -u deploy ssh-keygen -t ed25519 \
       -f /home/deploy/.ssh/id_ed25519_new \
       -N "" -C "github-actions@stockmanager-$(date +%Y%m%d)"
   ```
3. Append the new public key to `authorized_keys`:
   ```bash
   sudo -u deploy bash -c \
       'cat /home/deploy/.ssh/id_ed25519_new.pub >> /home/deploy/.ssh/authorized_keys'
   ```
4. Copy the **private** key content:
   ```bash
   sudo cat /home/deploy/.ssh/id_ed25519_new
   ```
5. Go to
   <https://github.com/matescb/stockManager/settings/secrets/actions> and
   update `DEPLOY_SSH_KEY` with the new private key.
6. Push a no-op commit to `main` to trigger a deploy:
   ```bash
   git commit --allow-empty -m "chore: verify new deploy SSH key"
   git push origin main
   ```
7. Confirm the `deploy` CI job succeeds end-to-end.
8. Remove the old public key from `authorized_keys` on the VPS:
   ```bash
   # Edit the file and delete the line containing the old key fingerprint.
   sudo -u deploy $EDITOR /home/deploy/.ssh/authorized_keys
   ```
9. Remove the old keypair files:
   ```bash
   sudo -u deploy rm /home/deploy/.ssh/id_ed25519 \
                     /home/deploy/.ssh/id_ed25519.pub
   sudo -u deploy mv /home/deploy/.ssh/id_ed25519_new \
                     /home/deploy/.ssh/id_ed25519
   sudo -u deploy mv /home/deploy/.ssh/id_ed25519_new.pub \
                     /home/deploy/.ssh/id_ed25519.pub
   ```

---

### 2.7 `DEPLOY_HOST_FINGERPRINT`

**Effect of rotation:** CI deploys fail until GitHub Actions has the new
fingerprint. The deploy job uses raw SSH pinned to the verified ED25519
`known_hosts` entry, so update the secret immediately after a planned VPS
host-key rotation, and before the first deploy to a migrated VPS.

**Steps:**

1. SSH into the VPS through a trusted admin path.
2. Print the public ED25519 host-key fingerprint:
   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```
3. Copy only the `SHA256:...` fingerprint field from the output.  Do not copy
   any private host-key file.
4. From a separate trusted network, confirm the same public fingerprint is
   visible remotely:
   ```bash
   ssh-keyscan -t ed25519 <DEPLOY_HOST> 2>/dev/null | ssh-keygen -lf -
   ```
5. Go to
   <https://github.com/matescb/stockManager/settings/secrets/actions> and
   update `DEPLOY_HOST_FINGERPRINT` with the `SHA256:...` value.
6. Push a no-op commit to `main` to trigger a deploy:
   ```bash
   git commit --allow-empty -m "chore: verify deploy host fingerprint"
   git push origin main
   ```
7. Confirm the `deploy` CI job reaches the remote script.  A host-key mismatch
   fails before any deploy command runs on the VPS.  The trusted secret must
   stay tied to `/etc/ssh/ssh_host_ed25519_key.pub`; see
   `docs/deployment.md`.

---

### 2.8 `WORKSPACE_SECRETS_KEY` — the complex one

**Background:** `WORKSPACE_SECRETS_KEY` is a Fernet key
(`cryptography.fernet.Fernet`).  Every workspace's provider API key, provider
API secret, and scanner licence key is encrypted with this key and stored as a
ciphertext column.  Losing the key makes every encrypted credential
**permanently unrecoverable**.  See `backend/app/core/secrets.py` for the
implementation.

A naive swap (delete old key, set new key, restart) will cause the backend to
fail to decrypt any existing credential — the app will return HTTP 500 for
every API call that needs a provider credential until every row is re-encrypted.
The safe path uses a **dual-key transition** via `cryptography.fernet.MultiFernet`.

**Pre-rotation checklist:**

- [ ] Take a `pg_dump` backup **before starting** (see
  `docs/deployment.md#backups`).  The backup is your recovery path if anything
  goes wrong.
- [ ] Confirm the current key is escrowed in the password manager.
- [ ] Schedule the rotation during a low-traffic window.

**Step 1 — Generate the new key and enable dual-key reads**

Generate a new Fernet key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Open a PR that:
1. Adds `WORKSPACE_SECRETS_KEY_OLD` to `backend/app/core/config.py` as an
   optional `str | None` setting (default `None`).
2. Modifies `_fernet()` in `backend/app/core/secrets.py` to return a
   `MultiFernet` when `WORKSPACE_SECRETS_KEY_OLD` is set:
   ```python
   from cryptography.fernet import Fernet, MultiFernet, InvalidToken

   @lru_cache(maxsize=1)
   def _fernet():
       from app.core.config import settings
       s = settings()
       new_key = Fernet(s.WORKSPACE_SECRETS_KEY.encode("ascii"))
       old_raw = s.WORKSPACE_SECRETS_KEY_OLD
       if old_raw:
           old_key = Fernet(old_raw.encode("ascii"))
           return MultiFernet([new_key, old_key])
       return new_key
   ```
   `MultiFernet` encrypts with the first key in the list (the new key) and
   tries each key in order on decrypt — so existing rows encrypted under the
   old key continue to decrypt correctly.

Merge and deploy.

**Step 2 — Deploy with both keys active**

On the VPS, edit `.env.prod`:
- Set `WORKSPACE_SECRETS_KEY=<NEW_KEY>`
- Add `WORKSPACE_SECRETS_KEY_OLD=<OLD_KEY>` (the value that was previously in
  `WORKSPACE_SECRETS_KEY`)

Restart the backend:
```bash
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    up -d backend
```

At this point:
- New credentials written by workspaces are encrypted under the new key.
- Existing credentials still encrypted under the old key decrypt correctly via
  the MultiFernet fallback.

**Step 3 — Re-encrypt existing rows**

Run the re-encryption script against the live database.  The script is a
future deliverable tracked as `backend/scripts/reencrypt_workspace_secrets.py`;
once it ships, run it with:
```bash
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec backend python backend/scripts/reencrypt_workspace_secrets.py
```
The script should:
1. Select every workspace row that has a non-null credential column.
2. Decrypt each value with the current `_fernet()` (MultiFernet — tries new key
   first, falls back to old key).
3. Re-encrypt with `_fernet().encrypt(…)` (always writes under the new key
   since MultiFernet encrypts with the first key).
4. Write the ciphertext back and commit the transaction per-row (or in small
   batches) so a restart does not lose progress.

**Step 4 — Verify zero rows still need the old key**

After the script completes, query the workspace table to confirm every
credential column has been touched (updated `_at` timestamp or a dedicated
migration flag).  Attempt a test decrypt of a sample row through the API — a
successful provider API call confirms the credential round-trips correctly under
the new key.

**Step 5 — Remove the old key and revert MultiFernet**

Once you are confident all rows are encrypted under the new key:

1. Open a cleanup PR that reverts the `MultiFernet` change in
   `backend/app/core/secrets.py` back to a single `Fernet(new_key)` and
   removes the `WORKSPACE_SECRETS_KEY_OLD` setting from `config.py`.
2. Merge and deploy.
3. On the VPS, remove `WORKSPACE_SECRETS_KEY_OLD` from `.env.prod`.
4. Restart the backend.
5. Confirm the health endpoint and a live provider call succeed.
6. Revoke the old key from the password manager escrow.

---

## 3. Cadence table

| Category | Secrets | Cadence |
|----------|---------|---------|
| App secrets (VPS `.env.prod`) | `SESSION_SECRET`, `POSTGRES_PASSWORD`, `WORKSPACE_SECRETS_KEY`, `SENTRY_DSN`, `VITE_SENTRY_DSN` | Annual; immediately on personnel change or suspected leak |
| CI / infrastructure secrets | `DEPLOY_SSH_KEY`, `SENTRY_AUTH_TOKEN` | Annual; immediately on personnel change or suspected leak |
| SSH trust pins | `DEPLOY_HOST_FINGERPRINT` | On VPS host-key rotation or VPS migration |
| SaaS identifiers | `SENTRY_ORG`, `SENTRY_PROJECT`, `DEPLOY_HOST`, `DEPLOY_USER` | On rename / migration only |
| Monitoring credentials | `UPTIMEROBOT_*` (issue #94) | On personnel change; immediately on leak |
| Backup encryption key | `BACKUP_AGE_RECIPIENT` (issue #90) | On personnel change |
| Immediate-on-leak | All of the above | Any confirmed or suspected exposure — rotate first, investigate second |

---

## 4. Out of scope

The following are **not** covered by this runbook:

- **TLS certificate** (`parts.matescb.cz`) — renewed automatically by the
  `certbot` systemd timer.  Manual intervention is only needed if the timer
  fails; see `docs/deployment.md`.
- **Postgres `postgres` superuser password** — not used by the application;
  managed separately as part of VPS host hardening.
- **Per-workspace provider API keys** (Mouser, DigiKey) — these are
  user-managed credentials stored encrypted in the `workspaces` table.  Users
  rotate them via the workspace settings UI; the encryption is covered by the
  `WORKSPACE_SECRETS_KEY` playbook above.
- **SSH host keys on the VPS** — rotate the `DEPLOY_HOST_FINGERPRINT` GitHub
  Actions secret with the playbook above.
