# ── Stage 1: builder ────────────────────────────────────────────────────────
# Full Debian image so we can compile C extensions (psycopg, cryptography, …).
# Digest pinned on 2026-09-02; bump via Dependabot (docker ecosystem).
FROM python:3.14@sha256:8edbf9e42c7fb168b9c523718ed907117e6d2e60f5889c0c499bbda3a787da53 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Pin uv so the builder layer is reproducible.
RUN pip install --no-cache-dir uv==0.11.8

WORKDIR /build

# Copy the manifest and lockfile first so dep resolution is cached
# independently of source-code changes.
COPY pyproject.toml uv.lock ./

# Create a virtualenv and install production deps from the frozen lockfile.
# --no-dev: exclude the [dev] optional-dependencies group.
# --frozen: refuse to proceed if uv.lock is out of sync with pyproject.toml.
RUN uv sync --frozen --no-dev


# ── Stage 2: runtime ────────────────────────────────────────────────────────
# Slim image — only the runtime libs, no compiler toolchain.
# Digest pinned on 2026-09-02; bump via Dependabot (docker ecosystem).
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/build/.venv/bin:$PATH"

# libpq5 — psycopg C extension ABI dependency.
# curl  — healthcheck inside the container hits /api/health.
# gosu removed — /data ownership is now fixed by the backend-init one-shot
# service (see docker-compose.prod.yml) before this container starts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 1000 -m -d /home/appuser appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data

# Bring in the pre-populated virtualenv from the builder stage.
COPY --from=builder /build/.venv /build/.venv

WORKDIR /app

# Copy application source last so source changes don't bust the dep layer.
COPY . .

# /app stays root-owned (read-only at runtime; appuser doesn't need to
# mutate the source tree).
USER appuser

EXPOSE 8000
# Default for direct `docker run`. Compose overrides this; see docker-compose.prod.yml.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
