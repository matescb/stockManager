#!/bin/bash
# Open an interactive psql session inside the running db container.
#
# Usage (run as root on the VPS):
#   sudo /srv/stockmanager/deploy/db-shell.sh
#
# Credentials are read from .env.prod so they expand inside the container,
# not in the operator's host shell where $POSTGRES_USER / $POSTGRES_DB are
# almost certainly unset.

set -euo pipefail

REPO_DIR="/srv/stockmanager"
COMPOSE_FILE="${REPO_DIR}/docker-compose.prod.yml"
ENV_FILE="${REPO_DIR}/.env.prod"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: ${ENV_FILE} not found" >&2
    exit 1
fi

# Run as the deploy user (same as CI) to avoid stray root-owned volume files.
exec sudo -u deploy \
    docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" \
    exec db sh -c 'exec psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
