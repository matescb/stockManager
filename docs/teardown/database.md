# Database & Migrations Teardown

Scope: Alembic chain, schema, FKs/indexes/triggers, ledger semantics.
Date: 2026-05-01.

## Migration chain status

Head revision: `0016`. Total: 16 files (`0001`..`0016`), single linear
chain (`0001` has `down_revision=None`; every other revision pins the
prior NNNN). No branches, no orphans. Filenames match the
`NNNN_short_name.py` convention. Every migration is the product of a
single commit per `git log --follow` — there are **no post-merge edits**
to any migration file in the chain. There is **one stub/no-op
migration**: `0015_stub_after_revert.py` was deliberately preserved
with empty `upgrade()`/`downgrade()` after the original 0015 (encrypt-
workspace-secrets) was reverted on prod (see PR #23/#24); the work
re-landed as `0016`. The file must remain in-tree so `alembic upgrade
head` doesn't error on prod replicas whose `alembic_version` got stuck
at `0015` during the failed deploy.

Static review only — no `alembic upgrade` was run; no DB was contacted.
A delegated per-file run of the `alembic-migration-reviewer` subagent
was not performed (see Coverage gaps); findings below come from direct
file inspection.

## Database Issues

### DB-001: `stock_entries.order_id` / `order_entry_id` / `build_id` and `lots.source_order_id` / `source_build_id` are not foreign keys

Severity: **High**

Evidence:

- `backend/app/domain/stock/models.py:55` — `order_id = Column(UUID(...), nullable=True)` (no `ForeignKey`).
- `backend/app/domain/stock/models.py:56` — `order_entry_id = Column(UUID(...), nullable=True)`.
- `backend/app/domain/stock/models.py:58` — `build_id = Column(UUID(...), nullable=True)`.
- `backend/app/domain/lots/models.py:25-26` — `source_order_id`, `source_build_id` likewise plain UUIDs.
- `backend/alembic/versions/0001_initial.py:361-364` and `:259-260` — the stock_entries / lots tables are created with these columns but no `ForeignKeyConstraint` rows.
- Other FKs on the same tables (`part_id`, `lot_id`, `storage_location_id`, `project_id`, `workspace_id`) are declared as real FKs.

Impact:

The ledger and lot rows hold "soft" references to orders, order entries
and builds with no referential integrity. Deleting an order, order
entry, or build leaves dangling stock-entry rows pointing at
non-existent IDs; an audit query joining `stock_entries → orders` will
silently lose history. A CASCADE delete of an order today does *not*
clean up the stock entries that referenced it (the ledger is append-
only, so that's arguably the right policy, but it's currently enforced
by absence of FK rather than by `ON DELETE SET NULL`). Cross-workspace
references can also be persisted unnoticed because there's no FK to
even reach `_belongs(...)` semantics from raw DB inserts.

Fix instruction:

Decide the intended deletion policy per column (almost certainly `ON
DELETE SET NULL` for `stock_entries.order_id` / `order_entry_id` /
`build_id` and `lots.source_order_id` / `source_build_id`, mirroring
the existing `lot_id` / `storage_location_id` SET NULL pattern), then
emit a new migration that adds the four/five `ForeignKeyConstraint`s.
Verify-then-migrate: pre-flight a query that finds rows whose `*_id`
points at a missing parent and reconcile (NULL them out) before adding
the constraint. Mirror the columns in the model files. Consider also
adding indexes on `stock_entries(workspace_id, build_id)` and
`stock_entries(workspace_id, order_id)` — the consume-build and
receive-order audit queries currently can't use one.

### DB-002: Trigger `check_stock_nonneg` has a race against the advisory lock when callers omit `lot_id` / `storage_location_id`

Severity: **High**

Evidence:

- `backend/alembic/versions/0013_stock_nonneg_trigger.py:55-56` — trigger groups by `IS NOT DISTINCT FROM NEW.lot_id` and `IS NOT DISTINCT FROM NEW.storage_location_id`, i.e. it treats NULL as a distinct bucket, not a wildcard.
- `backend/app/domain/stock/service.py:269-275` (`remove_stock`) — the service's `current_quantity()` lookup uses `.where(... == lot_id)` only when `lot_id is not None`; passing `lot_id=None` aggregates *all* lot buckets for the part.
- Same mismatch in `move_stock` (`service.py:330-338`) and `adjust_stock` (`service.py:438-444`).
- `service.py:37-58` — advisory lock keyed on `(workspace_id, part_id)`.

Impact:

Service-layer validation says "you have N globally; you can remove N",
the per-tuple trigger then fires on the negative row at a single
`(part_id, lot_id=NULL, storage_location_id=NULL)` bucket whose actual
sum is zero, raising `check_violation` and bubbling a 500 to the API.
The advisory lock prevents two concurrent operators racing each other
but cannot rescue a single request whose validation key differs from
the trigger's grouping key. This is the BE-002 / BE-004 issue but
described from the database side: the trigger's grouping semantics are
*correct* (every NULL bucket is independent in an append-only ledger);
it's the service whose validation read is too loose.

Fix instruction:

Align validation and trigger semantics. The cleanest fix is to require
explicit `lot_id` and `storage_location_id` on `remove`, `move_out`,
`adjust`, and `build_consume` whenever the part has any rows in a
non-NULL bucket — or implement deterministic FIFO/LIFO allocation that
expands one logical "remove N globally" into one negative row per
existing positive bucket. Either way, validation must run with the
exact same `(part_id, lot_id, storage_location_id, status)` tuple the
trigger groups by. Add an integration test that a request with NULL
coordinates against stock that lives in a specific lot fails at the
service layer with a 400, not at the trigger with a 500.

### DB-003: Soft-delete name collision — `uq_storage_ws_name`, `uq_tag_ws_name`, `uq_workspace_member` are full unique constraints, not `WHERE archived_at IS NULL` partials

Severity: **High**

Evidence:

- `backend/alembic/versions/0001_initial.py:155` — `sa.UniqueConstraint('workspace_id', 'name', name='uq_storage_ws_name')` (no `postgresql_where`).
- `backend/alembic/versions/0001_initial.py:174` — `sa.UniqueConstraint('workspace_id', 'name', name='uq_tag_ws_name')`.
- `backend/alembic/versions/0001_initial.py:188` — `sa.UniqueConstraint('workspace_id', 'user_id', name='uq_workspace_member')` (no `archived_at` here, but the same shape — there is no soft-delete on this table, only `status='disabled'`).
- Contrast: `parts.uq_parts_ws_mpn` uses `postgresql_where="mpn IS NOT NULL AND archived_at IS NULL"` (`alembic/versions/0011_parts_mpn_unique.py:34`).

Impact:

Archive a storage location named "Bin A", then try to create a new
storage location named "Bin A" — the create fails with the unique
constraint, even though the intent of `archived_at` is to free the
name for re-use. Same for `tags`. The MPN partial-unique pattern
(`0011`) is the obvious template that wasn't applied to the older
constraints. (CLAUDE.md hard invariants describe the MPN partial as
the canonical pattern; the earlier tables were defined before that
pattern was adopted.)

Fix instruction:

Add a new migration that drops `uq_storage_ws_name` and re-adds it as
a partial unique index `WHERE archived_at IS NULL`; same for
`uq_tag_ws_name`. Update the model `__table_args__` to use
`Index(..., unique=True, postgresql_where=...)` rather than
`UniqueConstraint`. Pre-flight a query for active duplicates and
reconcile before adding the partial. (`uq_workspace_member` is fine —
its dedup is by `status` not by `archived_at`.)

### DB-004: `workspace_id` only indexed alone or paired with one other column — no covering index for the universal `(workspace_id, archived_at, ...)` filter on every list endpoint

Severity: **Medium**

Evidence:

- Every workspace-scoped table has `ix_<table>_workspace_id` (single column) plus tables that have `archived_at` get `ix_<table>_archived_at` (single column) and `ix_<table>_ws_archived` `(workspace_id, archived_at)`.
- That's the right shape for "list active rows in a workspace" reads, but for sort-by-name / sort-by-updated-at, the planner has nothing better than one of those plus a sort. E.g. `ix_parts_ws_archived` on `(workspace_id, archived_at)` is great for the existence check but not for the typical UI query "active parts in workspace, ordered by updated_at desc".
- `lots`, `stock_entries`, `attachments`, `tag_links`, `custom_fields`, `bom_import_presets`, `project_entries` — none have `(workspace_id, archived_at)` composite. Only `parts`, `projects`, `storage_locations`, `orders`, `builds`, `lots` (yes, `lots` does) have the composite.

Impact:

List queries on `attachments` / `tag_links` / `custom_fields` /
`bom_import_presets` / `project_entries` will mostly land on
`ix_<table>_workspace_id` and post-filter `archived_at IS NULL` on the
rows. Fine at small scale, expensive once the table has tens of
thousands of mostly-archived rows.

Fix instruction:

Audit which workspace-scoped tables routinely filter by `archived_at`
and add `(workspace_id, archived_at)` partial indexes (`WHERE
archived_at IS NULL`) where the active-row filter is the hot path.
For `stock_entries`, also consider `(workspace_id, part_id, status,
lot_id, storage_location_id)` to make the trigger's lookup index-only
— right now the trigger does a SUM that has to filter on five
columns and only `ix_stock_ws_part_status` covers three of them.

### DB-005: `Numeric` precision/type mismatch — `stock_entries.quantity_delta` is `Integer` but `project_entries.quantity` is `Numeric(18,6)`

Severity: **Medium**

Evidence:

- `backend/app/domain/stock/models.py:49` — `quantity_delta = Column(Integer, nullable=False)`.
- `backend/app/domain/projects/models.py:54` — `quantity = Column(Numeric(18, 6), nullable=False, default=1)`.
- `backend/app/domain/orders/models.py:47-48` — `quantity_ordered`, `quantity_received` both `Integer`.
- `current_quantity()` returns `int` (`stock/service.py:80`).

Impact:

A BOM line for "0.5 m of cable" or "1.25 kg of resin" can be created
on a `Project`, but no representation of fractional consumption exists
in the ledger. Build consume coerces fractional BOM quantity × build
quantity to int, silently losing precision (or raising if it doesn't
round). For an electronics-parts shop this is mostly fine (everything
is whole pieces), but the BOM model's `Numeric(18,6)` is misleading
because the ledger can't represent it.

Fix instruction:

Either (a) widen `stock_entries.quantity_delta` and the order-entry
quantities to `Numeric(18,6)` end-to-end and update the trigger's SUM
math (no schema work for the trigger; `SUM` of `numeric` is fine), or
(b) constrain BOM `quantity` to integer with a `CHECK` and update the
schemas. Option (a) is the structural fix; option (b) is the
"electronics workspace" simplification. Pick one and document it.

### DB-006: `attachments`, `custom_fields`, `tag_links` — polymorphic `object_id` has no FK and no ON DELETE behaviour

Severity: **Medium**

Evidence:

- `backend/app/domain/attachments/models.py:16-17` — `object_type: String(40)`, `object_id: UUID`, no FK.
- Same shape in `custom_fields/models.py:17-18` and `tag_links/models.py:28-29`.
- `0001_initial.py` creates these tables with no per-target FK constraint (the polymorphic discriminator makes a single FK impossible without per-object-type partial constraints).

Impact:

Deleting a `Part`, `Order`, `Project`, etc. orphans rows in
`attachments` / `custom_fields` / `tag_links` whose `object_id` pointed
at it. There's no cleanup hook in any cascade chain; orphans
accumulate forever. `tag_links` also breaks the workspace_id =
parent.workspace_id invariant if a parent ever moves workspace (no
such code exists today, but no constraint enforces it either).

Fix instruction:

Add explicit cleanup in the `archive_part` / `delete_part` / equivalent
service paths (`DELETE FROM attachments WHERE workspace_id=:ws AND
object_type='part' AND object_id=:id`, etc.). Or — better — replace
polymorphism with a per-target join table (one `part_attachments`,
one `order_attachments`, …) so a real FK + ON DELETE CASCADE applies.
Until then, add an `ix_attachments_ws_objid` etc. partial index to
make orphan-cleanup queries fast, and a periodic cleanup job that
reports orphans.

### DB-007: `user_sessions` has no index on `expires_at` and the table grows unbounded

Severity: **Medium**

Evidence:

- `backend/alembic/versions/0001_initial.py:33-41` — only indexes `ix_user_sessions_user_id`.
- `backend/app/domain/users/models.py:30-35` — `expires_at` column, no index, no TTL.

Impact:

Any "delete expired sessions" sweep is a full table scan and grows
linearly with logins. There is also no documented cleanup path —
sessions only get deleted on explicit logout. A long-running prod
instance will accumulate every expired session forever.

Fix instruction:

Add a migration that creates `ix_user_sessions_expires_at`. Add a
periodic cleanup task (cron / startup hook) that does `DELETE FROM
user_sessions WHERE expires_at < now()`. Cross-ref Sec issue SEC-006:
sessions are stored as plaintext tokens, which is independently a
problem. If you fix that and re-issue, the cleanup window for the old
plaintext token rows wants this index.

### DB-008: Bag-signature index does not have a partial `WHERE bag_signature IS NOT NULL` predicate

Severity: **Medium**

Evidence:

- `backend/alembic/versions/0012_stock_entries_bag_signature.py:28-32` — `op.create_index("ix_stock_ws_bag_signature", "stock_entries", ["workspace_id", "bag_signature"])` with no predicate.
- The vast majority of `stock_entries` rows have `bag_signature IS NULL` (only scan-import rows set it).

Impact:

The index is bloated with a row per stock entry instead of just the
scan-imported ones (a small fraction). Insert cost on every ledger
write pays for an index that's only read by the scan-recognition
flow. Storage waste at scale, slower writes for the 99% case.

Fix instruction:

Drop and recreate as a partial: `CREATE INDEX
ix_stock_ws_bag_signature ON stock_entries(workspace_id,
bag_signature) WHERE bag_signature IS NOT NULL;`. Update
`stock/models.py:39` to mirror the predicate via
`postgresql_where=text("bag_signature IS NOT NULL")`. Do this
concurrently in prod (`CREATE INDEX CONCURRENTLY` + drop old) — the
ledger is the hottest write table and a non-concurrent reindex blocks
inserts.

### DB-009: `0001_initial.py` revision-ID metadata mismatch — file says `revision = '0001'` but `Revision ID:` docstring is `2a3353f8b5fe`

Severity: **Low**

Evidence:

- `backend/alembic/versions/0001_initial.py:3` — `Revision ID: 2a3353f8b5fe` in docstring.
- `backend/alembic/versions/0001_initial.py:13` — `revision = '0001'`.
- Same shape in `0005_workspace_invitations.py:5` (docstring says `24ac5d07a692`, code says `0005`).

Impact:

Cosmetic — Alembic only reads the assigned `revision` constant, not
the docstring header — but `git grep 2a3353f8b5fe` returns the file
and tools / future authors might assume the docstring is canonical.
On a downgrade or `alembic show 2a3353f8b5fe`, the lookup fails.

Fix instruction:

A throwaway pass to rewrite each file's docstring header to match the
real revision ID. (Don't modify executable code in the migrations —
`Don't edit a migration file once it's been merged to main` — but the
docstring is comment-only and safe. If even that's too risky, add a
new migration that does nothing but document the canonical ID
mapping in CHANGELOG.md instead.)

### DB-010: `0016` backfill imports application code at migration time, breaking the "migrations should be self-contained" invariant

Severity: **Medium**

Evidence:

- `backend/alembic/versions/0016_encrypt_workspace_secrets.py:85` — `from app.core.secrets import encrypt, safe_decrypt` inside `upgrade()`.
- The same migration's idempotency story (`safe_decrypt(value)` round-trip) depends on whatever `app.core.secrets` looks like at the time the migration runs.

Impact:

If the future codebase deletes or renames `app.core.secrets.encrypt` /
`safe_decrypt`, replaying this migration on a fresh DB (e.g. CI
provisioning a new test DB from scratch) breaks. Migrations are
supposed to be replayable against the historical schema; reaching into
live application code couples the migration to whichever app-code
revision is checked out at upgrade time. CLAUDE.md's pre-edit hook
explicitly forbids editing migrations after merge — but the migration
is already pre-coupled to a moving target.

Fix instruction:

Inline the Fernet logic the migration needs (or vendor it as a
private `_encrypt_at_migration_0016` helper inside the migration
file) so the migration upgrades cleanly even if `app.core.secrets`
later evolves. If you really want to share the code, freeze the
shared helper at the import site by referencing a version-pinned
internal module (e.g. `app.core._secrets_v1`) that is never edited.
Same caveat for any future migration that reaches into the app
package.

### DB-011: `0016` schema change uses `op.alter_column(type_=String(1024), existing_type=String(255))` without `postgresql_using` — implicit cast is fine for `String→String` but the migration doesn't say so

Severity: **Low**

Evidence:

- `0016_encrypt_workspace_secrets.py:64-81` — three `op.alter_column` calls widening `String(255)/String(2048)` to `String(1024)/String(4096)`.

Impact:

Postgres handles `varchar(255) → varchar(1024)` without a rewrite (it's
a metadata-only catalog change), so this is fine for *this* migration.
The risk is that the same shape on a future "shrink" migration would
hard-fail without a `USING` clause and a possible truncation. The
file pattern doesn't document the assumption.

Fix instruction:

Add a one-line comment beside each `alter_column` recording "widening
varchar is metadata-only on Postgres — no `USING` needed". On any
future shrink, require `existing_server_default=None`,
`postgresql_using="left(col, N)"`, and a regression test.

### DB-012: `parts ↔ projects` cyclic FK uses `use_alter` correctly but the *only* test of this codepath is `pytest tests/conftest.py` recreating the schema

Severity: **Low**

Evidence:

- `0001_initial.py:128` — `Project.associated_subassembly_part_id` ForeignKey with `use_alter=True`, `name='fk_projects_associated_subassembly_part'`.
- `0001_initial.py:382-388` — explicit `op.create_foreign_key(...)` after both tables exist.
- `0001_initial.py:394` — `downgrade()` first drops the cycle FK before any table.
- ARCHITECTURE.md:160-162 documents the pattern.

Impact:

Pattern is correct. Only flag is that `downgrade()` will fail mid-way
if any *other* migration ever adds another `use_alter` cycle and is
itself downgraded out-of-order. Today there's only the one cycle, so
this is theoretical.

Fix instruction:

Document in `docs/development.md` that any future cyclic FK must use
`use_alter` and must explicitly drop the alter-FK at the *top* of its
own `downgrade()`. No code change needed today.

### DB-013: `workspaces.owner_user_id` uses `ondelete='RESTRICT'`, but every other user-facing FK uses `SET NULL` — deleting a user is impossible if they own a workspace

Severity: **Low**

Evidence:

- `backend/app/domain/workspaces/models.py:22` — `ForeignKey("users.id", ondelete="RESTRICT")`.
- All `created_by`, `updated_by`, `created_by`, etc. columns use `ondelete='SET NULL'`.
- `WorkspaceMember.user_id` uses `ondelete='CASCADE'` (`workspaces/models.py:50`).

Impact:

`RESTRICT` is intentional — a workspace cannot survive losing its
owner without ownership reassignment. But the asymmetry (members
cascade-deleted, owner is restrict, audit trail SET NULL) means a
"delete a user" operation has to: (1) reassign ownership of every
workspace they own, (2) remove all memberships, (3) NULL all `*_by`.
There's no service-layer "delete user" endpoint today, so this is
latent — the next time someone implements one, they'll hit the
RESTRICT and need the reassignment workflow.

Fix instruction:

When the user-deletion endpoint lands, document the workflow:
reassign-or-archive every owned workspace before the user row goes.
Add a service-layer test that proves a delete fails cleanly (with
`409 owns workspaces`) instead of bubbling a Postgres error to a 500.
No migration change needed.

### DB-014: `workspace_invitations.email` indexed but no composite `(workspace_id, email, status)` for the canonical "pending invitation for this email in this workspace?" query

Severity: **Low**

Evidence:

- `0005_workspace_invitations.py:38` — `op.create_index('ix_workspace_invitations_email', ...)` (single col).
- `0005_workspace_invitations.py:39` — `op.create_index('ix_workspace_invitations_workspace_id', ...)` (single col).
- The hot lookup pattern — "is there a pending invitation for `email` in `workspace_id`?" — needs both, and `status='pending'` to filter dupes.

Impact:

Mild — invitation tables are tiny in practice. The lookup is correct;
the planner just merges the two single-column indexes. Becomes a
problem only at thousands-of-pending-invites scale, which is unlikely
in this product.

Fix instruction:

Optional. Add a partial composite `(workspace_id, lower(email))
WHERE status='pending'`. Skip if invite volume stays low.

### DB-015: `default_storage_location_id` cross-workspace lookup is not constrained at the DB level

Severity: **Medium**

Evidence:

- `backend/app/domain/parts/models.py:56-58` — `default_storage_location_id` FK to `storage_locations.id` with no workspace scoping.
- A part in workspace A could (via raw SQL or a buggy service path) point at a `storage_locations.id` whose `workspace_id` is workspace B.

Impact:

The service-layer enforces this with `_belongs(...)` checks (e.g.
`stock/service.py:159`, `:163`), but the DB does not. A buggy import,
a future endpoint that forgets the check, or a manual `UPDATE` will
persist a cross-workspace reference. Same shape as BE-005 (Order
entries can reference parts from another workspace) but on parts ↔
storage rather than orders ↔ parts. Workspace isolation is by design
"enforced in code, not the DB" (per CLAUDE.md), but at minimum a CHECK
constraint matching `workspace_id = (SELECT workspace_id FROM
storage_locations WHERE id = default_storage_location_id)` would
turn a future bug into a 23514 instead of a silent leak. Postgres
doesn't allow that as a CHECK, but it can be a deferred trigger or
a generated column joined into the unique constraint.

Fix instruction:

Lowest-effort: add an integration test that asserts a cross-workspace
default_storage_location_id is rejected. Higher-effort: add a row-
level trigger on `parts` (and `lots.part_id`, `project_entries.part_id`,
`order_entries.part_id`, `builds.project_id`, etc.) that enforces
`NEW.workspace_id = (SELECT workspace_id FROM <ref> WHERE id =
NEW.<ref>_id)`. The trigger is the same shape as `check_stock_nonneg`
but applied to every cross-workspace FK.

## Coverage gaps

- The `alembic-migration-reviewer` subagent was not invoked per file —
  it was not available in this session. Findings above are from
  manual file review. The riskiest 6–8 (per the plan: `0001`, `0011`,
  `0012`, `0013`, `0014`, `0015`, `0016`) were read end-to-end.
  Filenames `0002`..`0010` are smaller per-feature deltas and were
  read but not subjected to a deeper lock-analysis review.
- No live database was inspected — physical index sizes, bloat,
  dead-tuple ratio, and actual planner choices on the prod schema
  are unknown. Index-presence findings (DB-004, DB-008) are about
  *predicate fit*, not measured query performance.
- The trigger function `check_stock_nonneg` was reviewed for
  semantics; no test was run against a live Postgres to confirm the
  `IS NOT DISTINCT FROM` NULL-bucket semantics behave as documented.
  An integration test pinning that exact shape (`NULL bucket and
  named bucket are independent`) is missing from the test suite.
- Polymorphic-target reachability for `attachments` / `custom_fields`
  / `tag_links` was inspected for FK absence (DB-006); the actual
  list of consumers (services that delete a parent without cleaning
  up these tables) was not enumerated — that's a backend-teardown
  responsibility.
