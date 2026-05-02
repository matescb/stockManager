#!/bin/bash
# Pre-deploy DB snapshot. Runs from the SSH deploy step in
# .github/workflows/ci.yml AFTER `git reset --hard origin/main` and
# BEFORE `docker compose up --build`. Captures the state of the
# running Postgres container before any new alembic migration applies.
#
# Closes 2026-04-30 review's Infra CRIT-1 ("no rollback path for
# destructive migrations") and the v2 teardown's INFRA2-001
# ("automated DB backup before destructive deploys"). On dump failure,
# this script exits non-zero so the SSH deploy aborts and the existing
# container keeps serving the old code.
#
# Output: /srv/backups/stockmanager/pre-deploy-${TS}-${SHA}.sql.gz
#
# Retention: separate from the nightly cron-driven backups in
# `backup.sh`. Keep the last 14 pre-deploy dumps. Rationale: nightlies
# are the canonical retention; pre-deploy dumps are a short-window
# safety net for the deploy itself.
#
# Args:
#   $1 — short git SHA being deployed (used in the filename so an
#         operator can tie a dump to the deploy that triggered it).
#         Optional; defaults to "unknown" if missing.

set -euo pipefail

REPO_DIR="/srv/stockmanager"
BACKUP_DIR="/srv/backups/stockmanager"
RETAIN_PREDEPLOY=14
COMPOSE_FILE="${REPO_DIR}/docker-compose.prod.yml"
ENV_FILE="${REPO_DIR}/.env.prod"
SHA="${1:-unknown}"
TS="$(date +%FT%H%M)"

mkdir -p "${BACKUP_DIR}"

POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "${ENV_FILE}" | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "${ENV_FILE}" | cut -d= -f2-)"

if [[ -z "${POSTGRES_USER}" || -z "${POSTGRES_DB}" ]]; then
    echo "ERROR: POSTGRES_USER / POSTGRES_DB missing from ${ENV_FILE}" >&2
    exit 1
fi

OUT="${BACKUP_DIR}/pre-deploy-${TS}-${SHA}.sql.gz"

echo "$(date -Iseconds)  pre-deploy dump  ${SHA}  ->  ${OUT}"

# Fail loudly if the running db container is missing — the dump is the
# whole point; a deploy that skips it is exactly what this script
# exists to prevent.
if ! docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" ps --status running --services | grep -qx db; then
    echo "ERROR: db container not running — refusing to deploy without a snapshot" >&2
    exit 1
fi

docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" exec -T db \
    pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
    | gzip > "${OUT}.tmp"
mv "${OUT}.tmp" "${OUT}"

# pg_dump emits non-empty output even for an empty schema (header,
# extensions, etc). A truly empty file means the pipe broke; treat
# that as a dump failure.
if [[ ! -s "${OUT}" ]]; then
    echo "ERROR: dump file is empty — assuming pg_dump failed" >&2
    rm -f "${OUT}"
    exit 1
fi

echo "$(date -Iseconds)  pre-deploy dump OK  $(du -h "${OUT}" | cut -f1)"

# Retention: keep the most recent N pre-deploy dumps. Sort by mtime
# descending, skip the first N, delete the rest.
ls -1t "${BACKUP_DIR}"/pre-deploy-*.sql.gz 2>/dev/null \
    | tail -n +$((RETAIN_PREDEPLOY + 1)) \
    | xargs -r rm -v
