# tags

Audience: engineer

Owns tag definitions and the polymorphic link table. A `Tag` belongs to a workspace; a `TagLink` attaches a tag to any entity by `(entity_type, entity_id)` with no DB-level FK.

## Files

| File | What |
|---|---|
| `models.py` | `Tag`, `TagLink` |
| `schemas.py` | Pydantic shapes for tag CRUD + attach / detach |

(No `service.py` — CRUD lives in the route module; cleanup lives in `backend/app/domain/_polymorphic_cleanup.py`.)

## Public surface

This module's surface is its models + schemas. Lifecycle:

| Operation | Where |
|---|---|
| Tag CRUD | `backend/app/api/routes/tags.py` |
| Attach / detach to entity | same route |
| Cleanup on parent delete | `backend/app/domain/_polymorphic_cleanup.py` |

## Hard rules (this module)

1. **No FK from `TagLink` to its parent.** The `(entity_type, entity_id)` pair is application-managed. Parent deletes must call the cleanup helper.
2. **Workspace-scoped.** Both `Tag.workspace_id` and `TagLink.workspace_id` are required.
3. **Tag uniqueness is per-workspace** (TODO(verify): exact unique-index name). Two workspaces can have a tag with the same name.

## See also

- [Domain doc — polymorphic](../../../../docs/domain/polymorphic.md) — the no-FK surface (attachments / tags / custom_fields) and cleanup model
- [API — attachments / tags / custom-fields](../../../../docs/api/attachments-tags-cf.md) — REST surface

## Don't

- Don't add a real FK from `TagLink` to a parent entity — the polymorphic shape is deliberate.
- Don't bypass `_polymorphic_cleanup` on parent delete; orphan `TagLink` rows accumulate.
- Don't query tag links across workspaces.
