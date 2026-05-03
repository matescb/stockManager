# Runbook: workspace recovery

Audience: engineer / on-call

Restore a workspace that was disabled (intentionally or by mistake),
audit a suspected workspace-isolation leak, or backfill a member who
lost access. Workspace isolation is enforced in **code**, not the DB
(CLAUDE.md "Workspace isolation is enforced in code, not the DB"), so
both the symptom set and the audit method are different from a
database-row-security model.

- **When to run**:
  - Workspace owner reports their workspace is "gone" / "I can't log
    in" → likely `status = 'disabled'`.
  - A user reports seeing data that doesn't belong to their workspace
    (suspected isolation leak — SEV-1, treat as security incident).
  - A workspace member was removed and needs to be re-added.
  - A workspace owner left the org and access needs to be transferred.
- **Severity**:
  - Re-enable a disabled workspace: routine / SEV-3.
  - Member backfill: routine.
  - **Suspected isolation leak: SEV-1 + security incident**
    (`incident-response.md` + escalation immediately).
- **Time-to-recovery target**: 15 min for re-enable; isolation audit is
  open-ended.
- **Owner**: `<TODO(verify): on-call rotation>`. For isolation leaks,
  immediately also notify `<TODO(verify): security owner>`.

## Background — what "disabled" means

The `workspace_members` row carries a status field:
`backend/app/domain/workspaces/models.py:65` —
`status = Column(String(20), nullable=False, default="active")  #
invited | active | disabled`. Setting a member to `disabled` blocks that
user's access to the workspace; it does **not** delete data.

Workspaces themselves are not soft-deleted in the same way — what users
call "disabling a workspace" is typically disabling all members. Confirm
the actual mechanism in code before touching DB:
`<TODO(verify): is there a workspaces.status / archived_at column? as
of writing, only workspace_members.status is documented in models.py>`.

The status update endpoint is gated by admin role —
`backend/app/api/routes/invitations.py:229` is the comparable pattern
for membership writes.

## Pre-flight

- SSH access to the VPS as `deploy`.
- For isolation-leak audits: the user's report (which workspace, which
  page, what data they saw, ideally a screenshot).
- For member backfill: the inviter / admin's user_id and the email of
  the member to restore.

## A. Re-enable a disabled workspace member

1. SSH in.
   ```bash
   ssh deploy@<vps-host>
   cd /srv/stockmanager
   ```
2. Confirm the member is actually disabled (don't guess):
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec db psql -U stockmgr stockmgr
   ```
   ```sql
   SELECT wm.id, wm.workspace_id, wm.user_id, wm.status, w.name, u.email
     FROM workspace_members wm
     JOIN workspaces w ON w.id = wm.workspace_id
     JOIN users u      ON u.id = wm.user_id
    WHERE u.email = '<user-email>';
   ```
3. If `status = 'disabled'`, take a backup before touching it:
   ```bash
   /srv/backup/bin/run-backup.sh stockmanager
   ```
4. Re-enable through the API (preferred — leaves an `audit_log` entry):
   ```bash
   curl -X PATCH https://parts.matescb.cz/api/workspaces/members/<member-id> \
       -H 'Content-Type: application/json' \
       -H 'Cookie: <admin-session>' \
       -H 'X-Workspace-Id: <ws-id>' \
       -d '{"status":"active"}'
   ```
   `<TODO(verify): exact endpoint path / payload for member status update
   — check backend/app/api/routes/workspaces.py>`
5. If no admin is available to call the API, direct DB update is the
   fallback (record the change in the incident channel):
   ```sql
   UPDATE workspace_members
      SET status = 'active'
    WHERE id = '<member-id>';
   ```
6. Confirm:
   ```sql
   SELECT id, status FROM workspace_members WHERE id = '<member-id>';
   ```

## B. Backfill a removed member

The member row was hard-deleted (revoked invitation + member removed).
Re-add via the standard invitation flow:

1. Workspace admin opens the Members UI and sends a fresh invitation
   to the member's email.
2. The flow at `backend/app/api/routes/invitations.py:91`
   (`create_invitation`) creates a new `workspace_invitations` row.
3. Member accepts at `POST /api/invitations/accept`
   (`backend/app/api/routes/invitations.py:267`), which creates a new
   `workspace_members` row with `status = 'active'`.

Don't re-create the row by direct SQL — the audit trail
(`backend/app/domain/audit/`) won't capture it and the role assignment
goes through validation in the route.

## C. Audit a suspected isolation leak — SEV-1

Treat any user report of "I saw another workspace's data" as a security
incident until proven otherwise. **Do not dismiss without an audit.**

### C.1 Capture before doing anything

1. Get the user's report in writing (chat / email): which page, what
   they saw, when. Screenshot if possible.
2. Note the user's email and current `workspace_id` (from the user's
   browser DevTools → cookies → `X-Workspace-Id` value, or from the
   DB).
3. Declare a SEV-1 (`incident-response.md` step 1). Treat as
   "potential data exposure".

### C.2 Reproduce or rule out

1. Have the reporter log in again with DevTools open and the Network
   tab recording.
2. Capture the URL and the `X-Workspace-Id` header on the request that
   showed the wrong data.
3. Pull the same URL with a clean session (your own admin user, your
   own workspace) and compare.

If the response now shows your data (not the reporter's, not the third
party's), the issue may have been a stale cache on the reporter's
browser — but **don't** stop there. Continue with C.3.

### C.3 Audit the route

1. Identify the route from step C.2.
2. Open the corresponding handler in `backend/app/api/routes/`.
3. Confirm the handler:
   - Resolves the current workspace via the `CurrentWorkspace`
     dependency (or equivalent).
   - Filters the primary query by `workspace_id == ws.id`.
   - For any cross-table FK lookup, checks `workspace_id` equality on
     the joined row.
4. Check for the standard isolation test pattern:
   ```bash
   grep -n "<route-path>" backend/tests/test_workspace_isolation.py
   ```
   If there's no test for the route, that's the first remediation
   item.

CLAUDE.md "Workspace isolation is enforced in code, not the DB" is the
canonical statement — there is no row-level security, every route is
responsible. The exception is `parts.default_storage_location_id`,
which is additionally enforced by a Postgres BEFORE trigger
(`parts_default_storage_workspace_check`, migration 0036).

### C.4 If a leak is confirmed

1. Patch the missing `workspace_id` filter. Add a test in
   `tests/test_workspace_isolation.py` that fails without the patch.
2. Audit the audit log for the affected period:
   ```sql
   SELECT actor_user_id, action, target_type, target_id, ts
     FROM audit_log
    WHERE ts > '<window-start>'
      AND (actor_user_id = '<reporter-user-id>'
           OR target_id::text IN (
               SELECT id::text FROM <leaking-table> WHERE workspace_id = '<exposed-ws-id>'
           ))
    ORDER BY ts DESC
    LIMIT 200;
   ```
   `<TODO(verify): exact column names for audit_log — check
   backend/app/domain/audit/models.py>`
3. Determine the scope — how many rows from how many workspaces were
   exposed to whom for how long.
4. Notify the affected workspace owners. Be specific about what data
   was exposed and to whom.
5. Hotfix deploy with the patch. Standard pipeline applies.
6. Schedule a post-mortem within 1 week (`incident-response.md`).

### C.5 If the leak is **not** confirmed

The reporter saw what looked like wrong data but the audit shows the
route filters correctly. Possible causes:

- Stale client cache (e.g. switched workspaces in another tab; UI
  hadn't refetched).
- Reporter was unknowingly logged in to the workspace they "didn't
  own" — happens with shared computers.
- The reporter saw a value that **resembled** another workspace's data
  but was their own.

Document the no-leak finding in the incident channel and resolve. Keep
the audit notes — false positives compound into evidence over time.

## Verification

- Section A: the user can log in and see their workspace.
- Section B: the new member appears in the Members UI with role
  `<role>` and status `active`.
- Section C confirmed leak: the reproduction case from C.2 no longer
  returns the wrong data; an isolation test covers the route; the
  affected owners have been notified.
- Section C false positive: the reporter agrees the scenario was a
  client-side artifact.

## Rollback

- Section A re-enable was wrong (user shouldn't have been re-enabled):
  flip back to `disabled` via the same API (or SQL fallback).
- Section B invitation was sent to the wrong email: revoke at
  `DELETE /api/invitations/<id>` (`backend/app/api/routes/invitations.py:229`).
- Section C hotfix made things worse: standard `prod-rollback.md`
  procedure. Migration changes for an isolation fix are rare but
  possible — see `migration-recovery.md`.

## Post-mortem prompts

- For isolation leaks: was there an existing test covering the route?
  If yes, why didn't it catch this? If no, why was the route shipped
  without one?
- Did the audit log have enough detail to scope the leak quickly?
- For disable/re-enable: do we have a UI affordance for this, or did
  the operator have to SSH? If SSH is the only path, that's an
  affordance gap.
- For member backfill: did the member's old data (e.g. assignments,
  audit-log authorship) get reattached, or are they orphaned?
