# Phase 10 — RBAC + workspace invitations

Audience: engineer

Brings real role-based access to workspace administration and lets a
workspace owner invite a teammate, who can accept after signing up.

## Roles

`WorkspaceMember.role` is now constrained to one of four values
(checked at the API boundary, not the DB):

| Role     | Can read workspace data | Can write data | Can manage members & settings |
|----------|------------------------|---------------|-------------------------------|
| viewer   | yes                    | no¹           | no                            |
| member   | yes                    | yes           | no                            |
| admin    | yes                    | yes           | yes (except owner role)       |
| owner    | yes                    | yes           | yes, including owner role     |

¹ As of Phase 10, viewer is identified at the role layer but the
existing data-mutating endpoints (parts, stock, projects, orders,
builds…) do not yet gate on it. That refit is intentionally left for
a follow-up — it requires sweeping every router and adding tests for
each. Treat `viewer` today as "in the workspace and can read" until
those gates land.

## `require_role` dependency

`backend/app/core/deps.py::require_role(min_role)` returns a FastAPI
dependency that 403s unless the current user's membership in the
current workspace ranks at or above `min_role` in the `viewer < member
< admin < owner` ladder. Apply it on a router-level
`dependencies=[Depends(require_role("admin"))]` (preferred) or per-
route to gate sensitive operations.

Currently used on:

- `PATCH /api/workspaces/current`
- `PATCH /api/workspaces/members/{id}`
- `DELETE /api/workspaces/members/{id}`
- `POST /api/invitations`, `GET /api/invitations`,
  `DELETE /api/invitations/{id}`

## Member management

```
GET    /api/workspaces/members
PATCH  /api/workspaces/members/{id}    { role?, status? }   admin+
DELETE /api/workspaces/members/{id}                         admin+
```

Guards:

- Only an owner can promote a non-owner to owner, or demote/remove an
  owner.
- The last active owner cannot be demoted or removed (you cannot
  strand the workspace ownerless).
- Cannot remove yourself — the UI suggests transferring ownership first.

## Invitations

```
POST   /api/invitations                 { email, role }    admin+   → returns token
GET    /api/invitations                                    admin+
DELETE /api/invitations/{id}                               admin+
POST   /api/invitations/accept          { token }          any auth user
```

`workspace_invitations` table (alembic 0005):

| Field | |
|---|---|
| `email` | the invited address |
| `role` | one of `admin / member / viewer` (cannot invite as `owner`) |
| `token` | URL-safe random; only returned to admins of the issuing workspace, while pending |
| `status` | `pending → accepted` or `pending → revoked` |
| `invited_by`, `accepted_by`, `accepted_at` | audit fields |

`accept`:
- 404 if token unknown
- 400 if not pending
- 403 if the authenticated user's email doesn't match `inv.email`
- otherwise creates (or activates) a `WorkspaceMember` with the
  invited role, marks the invitation accepted, returns the workspace.

Re-inviting the same email while a pending invite exists returns the
existing pending invite (idempotent) rather than creating a duplicate.

## UI

- **Settings → Workspace** now has Members and Invitations cards. The
  members table lets you change roles inline (drop-down per row) and
  remove members. The invitations card has an Email + Role + Invite
  form; pending invitations show the token (copy-paste to share).
- **Settings → Account** has a "Accept workspace invitation" form
  that takes a token, accepts it, switches workspace, and reloads.

## Tests

`backend/tests/test_invitations.py`:

- signup creator gets the `owner` role
- create-invite → invitee signs up → accepts → joins workspace
- non-admin's invite attempt is 403
- email-mismatch on accept → 403
- revoke blocks subsequent acceptance
- last-owner demotion → 400
- inviting an already-existing member → 409

40 backend tests pass total.
