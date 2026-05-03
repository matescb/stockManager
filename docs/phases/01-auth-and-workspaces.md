# Phase 1 — Auth & workspaces

Audience: engineer

> Note: retro-documented 2026-05-03 from migration 0001; the original PR
> predates the phase-docs convention.

Lays the multi-tenant foundation: users, opaque-token sessions, and the
`workspace_id`-scoped data model that every later phase inherits.

## Why

- Single-tenant SaaS dies the first time two unrelated parties want to
  use it. The repo had to start multi-tenant or pay for a retrofit.
- Auth had to be password-based + cookie-based — there was no IDP and
  no plan to add one.
- Workspace ownership had to be expressible at the row level so
  every query could enforce it (see ARCHITECTURE — workspace isolation).

## What shipped

- `users` table (argon2 password hash, locale, timezone) —
  `backend/alembic/versions/0001_initial.py:21-32`.
- `user_sessions` (opaque token PK, `expires_at`, `ON DELETE CASCADE`
  to user) — `0001_initial.py:33-41`. Sessions are server-side; the
  cookie carries only the random token.
- `workspaces` (`name`, `kind`, `owner_user_id`, `currency_default`,
  `lot_control_enabled`, `serial_tracking_enabled`) —
  `0001_initial.py:42-53`. `kind` distinguishes personal vs team
  workspaces; `serial_tracking_enabled` and `lot_control_enabled` are
  switches that later phases consume (Phases 9 and onwards).
- `workspace_members` (`workspace_id`, `user_id`, `role`, `status`)
  with `uq_workspace_member` — `0001_initial.py:178-191`. `role` is a
  free-text string at this point; the four-role ladder is locked in by
  [Phase 10](10-rbac-invitations.md).
- The `WorkspaceOwned` mixin pattern: every domain table gets
  `id`, `workspace_id` (FK CASCADE), `created_at`, `updated_at`,
  `created_by`, `updated_by`, `archived_at`, plus the standard
  `ix_<table>_workspace_id` and `ix_<table>_archived_at` indexes.
  Visible across `attachments`, `parts`, `projects`, `lots`, etc.

## Invariants introduced

- **Workspace isolation is enforced in code, not the DB.** Every
  service filters by `ws.id`; every cross-table FK lookup checks
  `workspace_id` equality after resolving. There is no row-level
  security. See `CLAUDE.md` and the ADR on workspace isolation
  (`../adr/`). The lone DB-enforced exception is added later
  (migration 0036, trigger on `parts.default_storage_location_id`).
- **Soft-archive over hard-delete.** Domain rows carry `archived_at`
  rather than being deleted, so the ledger and audit trail survive.
- **Sessions are opaque tokens, not JWTs.** Revocation is a
  `DELETE FROM user_sessions WHERE token = ?`. The cookie is httpOnly
  and `Secure` in prod (gated on `APP_ENV`).
- **Workspace switch is per-request.** The active workspace is
  resolved from an `X-Workspace-Id` header / cookie at the dependency
  layer; switching does not require re-login.

## Things deferred

- RBAC beyond a free-text `role` column — `viewer/member/admin/owner`
  and `require_role()` arrive in [Phase 10](10-rbac-invitations.md).
- Token-based teammate invitations — also Phase 10
  (migration 0005, `workspace_invitations`).
- Login lockout / brute-force defence — landed later as
  `0028_login_lockout.py`.
- Session-token hashing at rest — `0017_session_token_hash.py`.
- Audit log — `0030_audit_log.py`.
- Encryption-at-rest for workspace secrets —
  `0016_encrypt_workspace_secrets.py`.

## References

- Migration: `backend/alembic/versions/0001_initial.py`
- Tables created here: `users`, `user_sessions`, `workspaces`,
  `workspace_members`.
- Architecture: `docs/ARCHITECTURE.md` — "Workspace isolation".
- Hard invariants: `CLAUDE.md` — "Workspace isolation is enforced in
  code, not the DB".
- TODO(verify): exact cookie name and lifetime defaults — confirm
  against `backend/app/api/routes/auth.py::_set_session_cookie`.
