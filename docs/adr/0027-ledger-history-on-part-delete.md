# ADR-0027: Preserve ledger history on part delete

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-14
- **Supersedes**: —
- **Superseded by**: —

## Context

The stock ledger is the audit trail for inventory movement. Before AUD-018,
`stock_entries.part_id` was `NOT NULL` with `ON DELETE CASCADE`, so a hard
delete of a part deleted every ledger row for that part. That violated the
append-only ledger decision by making historical stock movements disappear.

## Decision

`stock_entries.part_id` is nullable and its foreign key to `parts.id` uses
`ON DELETE SET NULL`. Deleting a part detaches the ledger rows from the deleted
part while preserving the stock history, quantities, actor, operation type, and
other ledger metadata.

New stock writes still require a live `part_id` through the stock service input
schemas, workspace validation, and the `stock_entries` workspace FK trigger.
The nullable column exists for preserved history after the parent part has been
deleted, not as an application-level "unknown part" write path.

## Consequences

- **Good**: Hard-deleting a part no longer destroys ledger history.
- **Trade-offs**: Historical rows can have `part_id = NULL`, so history
  serializers must return JSON `null` for those rows instead of assuming every
  stock entry still has a live part.
- **What it forbids**:
  - Do not restore `ON DELETE CASCADE` on `stock_entries.part_id`.
  - Do not add application code that inserts stock entries without a part.
  - Do not compute current stock from NULL-part rows; current stock remains a
    part-scoped read through `domain/stock/service.py`.

## Alternatives Considered

- **Keep `ON DELETE CASCADE` and rely on soft archive** — rejected because a
  hard-delete path still exists at the database boundary and can erase audit
  history.
- **Block hard deletes when ledger rows exist** — rejected for this issue
  because the accepted behavior is to preserve ledger rows with `part_id = NULL`.

## References

- Issue: `#557` / AUD-018
- Migration: `backend/alembic/versions/0056_stock_entries_part_set_null.py`
- Model: `backend/app/domain/stock/models.py`
- ADR: `docs/adr/0001-append-only-stock-ledger.md`
