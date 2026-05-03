# ADR-0001: Append-only stock ledger

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

A parts-inventory system has to answer two questions cheaply: "what's the current quantity of part X?" and "how did we get there?". The naive design is an `inventory.qty` integer column that every receive / consume / adjust mutates in place. That makes "current" trivial and "how did we get there" impossible without a separate audit log — which then has to be kept in sync with the live column under concurrent writers.

The two-source design rots: the audit log and the live column drift, no foreign key prevents it, and reconciling after the fact requires re-deriving history from whichever side is less wrong.

## Decision

Stock is a single append-only ledger: `stock_entries`, with a signed `quantity_delta` per row. There is no `inventory.qty` column. Current quantity is the sum of `quantity_delta` over all rows for a `(workspace_id, part_id, status)` tuple, computed by `domain/stock/service.py::current_quantity`. Bulk variants exist on the same module for list views.

A Postgres trigger (migration `0013_stock_nonneg_trigger.py`) prevents the running sum from going negative, and a transactional advisory lock (`_lock_for_stock_write`) serialises writes per `(workspace_id, part_id)` so a concurrent receive + consume can't race past the availability check.

## Consequences

- **Good**: Audit history is the source of truth — there's no separate log to drift. Every adjustment carries reason, actor, and lot binding. Time-travel queries ("stock as of date T") fall out of `WHERE created_at <= T`.
- **Trade-offs**: A read of "current quantity" is a `SUM(...)` rather than a column read. The bulk roll-up exists because per-row reads in list views were too slow. Any code that wants "current" must call the service, not write its own SUM.
- **What it forbids**:
  - Don't add an `inventory.qty` column or any other materialised current-quantity store.
  - Don't compute current stock by joining or aggregating outside `domain/stock/service.py::current_quantity` / `bulk_current_quantity`.
  - Don't `UPDATE` an existing `stock_entries` row to "correct" history. Append a compensating row.
  - Don't bypass `_lock_for_stock_write` when writing — concurrent writers must serialise on the part.

## Alternatives considered

- **Mutable `inventory.qty` column with a separate audit log** — rejected because the two stores can drift, and reconciliation after a bug requires re-deriving the canonical value from whichever side is less corrupted. The ledger collapses both into one source.
- **Event-sourcing with snapshots** — rejected as over-engineered for this scale. The bulk-roll-up query (`backend/app/domain/stock/service.py:270`) keeps list-view latency acceptable without a snapshot layer; if it ever doesn't, snapshots can be added on top of the ledger without changing the API.

## References

- Source: `backend/app/domain/stock/service.py:140` (`current_quantity`)
- Source: `backend/app/domain/stock/service.py:50-103` (advisory locks)
- Source: `backend/alembic/versions/0013_stock_nonneg_trigger.py`
- Rule: `CLAUDE.md:87-91`
- Architecture: `docs/ARCHITECTURE.md` — ledger model
