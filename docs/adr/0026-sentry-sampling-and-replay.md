# ADR-0026: Sentry traces are explicitly sampled; Replay is opt-in

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-14
- **Supersedes**: —
- **Superseded by**: —

## Context

AUD-015 found two unsafe observability defaults:

1. The frontend `tracesSampleRate` fell back to `1.0` when the build env was unset or invalid. That can turn one bad deploy into 100% transaction capture and a Sentry quota/cost burst.
2. Session Replay was enabled at 10% of all sessions and 100% of error sessions. Even with `maskAllText` and `blockAllMedia`, Replay still captures DOM structure, route context, and user workflow timing.

The backend already used a low default (`0.0`), but prod should not silently inherit sampling defaults from code. Sampling is an operational budget and privacy decision, so the production value must be explicit in the deploy env.

## Decision

1. Production traces use `0.05` as the pinned default in `deploy/.env.prod.example` for both backend `SENTRY_TRACES_SAMPLE_RATE` and frontend `VITE_SENTRY_TRACES_SAMPLE_RATE`.
2. Backend `Settings` refuses to construct when `APP_ENV == "prod"` and `SENTRY_TRACES_SAMPLE_RATE` is missing or blank. The value must be between `0.0` and `1.0`.
3. The production web Docker build refuses to build without `VITE_SENTRY_TRACES_SAMPLE_RATE`. The frontend parser also rejects missing production trace sampling and any non-numeric/out-of-range sample rate.
4. Session Replay defaults to disabled: `VITE_SENTRY_REPLAYS_SESSION_SAMPLE_RATE=0.0` and `VITE_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE=0.0`. Enabling Replay requires changing explicit env values and revisiting this ADR or adding a superseding ADR with the privacy rationale.

## Consequences

- **Good**:
  - Missing prod sampling config fails closed before the app serves traffic.
  - The documented steady-state traces budget is low and symmetrical across backend and frontend.
  - Replay does not capture DOM state unless an operator deliberately opts in.
- **Trade-offs**:
  - Prod deploys must carry trace sample-rate env values even when the DSNs are empty. This is intentional: the sampling posture stays explicit before Sentry is enabled.
  - A future incident may justify temporarily raising sampling or Replay rates, but that change should be time-boxed and documented.
- **What it forbids**:
  - Do not restore `1.0` or invalid-env fallbacks for frontend traces.
  - Do not add `:-0.0` defaults for production trace sample rates in `docker-compose.prod.yml`.
  - Do not enable Session Replay by default.

## Alternatives considered

- **Keep Replay at 10% with masking** — rejected. Masking reduces payload sensitivity, but DOM shape and workflow timing are still telemetry. Default-off is the safer baseline.
- **Set traces to `0.1`** — viable, but `0.05` is the more conservative value inside the issue's accepted `0.05` to `0.1` range.
- **Only require sample rates when DSNs are set** — rejected. That keeps the old class of misconfiguration alive; a later DSN-only change could silently enable whatever code default exists at that time.

## References

- Source: `backend/app/core/config.py` (`_require_sentry_traces_rate_in_prod`)
- Source: `web/src/instrument.ts` (sample-rate parsing and Replay defaults)
- Source: `web/Dockerfile.prod` (prod build-time trace sample-rate guard)
- Required env: `deploy/.env.prod.example`
- Tests: `backend/tests/test_config.py`, `web/src/instrument.test.ts`
- Issue: #554
