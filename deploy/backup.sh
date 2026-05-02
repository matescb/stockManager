#!/bin/bash
# Nightly backup of the parts-inventory app.
#
# Produces two artifacts in /srv/backups/stockmanager/:
#   db-YYYY-MM-DD.sql.gz.age      — pg_dump of the running Postgres, age-encrypted
#   uploads-YYYY-MM-DD.tar.gz.age — full tar of the docker uploads volume, age-encrypted
#
# Retention: keep the last 30 days of each. Older files are deleted in
# place. To restore, see docs/deployment.md.
#
# Run via root cron:
#   30 3 * * * /srv/stockmanager/deploy/backup.sh >> /var/log/stockmanager-backup.log 2>&1
#
# Errors are surfaced via the cron log + the script's non-zero exit code.
# The cron daemon emails root on failure if MTA is configured.
#
# Dead-man's-switch alerting (optional, but strongly recommended):
#   Set BACKUP_HEALTHCHECK_OK_URL and BACKUP_HEALTHCHECK_FAIL_URL in .env.prod
#   to ping a monitoring service (e.g. healthchecks.io) on success/failure.
#   If only OK_URL is set the service alerts you when pings stop arriving.
#   If FAIL_URL is also set the service alerts immediately on each failure.
#   Leave both empty to rely solely on cron mail (the previous behaviour).
#
# Prerequisites:
#   - age must be installed on the VPS (https://github.com/FiloSottile/age)
#     Install: https://github.com/FiloSottile/age/releases — pick the static
#     linux-amd64 binary, drop it at /usr/local/bin/age, chmod +x.
#   - BACKUP_AGE_RECIPIENT must be set in .env.prod (the age public key)
#   - The corresponding age private key must be escrowed off-VPS

set -euo pipefail

# ---- Dead-man's-switch URLs (optional) ----
# Read from .env.prod if present; fall back to empty (no-op).
BACKUP_HEALTHCHECK_OK_URL="${BACKUP_HEALTHCHECK_OK_URL:-}"
BACKUP_HEALTHCHECK_FAIL_URL="${BACKUP_HEALTHCHECK_FAIL_URL:-}"

on_failure() {
    local rc=$?
    if [ -n "$BACKUP_HEALTHCHECK_FAIL_URL" ]; then
        curl -fsS --max-time 10 "$BACKUP_HEALTHCHECK_FAIL_URL" >/dev/null 2>&1 || true
    fi
    exit $rc
}
trap on_failure ERR

REPO_DIR="/srv/stockmanager"
BACKUP_DIR="/srv/backups/stockmanager"
RETAIN_DAYS=30
COMPOSE_FILE="${REPO_DIR}/docker-compose.prod.yml"
ENV_FILE="${REPO_DIR}/.env.prod"
TS="$(date +%F)"

# Minimum acceptable sizes — fail fast if the artifact looks implausibly small
DB_MIN_BYTES=1024
UPLOADS_MIN_BYTES=512

mkdir -p "${BACKUP_DIR}"

# ---- Read credentials from env file ----
# Read POSTGRES_USER / _DB out of the env file so this script doesn't drift
# when those values change. We don't need the password — the docker exec
# inherits the running container's env.
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "${ENV_FILE}" | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "${ENV_FILE}" | cut -d= -f2-)"
BACKUP_AGE_RECIPIENT="$(grep -E '^BACKUP_AGE_RECIPIENT=' "${ENV_FILE}" | cut -d= -f2-)"

if [[ -z "${BACKUP_AGE_RECIPIENT}" ]]; then
    echo "$(date -Iseconds)  ERROR: BACKUP_AGE_RECIPIENT not set in ${ENV_FILE}" >&2
    exit 1
fi

# verify_age_artifact <path> <min_bytes> [check_gzip]
#   Checks:
#     1. File size is above min_bytes (implausibility guard)
#     2. File starts with the age armored header (confirms encryption completed)
#     3. (DB only) Pipe-decrypt the raw ciphertext stream and confirm the
#        inner gzip stream is intact via gunzip -t.
#        NOTE: step 3 requires AGE_IDENTITY_FILE to point to the private key
#        if the variable is set; it is skipped silently when the variable is
#        unset (VPS holds only the recipient pubkey).
verify_age_artifact() {
    local path="$1"
    local min_bytes="$2"
    local check_gzip="${3:-}"

    # 1. Size floor
    local actual_size
    actual_size="$(stat -c%s "${path}")"
    if [[ "${actual_size}" -le "${min_bytes}" ]]; then
        echo "$(date -Iseconds)  ERROR: ${path} implausibly small (${actual_size} bytes)" >&2
        return 1
    fi

    # 2. age armored header  — binary age files start with "age-encryption.org"
    local header
    header="$(head -c 20 "${path}")"
    if [[ "${header}" != age-encryption.org* ]]; then
        echo "$(date -Iseconds)  ERROR: ${path} does not begin with an age header" >&2
        return 1
    fi

    # 3. Optional gzip stream integrity (requires private key)
    if [[ -n "${check_gzip}" && -n "${AGE_IDENTITY_FILE:-}" ]]; then
        if ! age -d -i "${AGE_IDENTITY_FILE}" "${path}" | gunzip -t; then
            echo "$(date -Iseconds)  ERROR: ${path} gzip stream integrity check failed" >&2
            return 1
        fi
    fi

    return 0
}

# ---- Postgres dump ----
DB_OUT="${BACKUP_DIR}/db-${TS}.sql.gz.age"
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" exec -T db \
    pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
    | gzip \
    | age -r "${BACKUP_AGE_RECIPIENT}" > "${DB_OUT}.tmp"

if ! verify_age_artifact "${DB_OUT}.tmp" "${DB_MIN_BYTES}" "check_gzip"; then
    echo "$(date -Iseconds)  ERROR: db dump verification failed; leaving ${DB_OUT}.tmp in place" >&2
    exit 1
fi

mv "${DB_OUT}.tmp" "${DB_OUT}"
echo "$(date -Iseconds)  db dump  $(du -h "${DB_OUT}" | cut -f1)  ${DB_OUT}"

# ---- Uploads tarball ----
# pg_dump doesn't cover lot photos / datasheets stored in the `uploads`
# docker volume — tar them separately.
UPLOADS_OUT="${BACKUP_DIR}/uploads-${TS}.tar.gz.age"
docker run --rm \
    -v stockmanager_uploads:/u:ro \
    alpine \
    sh -c "tar czf - -C /u ." \
    | age -r "${BACKUP_AGE_RECIPIENT}" > "${UPLOADS_OUT}.tmp"

if ! verify_age_artifact "${UPLOADS_OUT}.tmp" "${UPLOADS_MIN_BYTES}"; then
    echo "$(date -Iseconds)  ERROR: uploads tarball verification failed; leaving ${UPLOADS_OUT}.tmp in place" >&2
    exit 1
fi

mv "${UPLOADS_OUT}.tmp" "${UPLOADS_OUT}"
echo "$(date -Iseconds)  uploads  $(du -h "${UPLOADS_OUT}" | cut -f1)  ${UPLOADS_OUT}"

# ---- Retention prune ----
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'db-*.sql.gz.age' -mtime "+${RETAIN_DAYS}" -delete
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'uploads-*.tar.gz.age' -mtime "+${RETAIN_DAYS}" -delete

echo "$(date -Iseconds)  backup OK"

# ---- Dead-man's-switch: ping success URL ----
if [ -n "$BACKUP_HEALTHCHECK_OK_URL" ]; then
    curl -fsS --max-time 10 "$BACKUP_HEALTHCHECK_OK_URL" >/dev/null || echo "warning: backup OK ping failed"
fi
