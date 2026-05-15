# ADR-0028: Hard-delete policy and workspace trigger contract

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-15
- **Supersedes**: —
- **Superseded by**: —

## Context

Most user-facing deletion in StockManager is archive/restore, but hard deletes
still exist at the database boundary through admin scripts, tests, future data
retention work, and ordinary foreign-key actions. AUD-018 changed
`stock_entries.part_id` to `ON DELETE SET NULL` so part deletion preserves the
append-only stock ledger, but `lots.part_id` still cascaded and could delete
receiving/serial history.

Workspace FK triggers also re-validated every parent reference on any UPDATE
that mentioned `workspace_id` or a reference column. That was stricter than the
original AUD-052 goal, which was direct-SQL insert defense, and it made
operational UPDATEs depend on unrelated reference state.

## Decision

User-facing deletion of `parts`, `projects`, `orders`, and `builds` remains
archive-first. Hard deletes are reserved for explicit operational workflows and
must preserve independent history instead of erasing audit-relevant rows.

`parts` hard deletes detach historical `stock_entries` and `lots` by setting
their `part_id` to `NULL`. Feature-local part metadata such as substitutes,
meta-members, CAD keys, and provider/sourcing cache rows may cascade because
they are derived from the deleted part rather than inventory history.

`projects`, `orders`, and `builds` do not have public hard-delete endpoints.
If an operational hard delete removes one, direct child rows owned by that
aggregate may cascade, while cross-domain history keeps `ON DELETE SET NULL`
references where the row still has meaning without the parent.

Workspace FK triggers enforce parent workspace membership on INSERT. On UPDATE,
they validate only the reference column that changed. A workspace-only UPDATE
must not re-check unrelated references such as `lot_id`, `storage_location_id`,
`order_id`, or `build_id`.

## Consequences

- **Good**: Hard part deletes no longer erase stock ledger rows or lot history.
- **Good**: Direct SQL INSERTs still cannot create cross-workspace references.
- **Good**: Operational UPDATEs can touch narrow columns without being blocked
  by unrelated historical references.
- **Trade-offs**: A direct SQL UPDATE can still create inconsistent workspace
  state if it changes only `workspace_id`. Application code must continue to
  treat workspace movement as unsupported unless a dedicated migration handles
  every reference deliberately.
- **What it forbids**:
  - Do not restore `ON DELETE CASCADE` on `stock_entries.part_id` or
    `lots.part_id`.
  - Do not add a public hard-delete endpoint for parts, projects, orders, or
    builds without a new ADR that defines the retention behavior.
  - Do not make the workspace triggers re-validate unchanged reference columns
    on UPDATE.
  - Do not compute current inventory from preserved NULL-part history; current
    stock remains a part-scoped read through `domain/stock/service.py`.

## Alternatives considered

- **Block part hard deletes when lots or stock entries exist** — rejected
  because the accepted AUD-018 behavior is to preserve independent history with
  `part_id = NULL`.
- **Keep full UPDATE re-validation in workspace triggers** — rejected because
  it exceeds the direct-SQL insert defense requirement and can block safe,
  narrow operational updates.
- **Disable UPDATE trigger coverage entirely** — rejected because changing a
  reference column by direct SQL should still be validated against the row's
  workspace.

## References

- Issue: `#710` / AUD-072
- ADR: `docs/adr/0001-append-only-stock-ledger.md`
- ADR: `docs/adr/0027-ledger-history-on-part-delete.md`
- Migration: `backend/alembic/versions/0058_lots_part_set_null_trigger_update_gates.py`
- Model: `backend/app/domain/lots/models.py`
