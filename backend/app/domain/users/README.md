# users

Audience: engineer

Owns the user identity (`User`), session rows (`UserSession`), login-failure lockout (`UserLoginFailure`), and the email-verification staging table (`PendingUser`).

## Files

| File | What |
|---|---|
| `models.py` | `User`, `UserSession`, `UserLoginFailure`, `PendingUser` |
| `schemas.py` | Pydantic shapes for signup / login / me / password change |
| `service.py` | `assert_user_deletable` (FK-safety check before delete) |

Most session / password / lockout logic lives in `core/auth.py`, not here — this module owns the data shapes.

## Public surface

| Operation | Entry point |
|---|---|
| Pre-delete safety check | `service.py::assert_user_deletable` |
| Issue / hash / revoke session | `core/auth.py::create_session_row`, `::hash_session_token`, `::revoke_session` |
| Password hash + verify | `core/auth.py::hash_password`, `::verify_password`, `::validate_password_strength` |
| Login lockout | `core/auth.py::record_login_failure`, `::check_login_lockout`, `::clear_login_failures` |

## Hard rules (this module)

1. **Sessions are rows, not JWTs.** The cookie value is hashed before lookup; revoke = delete the row.
2. **Password strength is checked on signup + change** via `core/auth.py::validate_password_strength` (HIBP k-anonymity probe + complexity).
3. **`PendingUser` is the pre-verification staging table** — the real `User` row is created on verification, not on signup.

## See also

- [API — auth](../../../../docs/api/auth.md) — signup / login / logout / me / password change
- [Runbook — session purge](../../../../docs/runbooks/) — TODO(verify) if a runbook exists

## Don't

- Don't store or compare raw session tokens — always hash via `core/auth.py::hash_session_token`.
- Don't bypass `assert_user_deletable` when wiring a delete-user path; the FK fan-out is non-obvious.
- Don't gate `secure` cookie on anything other than `APP_ENV == "prod"` — local dev runs over HTTP. See [ADR-0011](../../../../docs/adr/0011-secure-cookie-env-gated.md).
