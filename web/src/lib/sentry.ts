/**
 * Frontend Sentry init. Vite inlines `import.meta.env.VITE_*` at build
 * time, so an empty DSN means we never even import the SDK runtime —
 * keeps the bundle small for self-hosted forks that don't want
 * third-party error reporting.
 *
 * The DSN is intentionally public (Sentry's threat model treats it as a
 * project identifier, not a write-credential). It does land in DevTools
 * for anyone inspecting the bundle, by design.
 */
export async function initSentry(): Promise<void> {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return;

  // Dynamically import so the SDK chunk isn't pulled into the main bundle
  // when DSN is unset (the static analysis above isn't enough for some
  // bundlers — keeping the import dynamic is the robust path).
  const Sentry = await import("@sentry/react");
  const tracesSampleRate = parseFloat(
    import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? "0.0"
  );
  Sentry.init({
    dsn,
    environment: import.meta.env.MODE,
    tracesSampleRate: Number.isFinite(tracesSampleRate) ? tracesSampleRate : 0,
    // Don't auto-attach request bodies — same posture as the backend.
    sendDefaultPii: false,
  });
}
