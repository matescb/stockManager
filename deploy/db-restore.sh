#!/bin/bash
# Decrypt and restore a pg_dump backup into the running db container.
#
# Usage (run as root on the VPS):
#   sudo /srv/stockmanager/deploy/db-restore.sh \
#       /path/to/backup-key.txt \
#       /srv/backups/stockmanager/db-YYYY-MM-DD.sql.gz.age
#
# This is DESTRUCTIVE: it pipes the SQL directly into psql which overwrites
# the existing database. An explicit confirmation prompt guards against
# accidental runs.
#
# Credentials are read from .env.prod so they expand inside the container,
# not in the operator's host shell where $POSTGRES_USER / $POSTGRES_DB are
# almost certainly unset (mirrors the pattern in deploy/backup.sh).

set -euo pipefail

REPO_DIR="/srv/stockmanager"
COMPOSE_FILE="${REPO_DIR}/docker-compose.prod.yml"
ENV_FILE="${REPO_DIR}/.env.prod"

# ---- Argument validation ----
if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <age-key-file> <backup-file.sql.gz.age>" >&2
    exit 1
fi

KEY_FILE="$1"
BACKUP_FILE="$2"

if [[ ! -f "${KEY_FILE}" ]]; then
    echo "ERROR: key file not found: ${KEY_FILE}" >&2
    exit 1
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "ERROR: backup file not found: ${BACKUP_FILE}" >&2
    exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: ${ENV_FILE} not found" >&2
    exit 1
fi

# ---- Confirmation prompt ----
echo "WARNING: This will OVERWRITE the production database."
echo "  Backup : ${BACKUP_FILE}"
echo "  Target : db container in ${COMPOSE_FILE}"
echo ""
read -r -p "Type YES to continue: " CONFIRM
if [[ "${CONFIRM}" != "YES" ]]; then
    echo "Aborted." >&2
    exit 1
fi

# ---- Restore ----
echo "Restoring..."
age -d -i "${KEY_FILE}" "${BACKUP_FILE}" \
    | gunzip -c \
    | sudo -u deploy \
        docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" \
        exec -T db sh -c 'exec psql -U "$POSTGRES_USER" "$POSTGRES_DB"'

echo "Restore complete."
