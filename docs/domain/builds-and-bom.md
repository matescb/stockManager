# Builds and BOM

Audience: engineer

A build runs against a project's bill-of-materials. This page covers the reservation lifecycle, the consume orchestration, shortage analysis, and sub-assembly output. The shape is "BOM lines (`project_entries`) ↔ `Build` ↔ ledger rows tagged with `build_id`".

For models see [`data-model.md`](data-model.md#projects) and [`data-model.md`](data-model.md#builds).

## Domain shape

```
Project ─< ProjectEntry  (the BOM)
   │
   └─< Build  (one or more passes against the BOM)
           │
           └── emits StockEntry rows (operation_type ∈ {reserve, release, build_consume, build_produce})
           │
           └── may emit one Lot (source_type='build') as the sub-assembly output
```

`ProjectEntry.entry_type` (`backend/app/domain/projects/models.py:69`):

| Value | Meaning |
|---|---|
| `part` | Standard BOM line. `part_id` set. Consumes from `part_id` or a registered substitute. |
| `meta_part` | Meta-part BOM line. `meta_part_id` set, `part_id` set to the meta. Consumes from any `part_meta_members` row. |
| `non_part` | Documentation-only line (e.g. "PCB fab"). Skipped during consume. |
| `unmatched` | BOM-import created the row but couldn't resolve to a part. Skipped during consume. |

`ProjectEntry.dnp` (`backend/app/domain/projects/models.py:78`) — "do not place". Skipped during reservation and consume.

BOM import match priority remains spec §16.3: internal ID, CAD key, internal part number, MPN, local name, then meta-part candidate. BOM-001 amends the failed-match outcome only: when `auto_create_missing_parts=true`, a row with MPN or part/name creates a zero-stock `Part` and becomes `entry_type='part'`; a row with neither MPN nor part/name is skipped. Default imports still create `entry_type='unmatched'` rows on failed match (`backend/app/domain/projects/bom_import.py::commit`).

## Required-quantity formula

`builds/service.py::_required(entry, part, build_qty)`:

```
base   = entry.quantity * build_qty
target = base * (1 + part.attrition_percentage / 100) * (1 + entry.attrition_pct / 100)
target = max(target, base + part.attrition_min_quantity)
required = ceil(target)          # Decimal, ROUND_CEILING
```

Two attrition sources **compound multiplicatively**:

- **Part-intrinsic** loss — `part.attrition_percentage` / `part.attrition_min_quantity` (spec §19.3). These describe physical loss rates (taping pickup error, soldering rejects) that are part-intrinsic, not project-specific.
- **Per-BOM-line** process scrap — `project_entries.attrition_pct` (Track B1, migration 0072). Mirrors PartsBox's "attrition": a `0 <= pct < 100` waste percentage on the specific BOM line. Both default to `0`, so neither disturbs the other when unset.

**Integer-only stock is the load-bearing rule here.** The ledger has no fractional rows (`stock_entries.quantity_delta` is Integer; `project_entries.quantity` is Integer, DB-005). The attrition-inflated requirement is therefore rounded **up** to an integer in `_required` — the single function shortage analysis, reservations, and consumption all read — so planning and actual consumption agree on the same number. Example: `100 base × 1 build × 2.5% = 102.5 → 103`, not `102`. `_required` uses `Decimal` so the ceiling is exact rather than a binary-float `102.4999…`.

## Service entry points

| Operation | Entry point | Notes |
|---|---|---|
| Substitute candidate set | `domain/builds/service.py::_candidate_part_ids` | Meta-parts → registered members; regular parts → registered substitutes (one-way main→sub or bidirectional). |
| Required quantity per BOM entry | `domain/builds/service.py::_required` | Folds in attrition. |
| Shortage analysis | `domain/builds/service.py::shortage_analysis` | Per-entry: required vs available, plus substitute availability. Read-only — used by the BOM check page. |
| List consumable BOM entries | `domain/builds/service.py::_consumable_entries` | Excludes DNP, `non_part`, `unmatched`, and rows with NULL `part_id`. |
| Apply reservations | `domain/builds/service.py::apply_reservations` | Writes one `reserve` row per consumable entry, sized by `_required`. No lot/storage binding. |
| Release reservations | `domain/builds/service.py::release_reservations` | Writes counter `release` rows for outstanding reserves. Idempotent. |
| Consume the build | `domain/builds/service.py::consume` | All-or-nothing. Bundles every lock up front. Optionally produces sub-assembly output lot. |

## Reservation lifecycle

`apply_reservations` (`backend/app/domain/builds/service.py:147-196`) writes one `StockEntry`:

- `status='reserved'`, `operation_type='reserve'`.
- `quantity_delta = _required(entry, part, build.quantity)`.
- `build_id`, `project_id` populated.
- **No** `lot_id` or `storage_location_id` — the consume step picks those.

`release_reservations` (`backend/app/domain/builds/service.py:199-260`) is idempotent: it finds every `reserve` row tied to the build that has no matching `release` (matched via `release.related_entry_id == reserve.id`) and writes counter rows with negated `quantity_delta`.

The sum `SUM(quantity_delta) WHERE status='reserved' AND part_id=…` is what `domain/stock/service.py::reserved_quantity` returns; subtracting it from `current_quantity` gives `available_quantity`.

Both flows take `lock_parts_for_stock_write` first — for `apply_reservations` the lock set is the consumable BOM's `part_id`s; for `release_reservations` it's the parts of the outstanding reserve rows. Lock IDs are sorted UUID-string to prevent AB/BA deadlocks (BE2-008).

## Consume orchestration

`builds.consume` (`backend/app/domain/builds/service.py:263-496`) is the heaviest service in the codebase. The order of operations is load-bearing:

1. **Refuse** if `build.status not in ('planned', 'in_progress')`.
2. **Lock everything up front** in deterministic UUID-string order. The lock set is `bom_part_ids ∪ line_part_ids ∪ output_part_ids` so every inner write — `release_reservations`, per-line consume, output-lot insert — re-acquires its lock as a re-entrant no-op (Postgres advisory locks are transaction-scoped). Without bundling, two concurrent consumes touching overlapping sets could acquire locks in different orders → AB/BA deadlock (`backend/app/domain/builds/service.py:286-301`).
3. **Release outstanding reservations** so the consumption itself isn't double-counted against `on_hand + reserved`.
4. **Aggregate demand per `(part_id, lot_id, storage_location_id)` tuple** before any per-line write. Without this, two BOM entries can each claim 60 of the same 100-piece reel and each pass `current_quantity >= line.quantity` independently — both lines write `-60` and the lot ends up at `-20` (BE CRIT-3, `backend/app/domain/builds/service.py:314-337`).
5. **Per-line consume**: validate that `line.part_id` is the entry's part, a registered substitute, or (for meta entries) a meta member. Validate caller-supplied lot/storage against the workspace before the availability check. Write a `build_consume` row with `quantity_delta = -line.quantity` (`backend/app/domain/builds/service.py:344-411`).
6. **Required-coverage check**: every non-DNP `part`/`meta_part` entry must be covered to at least its `_required(entry, part, build.quantity)` value. The aggregation key is `entry.id` so a single entry covered by multiple lines (different lots/storages) sums up (`backend/app/domain/builds/service.py:414-427`).
7. **Optional sub-assembly output**: if `project.associated_subassembly_part_id` is set, create a `Lot(source_type='build', source_build_id=build.id)` and write a `build_produce` row with `quantity_delta = build.quantity`. The output lot is set on `Build.output_lot_id` (`backend/app/domain/builds/service.py:429-477`). The destination storage is checked against `enforce_storage_constraints` first; a violation of `single_part_only` or `existing_parts_only` raises `StockConflictError` (→ `409 stock.conflict_error`) and rolls back the entire consume (PR #299, issue #280).
8. **Mark complete**: `build.status = 'complete'`, set `started_at` (if not set) and `completed_at` to `now`.

The whole sequence runs in the route's transaction. Any raise rolls back the entire build.

## Shortage analysis

`shortage_analysis` (`backend/app/domain/builds/service.py:77-125`) is the read-only counterpart to consume. Per BOM entry:

```
attrition_pct     = entry.attrition_pct                   # surfaced for the UI
required          = _required(entry, part, build_quantity) # attrition-adjusted, ceil-rounded
available         = current_quantity(part_id)             # part itself
substitute_ids    = _candidate_part_ids(part)
substitute_avail  = sum(current_quantity for each sub)
short_by          = max(0, required - (available + substitute_avail))
```

Substitute availability is summed across registered substitutes/members but is *informational* — the consume step still requires per-line opt-in to use a substitute.

## `Build` row

`Build` (`backend/app/domain/builds/models.py:18-33`):

| Column | Notes |
|---|---|
| `name` | Free-form. |
| `project_id` | FK, **CASCADE**. Deleting the project nukes its builds. |
| `quantity` | How many sub-assembly units this pass should produce. Drives `_required`. |
| `status` | `planned | in_progress | complete | cancelled`. Enforced only at the call site. |
| `started_at`, `completed_at` | Set by `consume` if not already. |
| `output_lot_id` | FK to `lots`, `SET NULL`. Set when consume produced a sub-assembly. |

## Things to never do

- **Never call `release_reservations` outside the consume flow without taking the same per-part lock first.** A bare release racing with another build's release on the same part can write duplicate counter rows.
- **Never validate per-line stock availability without the demand-aggregation pre-pass.** BE CRIT-3 covers exactly this — multiple BOM lines drawing from the same lot need the sum check, not per-line checks.
- **Never look up substitute candidates outside `_candidate_part_ids`.** It encodes the asymmetric `direction='one_way'` semantics on `part_substitutes` — bypassing it can let a one-way sub be used in the wrong direction.
- **Never set `output_lot_id` outside `consume`.** It's the only writer; tests assert that.
