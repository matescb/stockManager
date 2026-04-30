#!/bin/bash
# Nightly backup of the parts-inventory app.
#
# Produces two artifacts in /srv/backups/stockmanager/:
#   db-YYYY-MM-DD.sql.gz       — pg_dump of the running Postgres
#   uploads-YYYY-MM-DD.tar.gz  — full tar of the docker uploads volume
#
# Retention: keep the last 30 days of each. Older files are deleted in
# place. To restore, see docs/deployment.md.
#
# Run via root cron:
#   30 3 * * * /srv/stockmanager/deploy/backup.sh >> /var/log/stockmanager-backup.log 2>&1
#
# Errors are surfaced via the cron log + the script's non-zero exit code.
# The cron daemon emails root on failure if MTA is configured.

set -euo pipefail

REPO_DIR="/srv/stockmanager"
BACKUP_DIR="/srv/backups/stockmanager"
RETAIN_DAYS=30
COMPOSE_FILE="${REPO_DIR}/docker-compose.prod.yml"
ENV_FILE="${REPO_DIR}/.env.prod"
TS="$(date +%F)"

mkdir -p "${BACKUP_DIR}"

# ---- Postgres dump ----
# Read POSTGRES_USER / _DB out of the env file so this script doesn't drift
# when those values change. We don't need the password — the docker exec
# inherits the running container's env.
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "${ENV_FILE}" | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "${ENV_FILE}" | cut -d= -f2-)"

DB_OUT="${BACKUP_DIR}/db-${TS}.sql.gz"
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" exec -T db \
    pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
    | gzip > "${DB_OUT}.tmp"
mv "${DB_OUT}.tmp" "${DB_OUT}"
echo "$(date -Iseconds)  db dump  $(du -h "${DB_OUT}" | cut -f1)  ${DB_OUT}"

# ---- Uploads tarball ----
# pg_dump doesn't cover lot photos / datasheets stored in the `uploads`
# docker volume — tar them separately.
UPLOADS_OUT="${BACKUP_DIR}/uploads-${TS}.tar.gz"
docker run --rm \
    -v stockmanager_uploads:/u:ro \
    -v "${BACKUP_DIR}":/out \
    alpine \
    sh -c "tar czf /out/uploads-${TS}.tar.gz.tmp -C /u . && mv /out/uploads-${TS}.tar.gz.tmp /out/uploads-${TS}.tar.gz"
echo "$(date -Iseconds)  uploads  $(du -h "${UPLOADS_OUT}" | cut -f1)  ${UPLOADS_OUT}"

# ---- Retention prune ----
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'db-*.sql.gz' -mtime "+${RETAIN_DAYS}" -delete
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'uploads-*.tar.gz' -mtime "+${RETAIN_DAYS}" -delete

echo "$(date -Iseconds)  backup OK"
