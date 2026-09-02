# API tokens

Audience: engineer

Personal access tokens (PATs): the non-cookie credential that lets KiCad, the
PCM repository and agent/REST callers reach the API. A token acts **as** the
user who minted it, inside exactly one workspace.

## Conventions

See [API conventions](./README.md) for envelope, errors, auth. Mounted at
`/api/tokens` **without** `_member_gate` (`backend/app/main.py`) — a viewer may
mint a token, because the token inherits the viewer role and so can do nothing
its owner couldn't. Minting is rate-limited `10/hour` per workspace; revoking
`30/minute`.

Design rationale and the CSRF argument:
[ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md).

## Using a token

Send it as an `Authorization` header on any `/api` route. Both schemes are
accepted, case-insensitively — `Token` is what KiCad's HTTP library sends,
`Bearer` is what most agent tooling defaults to:

```
Authorization: Token smk_3f1c…b9.KJ3n…Qw
Authorization: Bearer smk_3f1c…b9.KJ3n…Qw
```

Three rules follow from `backend/app/core/deps.py::get_current_user`:

1. **Any non-empty `Authorization` header commits the request to the token
   path.** There is no fallback to the session cookie — a present-but-invalid
   header is a `401`, even from a browser with a valid session. This is what
   makes the CSRF exemption safe.
2. **The workspace is pinned** to the one the token was minted in. Sending
   `X-Workspace-Id` with a different value is a `403`
   (`auth.token_workspace_mismatch`); sending the same value is fine. The
   workspace cookie is ignored. The two routes that would otherwise span
   workspaces — `GET /api/auth/me` and `GET /api/workspaces` — return only the
   pinned one, so a token can never enumerate its owner's other tenants.
3. **The membership role still applies.** A viewer's token gets `403
   resource.insufficient_role` on writes even when `read_only` is `false`.
4. **Membership is re-checked on every request.** Losing the seat is a `401
   auth.invalid_token` on every route, and removing a member also revokes
   their tokens outright.
5. **Credential and tenancy administration is refused.** See *Session-only
   routes* below.

### Plaintext format

```
smk_{token_id_hex}.{secret}
```

`smk_` is a fixed prefix so leaked tokens are greppable. The middle is the
row's primary key, which is why resolution is a PK lookup plus a constant-time
HMAC compare rather than a scan over a secret-derived column (same reasoning as
the invitation token, SEC2-013). Only `HMAC-SHA256(secret, SESSION_SECRET)` is
stored; the plaintext appears in exactly one response and is never recoverable.

### `read_only`

A read-only token refuses every method outside `GET` / `HEAD` / `OPTIONS` with
`403 auth.token_read_only`, checked in `deps.py` before any route runs. It is
the credential to hand to the KiCad HTTP library and the PCM repository, where
the plaintext ends up in a config file or a URL path.

### Error codes

| Status | Code | When |
|---|---|---|
| 401 | `auth.invalid_token` | Malformed, unknown id, wrong secret, revoked, expired, or the owner is no longer a member. **One code for all of them** — anything finer is an oracle. |
| 403 | `auth.token_read_only` | Non-read method with a `read_only` token. |
| 403 | `auth.token_workspace_mismatch` | `X-Workspace-Id` disagrees with the pinned workspace. |
| 403 | `auth.token_no_token_management` | A token-authenticated request touched a session-only route (see below). |
| 403 | `resource.insufficient_role` | The owning membership's role is too low for the route. |

## Model

`ApiTokenOut` (`backend/app/domain/tokens/schemas.py`): `id`, `label` (≤120),
`read_only`, `created_at`, `expires_at`, `revoked_at`, `last_used_at`,
`user_email` (admin listing only). `token_hmac` has no field on any output
schema. `ApiTokenCreated` adds `token` — the one-time plaintext.

`last_used_at` / `last_used_ip` are best-effort telemetry: a failure to write
them is logged and swallowed, and the request proceeds. They are recorded
before the `read_only` check and committed independently, so a refused request
still leaves a trail; writes are throttled to one per 300s per token, so treat
the timestamp as "still in use?" rather than an access log. `last_used_ip` is
stored but deliberately not served.

## Routes

### `GET /api/tokens`

| Field | Type | Required | Notes |
|---|---|---|---|
| `all` | bool | no | Default `false`. `true` requires admin+ and returns every token in the workspace with `user_email` per row — the "revoke a departed teammate's tokens" path. `403 resource.insufficient_role` otherwise. |

Newest first. Never carries `token` or `token_hmac`.

### `POST /api/tokens`

| Field | Type | Required | Notes |
|---|---|---|---|
| `label` | string | yes | 1–120 chars. |
| `read_only` | bool | no | Default `false`. |
| `expires_in_days` | int \| null | no | 1–365, or `null` for no expiry. |

`201` with the row **plus `token`**. That response is the only place the
plaintext ever exists — there is no recovery endpoint and no admin backdoor.
Audit: `api_token.created` (comment `label=…,read_only=…`, never the secret).

### `POST /api/tokens/{id}/revoke`

Soft revoke; idempotent (`200` on an already-revoked token, keeping the original
`revoked_at`). Own token, or any workspace token with admin+. `404` for unknown
**and** cross-workspace ids; `403 resource.insufficient_role` for a
same-workspace token belonging to someone else. Audit: `api_token.revoked`.

There is no `PATCH`, no un-revoke, and no way to read a plaintext back.

## Session-only routes

These refuse a token-authenticated request with `403
auth.token_no_token_management`, whatever the method
(`core/deps.py::forbid_api_token`):

| Route | Why |
|---|---|
| all of `/api/tokens`, `GET` included | mint a successor, enumerate or revoke siblings |
| `POST /api/workspaces` | create a tenant |
| `POST /api/workspaces/{id}/switch` | move between tenants |
| `PATCH` / `DELETE /api/workspaces/members/{id}` | change a role, remove a seat |
| `POST` / `DELETE /api/workspaces/current/catalog/tokens[/{id}]` | mint a second credential that outlives this one |
| `POST` / `DELETE /api/invitations[/{id}]`, `POST /api/invitations/accept` | invite an accomplice, join a tenant |
| `PATCH /api/workspaces/current` | rotate the workspace's provider API keys |

The rule behind the table: a leaked token must not be able to widen itself,
mint a second credential that outlives its own revocation, invite an
accomplice, move its owner between tenants, or erase the `last_used_at` trail
that would expose the intrusion. All of those are human-at-a-browser actions,
so they need the session cookie.

`PATCH /api/workspaces/current` is on the list because it writes the encrypted
provider credentials, even though most of its payload is ordinary settings. An
agent that needs to change a non-credential setting will need a narrower
endpoint.

Separately, the CSRF exemption for `Authorization`-bearing requests is **not**
applied under `/api/auth/`: `logout` reads the session cookie directly, so the
no-cookie-fallback rule that justifies the exemption does not cover it. No
token client calls those routes.

## See also

- [ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md) — token design + CSRF exemption
- [ADR-0002](../adr/0002-code-enforced-workspace-isolation.md) — workspace isolation
- `backend/app/domain/tokens/README.md` — module orientation
