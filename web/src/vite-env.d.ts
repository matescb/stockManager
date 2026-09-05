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
  /**
   * Build-time release identifier (the deploy's 12-char git short SHA).
   * Used as Sentry's `release` tag and shown on `/about` next to the
   * backend's own build id. Absent in a plain `vite dev` run.
   */
  readonly VITE_APP_VERSION?: string;
  /**
   * ISO-8601 UTC timestamp of the bundle build, passed as a Docker build
   * arg. Shown on `/about`; absent means "no timestamp available", which
   * the page renders as nothing rather than as a guess.
   */
  readonly VITE_BUILD_TIME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
