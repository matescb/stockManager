from __future__ import annotations

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "dev"
    DATABASE_URL: str = "postgresql+psycopg://stockmgr:stockmgr@db:5432/stockmgr"
    SESSION_SECRET: str = "dev-secret-change-me"
    SESSION_COOKIE_NAME: str = "stockmgr_session"
    SESSION_LIFETIME_DAYS: int = 30
    UPLOAD_DIR: str = "/data/uploads"
    # Per-upload size cap. nginx (in web container) and Apache (in front)
    # both cap at 25 MiB, but FastAPI must enforce its own cap so a
    # direct hit on the backend port — or any future change to those
    # proxy limits — doesn't let a single request consume unbounded
    # memory. 10 MiB is plenty for part photos and most datasheets;
    # bigger PDFs that fail with 413 are the operator's signal to
    # compress or to bump this number.
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
    # Maximum size of a single Sentry envelope forwarded through
    # /api/sentry-tunnel, in bytes. Default 200 KiB. Real envelopes from
    # the React SDK are typically <50 KiB; this caps abuse without
    # truncating legitimate replays. The route is unauthenticated by
    # design (Sentry SDKs don't carry a session), so without this cap
    # the tunnel is an open ingress that anyone on the internet can
    # use to pump arbitrary bytes through this worker.
    SENTRY_TUNNEL_MAX_BYTES: int = 200 * 1024
    # Fernet key (urlsafe-base64-encoded 32 bytes) for encrypting
    # workspace-level secrets at rest: parts_provider_api_key,
    # parts_provider_api_secret, scanner_license_key. Empty → falls
    # back to a dev-only default (see app/core/secrets.py) with a
    # warning logged on first use. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    WORKSPACE_SECRETS_KEY: str = ""
    CORS_ORIGINS: str = "http://localhost:5173"
    # Sentry. Empty string → SDK is not initialised (no events sent, no
    # network egress, no performance overhead). Populate in .env.prod
    # only — leaving it unset in dev keeps local error stacks clean.
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    # Release identifier — set to the git SHA at deploy time by compose.
    # Sentry groups issues per release and auto-resolves when a fixed
    # release ships.
    SENTRY_RELEASE: str = ""
    # Frontend DSN. Vite consumes it at build time; the backend reads it
    # only to allow-list the /api/sentry-tunnel forwarder against
    # exactly one Sentry project (the React one) instead of any
    # ingest.sentry.io URL the client cares to put in an envelope.
    VITE_SENTRY_DSN: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def settings() -> Settings:
    return Settings()
