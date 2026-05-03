# Workspaces API

Audience: engineer

Workspace CRUD, member management, catalog tokens, and the active-workspace cookie switch. Workspace-bound invitation creation lives here too; the public accept flow is in [invitations](./invitations.md).

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/workspaces` (`backend/app/main.py:366`). Most routes require `CurrentWorkspace`; mutating routes are gated by `require_role(...)`.

## Workspace CRUD

### `GET /api/workspaces`

List the active memberships for the current user.

**Response** — `200 OK`

```json
{ "data": [ { "id": "…", "name": "…", "kind": "personal", "currency_default": "USD" } ], "status": { … } }
```

**Notes**

- Filters `WorkspaceMember.status == "active"` (`workspaces.py:39-43`).
- Source: `backend/app/api/routes/workspaces.py:37-49`.

### `POST /api/workspaces`

Create an additional organisation workspace owned by the current user.

**Request** — `WorkspaceCreateIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | |
| `currency_default` | string | no | ISO-4217 code; passes through to the row. |

**Response** — `201 Created`

```json
{ "data": { "id": "…", "name": "…" }, "status": { … } }
```

**Errors**

- `409 workspace.owner_cap` — the caller already owns `_OWNED_ORG_WORKSPACE_CAP = 5` organisation workspaces (`workspaces.py:34`, `workspaces.py:75-82`). Body includes `existing_count`, `cap`.

**Notes**

- Rate limit: `10/hour` per IP (`workspaces.py:53`).
- The personal workspace minted at signup is `kind="personal"` and excluded from the cap (`workspaces.py:65-74`).
- Caller becomes `WorkspaceMember(role="owner", status="active")` (`workspaces.py:86`).
- Source: `backend/app/api/routes/workspaces.py:52-87`.

### `GET /api/workspaces/current`

Return the active workspace.

**Response** — `200 OK`

```json
{ "data": {
    "id": "…", "name": "…", "kind": "organization", "currency_default": "USD",
    "lot_control_enabled": true, "serial_tracking_enabled": false,
    "catalog_enabled": true, "catalog_token_set": true,
    "parts_provider": "mouser", "has_parts_provider_api_key": true, "has_parts_provider_api_secret": true,
    "scanner": "zxing", "has_scanner_license_key": false
}, "status": { … } }
```

**Notes**

- Plaintext catalog token is never echoed; only `catalog_token_set: bool` is returned (`workspaces.py:120-122`). See [SEC2-008].
- API keys are also never echoed (`workspaces.py:124-129`).
- Source: `backend/app/api/routes/workspaces.py:137-139`.

### `GET /api/workspaces/current/scanner-license-key`

Decrypt and return the Scandit license key for the scanner mount.

**Response** — `200 OK`

```json
{ "data": { "license_key": "…" }, "status": { … } }
```

**Notes**

- Gated `require_role("member")` because the SDK key is a paid third-party credential (`workspaces.py:142-145`).
- Decrypts at the boundary — column stores Fernet ciphertext post-migration `0016` (`workspaces.py:155-157`).
- Source: `backend/app/api/routes/workspaces.py:142-157`.

### `PATCH /api/workspaces/current`

Update workspace settings, rotate provider/scanner credentials, and (re)mint the default catalog token.

**Request** — `WorkspacePatch` (partial). Notable fields:

| Field | Type | Notes |
|---|---|---|
| `name`, `currency_default`, `lot_control_enabled`, `serial_tracking_enabled` | scalar | Direct attribute assignment (`workspaces.py:188-189`). |
| `catalog_enabled` | bool | Enabling for the first time mints a token (`workspaces.py:197-205`). |
| `regenerate_catalog_token` | bool | Pop-only flag; forces a new default token even if one exists (`workspaces.py:163`). |
| `parts_provider` | string | `"mouser"` / `"digikey"` / `"none"`. |
| `parts_provider_api_key` | string | Encrypted via `app.core.secrets.encrypt`; empty string clears (`workspaces.py:175-178`). |
| `parts_provider_api_secret` | string | Same encrypt-or-clear (`workspaces.py:179-182`). |
| `scanner` | string | `"zxing"` / `"scandit"`. |
| `scanner_license_key` | string | Encrypt-or-clear (`workspaces.py:183-186`). |

**Response** — `200 OK` — same shape as `GET /current`. When a token was minted in the same call, includes `catalog_token_plaintext` exactly once (`workspaces.py:131-133`).

**Notes**

- Gated `require_role("admin")` (`workspaces.py:160`).
- A new token revokes all prior `label IN ('default', 'default (legacy)')` rows in `workspace_catalog_tokens` and inserts a fresh `label="default"` row (`workspaces.py:213-232`). User-labelled tokens are untouched.
- Credential rotation emits an audit row `workspace.credentials_rotated` listing only field names (`workspaces.py:234-244`).
- Source: `backend/app/api/routes/workspaces.py:160-246`.

## Catalog tokens

These manage the `workspace_catalog_tokens` rows that gate `/catalog/*` access. See [catalog](./catalog.md) for the public side.

### `GET /api/workspaces/current/catalog/tokens`

List all (active and revoked) tokens.

**Response** — `200 OK`

```json
{ "data": [ { "id": "…", "label": "default", "created_at": "…", "last_used_at": "…", "revoked_at": null } ], "status": { … } }
```

**Notes**

- Gated `require_role("admin")` (`workspaces.py:272-275`).
- `token_hmac` is never serialised (`workspaces.py:254-269`).
- Source: `backend/app/api/routes/workspaces.py:272-288`.

### `POST /api/workspaces/current/catalog/tokens`

Mint a new token.

**Request** — `CatalogTokenIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `label` | string | yes | |

**Response** — `201 Created`. The plaintext is included as `token` exactly once.

```json
{ "data": { "id": "…", "label": "ci", "created_at": "…", "last_used_at": null, "revoked_at": null, "token": "<plaintext>" }, "status": { … } }
```

**Notes**

- Gated `require_role("admin")` (`workspaces.py:294`).
- Plaintext is `secrets.token_urlsafe(32)`; only `_hmac_token(plaintext)` is persisted (`workspaces.py:307-316`).
- Source: `backend/app/api/routes/workspaces.py:291-317`.

### `DELETE /api/workspaces/current/catalog/tokens/{token_id}`

Revoke a token (sets `revoked_at`).

**Errors**

- `404 resource.not_found` — token absent, cross-workspace (404, never 403), or already revoked (`workspaces.py:329-341`).

**Notes**

- Gated `require_role("admin")` (`workspaces.py:322`).
- Source: `backend/app/api/routes/workspaces.py:320-343`.

## Members

### `GET /api/workspaces/members`

List members of the current workspace, joined with their `users` row.

**Response** — `200 OK`

```json
{ "data": [ { "id": "…", "user_id": "…", "email": "…", "name": "…", "role": "owner", "status": "active" } ], "status": { … } }
```

**Notes**

- Source: `backend/app/api/routes/workspaces.py:346-368`.

### `PATCH /api/workspaces/members/{member_id}`

Change a member's `role` or `status`.

**Request** — `MemberPatch` (partial; `role`, `status`).

**Errors**

- `404 workspace.member_not_found` — wrong workspace or unknown id (`workspaces.py:386-391`).
- `403 workspace.owner_only` — non-owner tried to grant or revoke `owner` (`workspaces.py:400-405`).
- `400 workspace.last_owner` — would leave zero active owners (`workspaces.py:406-412`).

**Notes**

- Gated `require_role("admin")`; owner-related transitions additionally require the caller to be an owner (`workspaces.py:383`, `workspaces.py:392-405`).
- Source: `backend/app/api/routes/workspaces.py:383-415`.

### `DELETE /api/workspaces/members/{member_id}`

Hard-delete a member row.

**Errors**

- `404 workspace.member_not_found` (`workspaces.py:421-426`).
- `400 workspace.self_remove` — caller tried to remove themselves (`workspaces.py:427-432`).
- `400 workspace.last_owner` — would leave zero active owners (`workspaces.py:433-438`).

**Notes**

- Gated `require_role("admin")` (`workspaces.py:418`).
- Source: `backend/app/api/routes/workspaces.py:418-440`.

## Active-workspace cookie

### `POST /api/workspaces/{workspace_id}/switch`

Set the `stockmgr_workspace` cookie to a workspace the caller is an active member of.

**Response** — `200 OK`, body `{ "workspace_id": "…" }`. Cookie attributes: `httponly=True`, `secure` only in `prod`, `samesite=strict`, `max_age = 365 days`, `path=/` (`workspaces.py:490-498`).

**Errors**

- `404 workspace.not_found` — no active membership for the user in the target workspace (`workspaces.py:461-475`).

**Notes**

- Pre-fix this route accepted any string and was unauthenticated; see the in-source comment for the SEC2-004 history (`workspaces.py:450-460`).
- Source: `backend/app/api/routes/workspaces.py:443-499`.

## Invitations (workspace-scoped)

The create / list / revoke endpoints live on `/api/invitations` (the router is mounted there; see `backend/app/main.py:378`). They require workspace context; documented in [invitations](./invitations.md) alongside the public accept route.
