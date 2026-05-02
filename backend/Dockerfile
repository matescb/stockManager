# ── Stage 1: builder ────────────────────────────────────────────────────────
# Full Debian image so we can compile C extensions (psycopg, cryptography, …).
FROM python:3.12 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Resolve deps from pyproject.toml and pre-build wheels.
# pip-tools is used if available; uv is the fallback — whichever produces
# /tmp/deps.txt first wins.
COPY pyproject.toml ./
RUN pip install --upgrade pip pip-tools \
    && pip-compile --output-file=/tmp/deps.txt pyproject.toml 2>/dev/null \
    && pip wheel --no-cache-dir --wheel-dir=/wheels -r /tmp/deps.txt


# ── Stage 2: runtime ────────────────────────────────────────────────────────
# Slim image — only the runtime libs, no compiler toolchain.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq5 — psycopg C extension ABI dependency.
# gosu  — compose command: chowns /data as root then drops to appuser.
# curl  — healthcheck inside the container hits /api/health.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 gosu curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 1000 -m -d /home/appuser appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data

WORKDIR /app

# Bring in pre-built wheels and the resolved dep list from the builder stage.
COPY --from=builder /wheels /wheels
COPY --from=builder /tmp/deps.txt /tmp/deps.txt

# Install deps from local wheels only (no network access needed).
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/deps.txt \
    && rm -rf /wheels

# Copy application source last so source changes don't bust the dep layer.
COPY . .

# Editable install of the local package (no deps — already installed above).
RUN pip install --no-cache-dir --no-deps -e .

# /app stays root-owned (read-only at runtime; appuser doesn't need to
# mutate the source tree). USER is intentionally NOT set here — the
# compose `command:` starts as root so it can chown /data on first
# mount, then `gosu appuser` drops privileges before exec'ing uvicorn.

EXPOSE 8000
# Default for direct `docker run`. Compose overrides with the alembic +
# gosu + uvicorn one-liner; see docker-compose.prod.yml.
CMD ["sh", "-c", "chown -R appuser:appuser /data 2>/dev/null || true; exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8000"]
