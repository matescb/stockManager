# ADR-0018: Prod email verification requires SMTP; never log verification tokens

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

The signup flow issues an email-verification link. The implementation had two backends: an SMTP backend (prod) and a stdout backend (dev — writes the verification link to container logs / stdout for local testing).

A prod deploy that forgot to set `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` / `APP_BASE_URL` would silently fall through to the stdout backend, leaking verification tokens to whoever could read the production container logs (Sentry breadcrumbs, journald, log shippers, future-self via `docker logs`). Issue #281.

Two failure modes had to be closed at once:

1. **Misconfigured prod deploy** — boot must fail loudly, not silently degrade to a logged-token mode.
2. **Defence in depth** — even if someone re-introduces the dev backend by accident, it must refuse to run in prod and must never log the verification link.

## Decision

1. `Settings` carries a `_require_smtp_in_prod` validator (`backend/app/core/config.py`). When `APP_ENV == "prod"`, all of `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `APP_BASE_URL` must be non-empty and not the dev defaults. Any missing or default value raises `ValueError` at import; the container never starts.
2. The stdout mail backend (`backend/app/core/mail.py::_send_stdout`) raises `RuntimeError` if invoked under `APP_ENV == "prod"`, and the verification link is **never logged** by either backend — the production code path uses SMTP and never writes the link to logger/stdout/print.
3. A regression test (`backend/tests/test_mail_prod_safety.py`) pins all three behaviours: prod-fail-closed-on-empty-SMTP, dev-still-works-with-empty-SMTP, prod-stdout-backend-RuntimeError-and-no-log-line.

## Consequences

- **Good**:
  - A prod deploy that forgets any SMTP variable fails immediately and visibly, not silently in a token-leaking mode.
  - The stdout backend's continued existence (for dev convenience) cannot harm prod — it actively refuses to run there.
  - The regression test makes it harder to re-open the leak by accident.
- **Trade-offs**:
  - Boot-time validation means env mistakes surface as `ValueError` from a pydantic validator, which is less ergonomic than a startup health-check log line. Acceptable: fail-fast beats fail-quiet for a security invariant.
  - Dev deploys still need `MAIL_FROM` and `APP_BASE_URL` to be set when testing the SMTP backend locally; the validator only applies in prod, but local-prod-mirror runs need the same values.
- **What it forbids**:
  - Don't add any path that lets prod boot without all five SMTP vars. The validator is the single check; bypassing it (e.g. with a `model_post_init` override) is the bug.
  - Don't log the verification link or token from either backend, ever. Don't add it to a Sentry breadcrumb. Don't put it in a debug print. Don't include it in a structured-log field.
  - Don't relax the `_send_stdout` prod-refusal. The dev backend is dev-only.

## Alternatives considered

- **Log the link only at DEBUG level** — rejected. Log levels are operational toggles, not security boundaries; a future on-call enabling DEBUG to debug an unrelated issue would re-leak the token.
- **Keep the stdout fallback in prod but redact the link** — rejected. Redaction is easy to break (one careful `.format()` call brings it back); the cleanest invariant is "the verification token never enters a writable log channel in prod".
- **Health-check at runtime instead of import-time** — rejected. Misconfigured prod would still serve traffic for the duration of the first request, and the alert would fire later than the deploy.

## References

- Source: `backend/app/core/config.py` (`_require_smtp_in_prod` validator)
- Source: `backend/app/core/mail.py::_send_stdout` (prod refusal)
- Test: `backend/tests/test_mail_prod_safety.py` (the regression contract)
- Required env: `deploy/.env.prod.example` lists the five SMTP vars
- PR: #297 (`fix(security): refuse to boot prod without SMTP; never log verification link`)
- Issue: #281
- Related ADRs: [ADR-0015](0015-digest-pinned-base-images.md) (also fail-closed at boot), [ADR-0011](0011-secure-cookie-env-gated.md) (also `APP_ENV`-gated)
