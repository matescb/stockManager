# core

Audience: engineer

Cross-cutting infra: response envelope, request deps (auth + workspace + role), session + password handling, mail, secrets encryption, pagination, rate limiting, request IDs, time helpers, structured logging.

## Files

| File | What |
|---|---|
| `config.py` | Pydantic Settings — env vars, `APP_ENV`, `UPLOAD_DIR`, etc. |
| `deps.py` | `get_current_user`, `get_current_workspace`, `require_role`, `require_member_for_writes` |
| `auth.py` | Password hash + strength (HIBP), session row issue / hash / revoke, login lockout |
| `responses.py` | `ok` / `err`, `Envelope` typed dict, `http_exception_handler`, `validation_exception_handler` |
| `errors.py` | Domain error classes that map to HTTP status |
| `pagination.py` | `Cursor`, `encode_cursor` / `decode_cursor` (signed), `paginate` helper |
| `mail.py` | Verification email sender (stdout in dev, SMTP in prod) |
| `secrets.py` | Fernet `encrypt` / `decrypt` / `safe_decrypt` for per-workspace provider creds |
| `_secrets_v0016.py` | Migration-time helper used by alembic 0016; do not call from app code |
| `ratelimit.py` | slowapi limiter instance + per-route decorators |
| `request_id.py` | Per-request UUID middleware → log context |
| `logging.py` | Structured (JSON in prod) logging configuration |
| `time.py` | `utcnow` and tz helpers — call instead of `datetime.utcnow()` |

## Public surface

The frequently called entry points (everything else is invoked from a single site):

| Operation | Entry point |
|---|---|
| Wrap a successful response | `responses.py::ok` |
| Wrap an error response | `responses.py::err` |
| Resolve session → user | `deps.py::get_current_user` |
| Resolve cookie workspace + role | `deps.py::get_current_workspace`, `::require_role` |
| Hash / verify password | `auth.py::hash_password`, `::verify_password`, `::validate_password_strength` |
| Issue / revoke session | `auth.py::create_session_row`, `::revoke_session`, `::revoke_all_user_sessions` |
| Encrypt / decrypt secrets | `secrets.py::encrypt`, `::decrypt`, `::safe_decrypt` |
| Cursor pagination | `pagination.py::paginate`, `::encode_cursor`, `::decode_cursor` |

## Hard rules (this module)

1. **Envelope is mandatory.** Every route returns through `responses.ok` / `err`. See [ADR-0003](../../../docs/adr/0003-api-envelope-data-status.md).
2. **Single uvicorn worker** because slowapi's bucket store is in-process. See [ADR-0012](../../../docs/adr/0012-uvicorn-single-worker-slowapi.md).
3. **`secure` cookie is gated on `APP_ENV == "prod"`.** Local dev runs over HTTP. See [ADR-0011](../../../docs/adr/0011-secure-cookie-env-gated.md).

## See also

- [API conventions](../../../docs/api/README.md) — envelope, errors, pagination
- [ADR-0003](../../../docs/adr/0003-api-envelope-data-status.md), [ADR-0011](../../../docs/adr/0011-secure-cookie-env-gated.md), [ADR-0012](../../../docs/adr/0012-uvicorn-single-worker-slowapi.md)
- [Runbook — secret rotation](../../../docs/runbooks/) — TODO(verify) exact filename

## Don't

- Don't bypass `responses.ok` / `err` — the frontend's `lib/api.ts` assumes the envelope and will throw otherwise.
- Don't read `datetime.utcnow()` directly; call `time.py` so tests can freeze time.
- Don't call `_secrets_v0016.py` from application code — it exists only for the alembic 0016 data migration.
- Don't bump `--workers` past 1 without first switching slowapi to a Redis backend (ADR-0012).
