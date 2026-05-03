# Auth API

Audience: engineer

Signup, email verification, login, logout, and the `me` lookup that the SPA shell calls on boot.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. All routes mount under `/api/auth` (`backend/app/main.py:365`).

## Prod boot invariants (signup mail)

Prod (`APP_ENV == "prod"`) cannot boot if any of `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `APP_BASE_URL` is empty or set to a dev default — the `_require_smtp_in_prod` validator raises at import time. The dev stdout mail backend additionally raises `RuntimeError` if invoked under `APP_ENV == "prod"` and the verification link is **never logged** by either backend. See [ADR-0018](../adr/0018-prod-smtp-fail-closed.md) and the regression test `backend/tests/test_mail_prod_safety.py`.

## Routes

### `POST /api/auth/signup`

Begin signup. Two modes selected by `SIGNUP_REQUIRE_EMAIL_VERIFICATION` (forced `True` in prod by the Settings validator).

**Request** — `SignupIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes | Pydantic `EmailStr`. |
| `password` | string | yes | Validated by `validate_password_strength` — `WeakPasswordError` becomes `400 auth.weak_password`. |
| `name` | string | yes | Display name. |
| `workspace_name` | string | no | Defaults to `"{name}'s workspace"`. |

**Response — verification mode** — `202 Accepted`

```json
{ "data": { "status": "verification_sent" }, "status": { "category": "ok", "message": "verification email sent" } }
```

**Response — immediate mode** — `200 OK`

```json
{ "data": { "user": { "id": "…", "email": "…", "name": "…" }, "workspace_id": "…" }, "status": { … } }
```

In immediate mode the session cookie (`SESSION_COOKIE_NAME`) is set on the response; see [ADR-0011](../adr/0011-secure-cookie-env-gated.md) for the `secure` flag rules.

**Errors**

- `400 auth.weak_password` — password fails `validate_password_strength` (`auth.py:96-100`).
- `409 auth.email_taken` — verified `User` row already exists for this email (`auth.py:104-110`).
- `503 mail.send_failed` — outbound verification mail raised (`auth.py:188-193`).

**Notes**

- Rate limit: `5/hour` per IP via slowapi (`auth.py:76`).
- Source: `backend/app/api/routes/auth.py:75-200`.
- Re-signup before TTL (24 h) returns `202` without creating a duplicate `PendingUser` row (`auth.py:154-168`).
- Verification token is a 32-byte URL-safe random string; only its HMAC (`SESSION_SECRET`-keyed SHA-256) is stored (`auth.py:56-65`, `auth.py:171-180`).

### `POST /api/auth/verify`

Consume a verification link → create `User` + `Workspace` + `WorkspaceMember(role="owner")` and mint a session.

**Request** — `VerifyIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string (UUID) | yes | `pending_users.id`. |
| `token` | string | yes | Plaintext token from email. |

**Response** — `200 OK`

```json
{ "data": { "user": { "id": "…", "email": "…", "name": "…" }, "workspace_id": "…" }, "status": { "category": "ok", "message": "email verified" } }
```

Sets the session cookie.

**Errors**

- `400 auth.verification_invalid` — id is not a UUID, no row matches, HMAC mismatch, or token already consumed (`auth.py:215-247`). HMAC is compared in constant time against a dummy digest when the row is missing to remove the timing oracle (`auth.py:225-230`).
- `400 auth.verification_expired` — `pending.created_at < now - 24h` (`auth.py:249-255`).
- `409 auth.email_taken` — race-condition guard if a `User` was created since the pending row (`auth.py:258-263`).

**Notes**

- Rate limit: `10/minute` per IP (`auth.py:209`).
- Source: `backend/app/api/routes/auth.py:208-303`.

### `POST /api/auth/login`

Verify credentials → revoke any existing sessions for the user → issue a new one.

**Request** — `LoginIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes | |
| `password` | string | yes | |

**Response** — `200 OK`

```json
{ "data": { "user": { "id": "…", "email": "…", "name": "…" } }, "status": { … } }
```

**Errors**

- `429 auth.account_locked` — per-account lockout (`check_login_lockout`); response includes `retry_after_seconds = 900` (`auth.py:323-330`, `LOCKOUT_WINDOW_SECONDS = 15*60` at `auth.py:370`).
- `401 auth.invalid_credentials` — unknown email or wrong password. Failure row is committed before raising so the lockout counter advances even though the dep would otherwise roll back (`auth.py:336-356`).

**Notes**

- Rate limit: `10/minute` per IP (`auth.py:314`).
- Login rotates sessions: `revoke_all_user_sessions` then `create_session_row` (`auth.py:359-364`).
- Source: `backend/app/api/routes/auth.py:313-366`.

### `POST /api/auth/logout`

Delete the session row for the cookie's token and clear the cookie.

**Request** — none.

**Response** — `200 OK`, `data: null`.

**Notes**

- No-op if the cookie is absent (`auth.py:376-379`).
- Source: `backend/app/api/routes/auth.py:373-381`.

### `GET /api/auth/me`

Return the authenticated user and the list of active workspace memberships.

**Response** — `200 OK`

```json
{ "data": { "user": { "id": "…", "email": "…", "name": "…" }, "workspaces": [ { "id": "…", "name": "…", "kind": "personal" } ] }, "status": { … } }
```

**Errors** — `401` if no session cookie (handled by `CurrentUser` dep).

**Notes**

- Filters memberships by `status == "active"` (`auth.py:387-390`).
- Source: `backend/app/api/routes/auth.py:384-401`.

## TODOs

- TODO(verify): the `password change` endpoint mentioned in the README index isn't present in `auth.py`. Confirm whether it's planned, lives elsewhere, or the README index is wrong.
