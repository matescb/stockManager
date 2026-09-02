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
# Output: /srv/backups/stockmanager/pre-deploy-${TS}-${SHA}.sql.gz.age
#
# Encryption: the dump is piped through `age -r $BACKUP_AGE_RECIPIENT`
# before it ever lands on disk, mirroring the off-host nightlies in
# `backup.sh`. The private key is escrowed off-VPS — see
# docs/deployment.md#backups. Closes #287 (INFRA2-016 follow-up:
# pre-deploy dumps were previously gzip-only, not encrypted at rest).
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
BACKUP_AGE_RECIPIENT="$(grep -E '^BACKUP_AGE_RECIPIENT=' "${ENV_FILE}" | cut -d= -f2-)"

if [[ -z "${POSTGRES_USER}" || -z "${POSTGRES_DB}" ]]; then
    echo "ERROR: POSTGRES_USER / POSTGRES_DB missing from ${ENV_FILE}" >&2
    exit 1
fi

# Refuse to run if the age recipient is missing or still the placeholder
# from .env.prod.example — an unencrypted dump on the VPS is exactly the
# exposure this script is being hardened against (issue #287).
if [[ -z "${BACKUP_AGE_RECIPIENT}" || "${BACKUP_AGE_RECIPIENT}" == "age1..." ]]; then
    echo "ERROR: BACKUP_AGE_RECIPIENT not configured in ${ENV_FILE} — refusing to write unencrypted dump" >&2
    exit 1
fi

OUT="${BACKUP_DIR}/pre-deploy-${TS}-${SHA}.sql.gz.age"

echo "$(date -Iseconds)  pre-deploy dump  ${SHA}  ->  ${OUT}"

# Fail loudly if the running db container is missing — the dump is the
# whole point; a deploy that skips it is exactly what this script
# exists to prevent.
if ! docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" ps --status running --services | grep -qx db; then
    echo "ERROR: db container not running — refusing to deploy without a snapshot" >&2
    exit 1
fi

# `< /dev/null` is load-bearing: `compose exec` attaches the caller's
# stdin by default (-T only drops the TTY). This script runs inside the
# deploy's `ssh 'bash -se' <<HEREDOC` — without the starvation, exec
# SWALLOWS the remainder of the deploy script off stdin, bash sees EOF
# after the dump, and the deploy "succeeds" without ever running
# `docker compose up`. That failure mode shipped nothing for a night
# while every job stayed green.
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" exec -T db \
    pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" < /dev/null \
    | gzip \
    | age -r "${BACKUP_AGE_RECIPIENT}" > "${OUT}.tmp"
mv "${OUT}.tmp" "${OUT}"

# pg_dump emits non-empty output even for an empty schema (header,
# extensions, etc). A truly empty file means the pipe broke; treat
# that as a dump failure.
if [[ ! -s "${OUT}" ]]; then
    echo "ERROR: dump file is empty — assuming pg_dump failed" >&2
    rm -f "${OUT}"
    exit 1
fi

echo "$(date -Iseconds)  pre-deploy dump OK (age-encrypted)  $(du -h "${OUT}" | cut -f1)"

# Retention: keep the most recent N pre-deploy dumps. Sort by mtime
# descending, skip the first N, delete the rest. NB: the glob is
# .sql.gz.age — older plain-gzip dumps from before #287 are NOT matched
# and must be cleaned up manually (one-shot; see docs/deployment.md).
ls -1t "${BACKUP_DIR}"/pre-deploy-*.sql.gz.age 2>/dev/null \
    | tail -n +$((RETAIN_PREDEPLOY + 1)) \
    | xargs -r rm -v
