# attachments

Audience: engineer

Owns the polymorphic `Attachment` table — files (image / datasheet / arbitrary blob) that hang off any entity by `(entity_type, entity_id)` with no DB-level FK.

## Files

| File | What |
|---|---|
| `models.py` | `Attachment` |

(No `schemas.py` / `service.py` here — request/response shapes live in `backend/app/api/routes/attachments.py`; cleanup lives in `backend/app/domain/_polymorphic_cleanup.py`.)

## Public surface

This module's surface is its model. CRUD lives in the route. Orphan cleanup (when the parent entity is deleted) lives in `backend/app/domain/_polymorphic_cleanup.py` — that helper is called from each delete path that owns a polymorphic-target entity.

## Hard rules (this module)

1. **No FK from `Attachment` to its parent.** The `(entity_type, entity_id)` pair is application-managed. Parent deletes must call the cleanup helper.
2. **Workspace-scoped.** `Attachment.workspace_id` is required; every read/write filters by workspace. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).
3. **File contents live on disk under `UPLOAD_DIR`** (content-addressed for provider assets — see `domain/parts/services/assets.py`). The DB row is metadata only.

## See also

- [Domain doc — polymorphic](../../../../docs/domain/polymorphic.md) — the no-FK surface (attachments / tags / custom_fields) and how cleanup works
- [API — attachments / tags / custom-fields](../../../../docs/api/attachments-tags-cf.md) — combined REST page

## Don't

- Don't add a real FK from `Attachment` to a parent entity — the polymorphic shape is deliberate.
- Don't bypass `_polymorphic_cleanup` on parent delete; orphans accumulate quickly.
- Don't store file contents in the DB; use the disk-backed path under `UPLOAD_DIR`.
