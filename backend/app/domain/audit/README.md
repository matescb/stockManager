# audit

Audience: engineer

Owns the activity log: a single append-only `AuditLog` table that records "who did what to which entity, when".

## Files

| File | What |
|---|---|
| `models.py` | `AuditLog` |
| `schemas.py` | Pydantic shapes for the audit query API |
| `service.py` | `log` — the single write entry point |

## Public surface

| Operation | Entry point |
|---|---|
| Record an event | `service.py::log` |

Reads are direct SQL from the audit route (`backend/app/api/routes/audit.py`).

## Auth Event Comments

Password-reset request throttling records the rejected cause on
`user.password_reset_requested`:

- `throttled:rate` means the per-email hourly reset cap was already reached.
- `throttled:concurrent` means the request lost the advisory-lock race to an
  in-flight request for the same email hash.

## Hard rules (this module)

1. **One write entry point.** All audit writes go through `service.py::log` so the row shape stays uniform.
2. **Append-only.** Audit rows are never updated or deleted by application code. Retention is a DB-side concern (TODO(verify): retention policy).
3. **Workspace-scoped by default.** Workspace events must set `AuditLog.workspace_id`; the query API filters by the caller's workspace.
4. **Auth/system exception.** Auth/system events with no workspace context may store `workspace_id = NULL`; those rows are not returned by tenant-scoped audit queries.

## See also

- [API — audit](../../../../docs/api/audit.md) — query surface
- [Domain doc — data model](../../../../docs/domain/data-model.md) — audit table position in the ER diagram

## Don't

- Don't insert `AuditLog` rows directly from routes — go through `service.py::log`.
- Don't use the audit log as a source of truth for business state. It's a record of changes, not a substitute for the entity tables.
- Don't strip workspace_id from workspace-scoped rows "because the action is global". Use `workspace_id = NULL` only for auth/system events with no workspace context.
