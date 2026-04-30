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
    CORS_ORIGINS: str = "http://localhost:5173"
    # Sentry. Empty string → SDK is not initialised (no events sent, no
    # network egress, no performance overhead). Populate in .env.prod
    # only — leaving it unset in dev keeps local error stacks clean.
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def settings() -> Settings:
    return Settings()
