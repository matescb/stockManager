/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Sentry frontend DSN. Empty → SDK not initialised. */
  readonly VITE_SENTRY_DSN?: string;
  /** Sentry traces sample rate (0.0..1.0). String at build time, parsed in initSentry. */
  readonly VITE_SENTRY_TRACES_SAMPLE_RATE?: string;
  /** Sentry Replay full-session sample rate (0.0..1.0). Defaults to 0.0. */
  readonly VITE_SENTRY_REPLAYS_SESSION_SAMPLE_RATE?: string;
  /** Sentry Replay error-session sample rate (0.0..1.0). Defaults to 0.0. */
  readonly VITE_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE?: string;
  /** Build-time release identifier (git SHA). Used as Sentry's `release` tag. */
  readonly VITE_APP_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
