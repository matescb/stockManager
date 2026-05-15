from __future__ import annotations

import urllib.parse
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "dev"
    # DATABASE_URL may be supplied directly (dev / CI / test) or assembled
    # at runtime from the discrete POSTGRES_* parts below (prod compose).
    # The model_validator handles the assembly; if DATABASE_URL is given
    # explicitly it wins unchanged — backward-compat with every existing
    # TEST_DATABASE_URL caller and with docker-compose.yml (dev).
    DATABASE_URL: str = ""

    # Discrete Postgres connection parts.  Used by docker-compose.prod.yml
    # instead of interpolating the password directly into DATABASE_URL,
    # which would cause the password to appear verbatim in
    # `docker inspect` output (INFRA2-005).
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: str = "5432"
    SESSION_SECRET: str = "dev-secret-change-me"
    PASSWORD_PEPPER: str = ""
    SESSION_COOKIE_NAME: str = "stockmgr_session"
    SESSION_LIFETIME_DAYS: int = 30
    # Sliding-expiry idle window. A session idle longer than this is
    # rejected even when the absolute lifetime has not elapsed.
    SESSION_IDLE_HOURS: int = Field(default=24, gt=0)
    # Workspace invitations expire after this many days. Operators may
    # tune this without a release; restart the backend for the env
    # override to be picked up by the cached Settings instance.
    INVITATION_TTL_DAYS: int = Field(default=14, gt=0)
    # Cadence (seconds) of the in-process expired-session purge driven
    # by the FastAPI lifespan hook. DB-007 / issue #98. Knob exists so
    # tests can shorten it; ops should not need to touch it. 0 disables
    # the periodic task entirely (the migration's index still exists,
    # so a future cron / one-off SQL still benefits).
    SESSION_PURGE_INTERVAL_SECONDS: int = 3600
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
    # parts_provider_api_secret, scanner_license_key.
    #
    # In prod the model validator below rejects an empty value — a
    # missing key fails the import loud rather than silently encrypting
    # under a fallback. In dev `app/core/secrets.py` generates a
    # per-process ephemeral key, so local runs work without a `.env`.
    #
    # Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    WORKSPACE_SECRETS_KEY: str = ""
    CORS_ORIGINS: str = "http://localhost:5173"
    # Sentry. Empty string → SDK is not initialised (no events sent, no
    # network egress, no performance overhead). Populate in .env.prod
    # only — leaving it unset in dev keeps local error stacks clean.
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float | None = None
    # Email / SMTP for the email-verification flow (SEC2-014).
    # In dev (APP_ENV != "prod") the mail backend writes to stdout so the
    # verification link surfaces in container logs without an SMTP server.
    # In prod the model_validator below rejects any of SMTP_HOST / SMTP_USER /
    # SMTP_PASSWORD / MAIL_FROM / APP_BASE_URL being empty (or APP_BASE_URL
    # being the dev default), so a misconfigured deploy fails fast at boot
    # instead of silently falling through to the stdout backend and writing
    # the verification link to container logs (issue #281).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    MAIL_FROM: str = "noreply@stockmanager.local"
    # Public-facing base URL for generating verification links.
    # In dev the default mirrors the Vite dev server; the prod validator
    # rejects this default (links must be on the public hostname).
    APP_BASE_URL: str = "http://localhost:5173"
    # Enable the two-step email-verification flow on signup (SEC2-014).
    # Defaults to True in prod, False elsewhere so the test suite can
    # use the old immediate-signup path without mocking mail.
    # Set SIGNUP_REQUIRE_EMAIL_VERIFICATION=true in .env.dev to opt in.
    SIGNUP_REQUIRE_EMAIL_VERIFICATION: bool = False
    # Release identifier — set to the git SHA at deploy time by compose.
    # Sentry groups issues per release and auto-resolves when a fixed
    # release ships.
    SENTRY_RELEASE: str = ""
    # Frontend DSN. Vite consumes it at build time; the backend reads it
    # only to allow-list the /api/sentry-tunnel forwarder against
    # exactly one Sentry project (the React one) instead of any
    # ingest.sentry.io URL the client cares to put in an envelope.
    VITE_SENTRY_DSN: str = ""

    @field_validator("SENTRY_TRACES_SAMPLE_RATE", mode="before")
    @classmethod
    def _blank_sentry_traces_rate_to_none(cls, value):
        if value == "":
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        """Assemble DATABASE_URL from POSTGRES_* parts when not supplied.

        This lets docker-compose.prod.yml pass discrete credentials instead
        of interpolating the password into a single URL variable — which
        would expose it verbatim in `docker inspect` output (INFRA2-005).

        If DATABASE_URL is provided explicitly (dev .env, CI TEST_DATABASE_URL,
        docker-compose.yml) it wins unchanged.  The assembled form uses
        urllib.parse.quote to percent-encode special characters in the
        password (e.g. @, :, /).
        """
        if not self.DATABASE_URL:
            if not all([self.POSTGRES_USER, self.POSTGRES_PASSWORD, self.POSTGRES_DB]):
                # Fall back to the dev default so unit tests that don't set
                # any DB env at all still get a usable URL.
                self.DATABASE_URL = (
                    "postgresql+psycopg://stockmgr:stockmgr@db:5432/stockmgr"
                )
            else:
                encoded_pw = urllib.parse.quote(self.POSTGRES_PASSWORD, safe="")
                self.DATABASE_URL = (
                    f"postgresql+psycopg://{self.POSTGRES_USER}:{encoded_pw}"
                    f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
                )
        return self

    @model_validator(mode="after")
    def _default_email_verification_in_prod(self) -> "Settings":
        """Force SIGNUP_REQUIRE_EMAIL_VERIFICATION=True in prod even if the
        operator forgets to set it, so the security feature is always on
        in production (SEC2-014)."""
        if self.APP_ENV == "prod":
            self.SIGNUP_REQUIRE_EMAIL_VERIFICATION = True
        return self

    @model_validator(mode="after")
    def _require_workspace_secrets_key_in_prod(self) -> "Settings":
        # The 2026-04-30 review (Sec HIGH-9) and the 2026-05-01 v2
        # teardown (INFRA2-004 / SEC2-002) both turn on this exact gap:
        # a prod deploy that forgets `WORKSPACE_SECRETS_KEY` would
        # encrypt every workspace's third-party credentials under the
        # process's fallback key — useless. Failing closed at import is
        # the only way to surface the misconfig before data is written.
        if self.APP_ENV == "prod" and not self.WORKSPACE_SECRETS_KEY:
            raise ValueError(
                "WORKSPACE_SECRETS_KEY is required when APP_ENV=prod. "
                "Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        return self

    @model_validator(mode="after")
    def _require_password_pepper_in_prod(self) -> "Settings":
        if self.APP_ENV == "prod" and not self.PASSWORD_PEPPER:
            raise ValueError(
                "PASSWORD_PEPPER is required when APP_ENV=prod. Generate a high-entropy "
                "secret and escrow it alongside SESSION_SECRET."
            )
        return self

    @model_validator(mode="after")
    def _require_smtp_in_prod(self) -> "Settings":
        # Issue #281: in prod the email-verification flow is forced on
        # (see _default_email_verification_in_prod above), but if any of
        # the SMTP creds are missing the mail backend silently fell
        # through to stdout, leaking the verification link into
        # `docker compose logs backend`. Fail closed at import so a
        # misconfigured deploy never gets the chance to mint a single
        # token. The error message lists the missing variable names only
        # — never the values — so it is safe to paste into a bug report.
        if self.APP_ENV == "prod":
            missing: list[str] = []
            for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM"):
                if not getattr(self, name):
                    missing.append(name)
            if not self.APP_BASE_URL or self.APP_BASE_URL == "http://localhost:5173":
                missing.append("APP_BASE_URL")
            if missing:
                raise ValueError(
                    "Email verification is mandatory when APP_ENV=prod, but the "
                    "following required variables are missing or set to a dev "
                    f"default: {', '.join(missing)}. Set them in .env.prod "
                    "before deploying — see deploy/.env.prod.example."
                )
        return self

    @model_validator(mode="after")
    def _require_sentry_traces_rate_in_prod(self) -> "Settings":
        if self.APP_ENV == "prod" and self.SENTRY_TRACES_SAMPLE_RATE is None:
            raise ValueError(
                "SENTRY_TRACES_SAMPLE_RATE is required when APP_ENV=prod. "
                "Set an explicit low production rate, e.g. 0.05."
            )
        if self.SENTRY_TRACES_SAMPLE_RATE is not None and not (
            0.0 <= self.SENTRY_TRACES_SAMPLE_RATE <= 1.0
        ):
            raise ValueError("SENTRY_TRACES_SAMPLE_RATE must be between 0.0 and 1.0.")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
