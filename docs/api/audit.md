# Audit API

Audience: engineer

Read-only audit log query (BE2-024). Cursor-paginated. Admin-only.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/audit` (`backend/app/main.py:364`). Note: the include sets `dependencies=_member_gate`, but the route ALSO declares `Depends(require_role("admin"))` (`audit.py:26`) — admin is the effective gate so members and viewers cannot enumerate past admin actions.

## Routes

### `GET /api/audit`

Return the most recent audit rows for the current workspace, in reverse-chronological order.

**Query**

| Field | Type | Required | Notes |
|---|---|---|---|
| `limit` | int | no | Default `50`, `1 <= limit <= 200`. |
| `before_id` | UUID | no | Cursor — id of the oldest row already shown. The handler looks up the pivot, then filters `(created_at < pivot.created_at) OR (created_at == pivot.created_at AND id < pivot.id)` so the page is stable when timestamps tie (`audit.py:52-64`). An unknown / cross-workspace `before_id` is silently ignored (`audit.py:54-57`). |

**Response** — `200 OK` — array of `AuditLogOut.model_dump(mode="json")` rows. TODO(verify): exhaustive `AuditLogOut` field list (`backend/app/domain/audit/schemas.py`).

**Notes**

- Workspace isolation is enforced by `workspace_id == ws.id` regardless of the cursor (`audit.py:48-50`).
- Source: `backend/app/api/routes/audit.py:26-69`.

## TODOs

- TODO(verify): `AuditLogOut` field shape (`backend/app/domain/audit/schemas.py`).
