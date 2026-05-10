# Runbook: maintenance mode

Audience: engineer / on-call

Use Apache maintenance mode for planned deploys and backend outages. The
deploy job now toggles it automatically; operators only run these commands
for manual maintenance or incident recovery.

- **When to run**: planned maintenance outside the GitHub deploy path, or
  a production backend outage where users should see the static page.
- **Severity**: Routine for planned work; SEV-1 when the backend is down.
- **Time-to-recovery target**: < 5 min to show or remove the page once SSH
  access is available.
- **Owner**: `<TODO(verify): on-call rotation>`

## Pre-flight

- SSH access to the VPS as the deploy operator.
- `sudo` is allowed for `a2enconf`, `a2disconf`, `apache2ctl configtest`,
  and `systemctl reload apache2`. If not, add the sudoers rule in a separate
  INFRA issue; do not weaken the deploy script.
- `/etc/apache2/conf-available/parts-maintenance.conf` exists and was copied
  from `deploy/parts.matescb.cz.maintenance.conf`.
- The regular vhosts include the outage fallback for 502/503/504 responses
  (`deploy/parts.matescb.cz.conf:31`, `deploy/parts.matescb.cz-le-ssl.conf:43`).

## Steps

1. Install or refresh the checked-in maintenance config.
   ```bash
   cd /srv/stockmanager
   sudo cp deploy/parts.matescb.cz.maintenance.conf \
       /etc/apache2/conf-available/parts-maintenance.conf
   sudo apache2ctl configtest
   ```
2. Enter maintenance mode.
   ```bash
   sudo a2enconf parts-maintenance
   sudo systemctl reload apache2
   ```
3. Confirm users see the static page while `/api/health` still reaches the
   backend health gate exemption.
   ```bash
   curl -i https://parts.matescb.cz/
   curl -fsS https://parts.matescb.cz/api/health
   ```
4. Do the planned maintenance or incident recovery.
5. Exit maintenance mode.
   ```bash
   sudo a2disconf parts-maintenance
   sudo systemctl reload apache2
   ```

## Automatic deploy toggle

The GitHub Actions deploy script registers this trap before the pre-deploy
dump and then enables maintenance mode:

```bash
trap 'sudo a2disconf parts-maintenance && sudo systemctl reload apache2 || true' EXIT
sudo a2enconf parts-maintenance && sudo systemctl reload apache2
```

Source: `.github/workflows/ci.yml:691`. The trap must stay ahead of
`deploy/predeploy-dump.sh`; failed dumps, failed builds, failed migrations,
and failed post-deploy health gates must all disable maintenance on exit.

## Outage fallback

When the maintenance drop-in is disabled but the backend is unreachable,
Apache handles upstream 502/503/504 responses with the same static page:

```bash
curl -i https://parts.matescb.cz/
```

Expected: the response body is `deploy/maintenance.html`, not Apache's
default proxy error. The fallback is scoped to 502/503/504 so application
error envelopes are not replaced during normal request flow. Sources:
`deploy/parts.matescb.cz.conf:33` and
`deploy/parts.matescb.cz-le-ssl.conf:45`.

## Verification

- `sudo apache2ctl configtest` returns `Syntax OK`.
- Planned toggle smoke:
  ```bash
  sudo a2enconf parts-maintenance && sudo systemctl reload apache2
  curl -i https://parts.matescb.cz/
  sudo a2disconf parts-maintenance && sudo systemctl reload apache2
  curl -i https://parts.matescb.cz/
  ```
- Outage smoke:
  ```bash
  ssh vps "docker compose -f /srv/stockmanager/docker-compose.prod.yml stop backend"
  curl -i https://parts.matescb.cz/
  ssh vps "docker compose -f /srv/stockmanager/docker-compose.prod.yml start backend"
  ```
- `deploy/maintenance.html` includes a 30-second refresh and CSS pulse
  animation (`deploy/maintenance.html:6`, `deploy/maintenance.html:78`).

## Rollback

If maintenance mode sticks after the deploy or an operator command:

```bash
sudo a2disconf parts-maintenance
sudo apache2ctl configtest
sudo systemctl reload apache2
curl -fsS https://parts.matescb.cz/api/health
```

If the outage fallback itself causes trouble, remove the copied vhost change
from `/etc/apache2/sites-available/parts.matescb.cz*.conf`, run
`sudo apache2ctl configtest`, reload Apache, and open a revert PR.

## Post-mortem prompts

- Did the deploy trap run, or was maintenance left enabled manually?
- Did the fallback serve `maintenance.html` for backend 502/503/504?
- Was the Apache config installed from the checked-in deploy files?
