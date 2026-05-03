# Invitations API

Audience: engineer

Workspace invitation lifecycle: create / list / revoke (workspace-scoped, admin-gated) and the public accept endpoint that runs without a workspace cookie.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/invitations` (`backend/app/main.py:378`). The accept endpoint deliberately doesn't require workspace context — it only needs an authenticated user.

## Token format

Tokens delivered to invitees are composite strings: `"{invitation_id}:{plaintext}"` (`invitations.py:62-70`). The accept handler splits on the first `:` to obtain the row PK (no SQL timing oracle) and uses `hmac.compare_digest` against the stored HMAC (`invitations.py:271-301`). Only the SHA-256 hash and HMAC are persisted; the plaintext is returned exactly once at creation.

## Routes

### `POST /api/invitations`

Mint or reuse a pending invitation for an email.

**Request** — `InviteIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes | Lowercased before lookup and insert (`invitations.py:104`). |
| `role` | string | yes | `"viewer"`, `"member"`, `"admin"`, `"owner"` (validated by schema). |

**Response — new** — `201 Created`, body includes the composite token in `token` exactly once (`invitations.py:209`).

```json
{ "data": {
    "id": "…", "workspace_id": "…", "email": "alice@example.com", "role": "member",
    "status": "pending",
    "token": "<id>:<plaintext>",
    "created_at": "…", "accepted_at": null
}, "status": { … } }
```

**Response — existing pending row** — `200 OK`, `token: null` (the plaintext is irrecoverable; the operator must revoke and re-invite to obtain a fresh link) (`invitations.py:131-142`, `invitations.py:78-83`).

**Errors**

- `409 invitation.already_member` — a `WorkspaceMember` already exists for `lower(email)` (`invitations.py:110-124`).

**Notes**

- Gated `require_role("admin")` (`invitations.py:89`).
- A flush race against `uq_workspace_invitation_pending` is caught in a savepoint and converted into the "existing pending" response shape (`invitations.py:163-199`).
- Emits audit row `invitation.created` (`invitations.py:200-208`).
- Source: `backend/app/api/routes/invitations.py:86-209`.

### `GET /api/invitations`

List invitations for the current workspace.

**Query**

| Field | Type | Required | Notes |
|---|---|---|---|
| `limit` | int | no | Default `200`, max `1000` (`invitations.py:216`). |

**Response** — `200 OK` — array of invitation rows; `token` is always `null` here.

**Notes**

- Gated `require_role("admin")` (`invitations.py:212`).
- Sorted `created_at DESC` (`invitations.py:222`).
- Source: `backend/app/api/routes/invitations.py:212-226`.

### `DELETE /api/invitations/{invitation_id}`

Mark a pending invitation `revoked`.

**Errors**

- `404 invitation.not_found` — wrong workspace or unknown id (`invitations.py:231-237`).
- `400 invitation.not_pending` — already accepted, revoked, or expired; body includes `invitation_status` (`invitations.py:238-244`).

**Notes**

- Gated `require_role("admin")` (`invitations.py:229`).
- Emits audit row `invitation.revoked` (`invitations.py:246-254`).
- Source: `backend/app/api/routes/invitations.py:229-255`.

### `POST /api/invitations/accept`

Public accept (no workspace context). Requires `CurrentUser` only.

**Request** — `AcceptIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string | yes | Composite `"{invitation_id}:{plaintext}"`. |

**Response** — `200 OK`

```json
{ "data": { "workspace_id": "…", "workspace_name": "…", "role": "member" }, "status": { … } }
```

**Errors**

- `404 invitation.not_found` — malformed token, non-UUID id, missing row, or HMAC mismatch (`invitations.py:271-307`). HMAC is computed regardless of row presence to keep the timing constant.
- `400 invitation.not_pending` — invitation already accepted / revoked / expired; body includes `invitation_status` (`invitations.py:308-314`).
- `403 invitation.email_mismatch` — `inv.email != user.email` (case-insensitive) (`invitations.py:315-320`).

**Notes**

- Rate limit: `10/minute` per IP (`invitations.py:266`).
- Idempotent under concurrent accepts: a `uq_workspace_member` collision is caught in a savepoint, the existing member is upgraded to `status="active"` with the invited `role`, and the invitation is re-marked accepted (`invitations.py:353-388`).
- Existing inactive memberships are reactivated rather than re-created (`invitations.py:330-348`).
- Emits audit row `invitation.accepted` against the target workspace (`invitations.py:390-400`).
- Source: `backend/app/api/routes/invitations.py:265-401`.
