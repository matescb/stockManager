FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# `gosu` lets the entrypoint chown the upload volume as root (required
# the first time a fresh named volume is mounted on top of /data/uploads,
# or after upgrading from a previous image that ran as root) and then
# drop privileges before exec'ing uvicorn. Without it the runtime would
# either stay root forever (Sec HIGH-2 / Infra CRIT-3) or fail to write
# to a root-owned upload volume.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 1000 -m -d /home/appuser appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

COPY . .

# /app stays root-owned (read-only at runtime; appuser doesn't need to
# mutate the source tree). USER is intentionally NOT set here — the
# compose `command:` starts as root so it can chown /data on first
# mount, then `gosu appuser` drops privileges before exec'ing uvicorn.

EXPOSE 8000
# Default for direct `docker run`. Compose overrides with the alembic +
# gosu + uvicorn one-liner; see docker-compose.prod.yml.
CMD ["sh", "-c", "chown -R appuser:appuser /data 2>/dev/null || true; exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8000"]
