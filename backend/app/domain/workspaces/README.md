# workspaces

Audience: engineer

Owns the tenant boundary: `Workspace`, member roles, invitations, and per-workspace catalog tokens (for the public read-only `/catalog` API).

## Files

| File | What |
|---|---|
| `models.py` | `Workspace`, `WorkspaceMember`, `WorkspaceInvitation`, `WorkspaceCatalogToken` |
| `schemas.py` | Pydantic shapes for workspace + member + invitation + catalog-token CRUD |

(No `service.py` — orchestration lives in routes and in `core/deps.py::require_role` / `get_current_workspace`.)

## Public surface

This module's surface is its models + schemas. The role gate lives in `core/deps.py::require_role`; the workspace cookie binding lives in `core/deps.py::get_current_workspace`.

## Hard rules (this module)

1. **Workspace isolation is code-enforced** on every domain query. New routes must filter by `workspace_id`. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).
2. **Catalog tokens grant read-only access to a single workspace** without a session cookie. Treat them as bearer credentials.
3. **Invitation acceptance is workspace-context-free** — `/api/invitations` runs without a `get_current_workspace` dep. See [API — invitations](../../../../docs/api/invitations.md).

## See also

- [Domain doc — workspace isolation](../../../../docs/domain/workspace-isolation.md) — the rule + the one DB-enforced exception
- [API — workspaces](../../../../docs/api/workspaces.md) — REST surface (workspace CRUD, members, invitations, catalog tokens)
- [API — invitations](../../../../docs/api/invitations.md) — accept flow
- [API — catalog](../../../../docs/api/catalog.md) — token-gated read-only catalog

## Don't

- Don't introduce row-level security or per-tenant schemas — the project deliberately enforces isolation in code (ADR-0002).
- Don't write a query that joins across workspaces "just to count" — every join must include `workspace_id` equality.
- Don't expose a catalog token in a URL the user might paste into chat — they're bearer credentials.
