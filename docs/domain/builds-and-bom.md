# Builds and BOM

Audience: engineer

A build runs against a project's bill-of-materials. This page covers the reservation lifecycle, the consume orchestration, shortage analysis, multi-stage builds, and sub-assembly output. The shape is "BOM lines (`project_entries`) ↔ `Build` ↔ ledger rows tagged with `build_id`".

For models see [`data-model.md`](data-model.md#projects) and [`data-model.md`](data-model.md#builds).

## Domain shape

```
Project ─< ProjectEntry  (the BOM)
   │
   └─< Build  (one or more passes against the BOM)
           │
           ├─< BuildStage ─< BuildStageLine → ProjectEntry   (optional; Track B2)
           │
           └── emits StockEntry rows (operation_type ∈ {reserve, release, build_consume, build_produce})
           │
           └── may emit one Lot (source_type='build') as the sub-assembly output
```

A build with **no stages** is a single-pass build and behaves exactly as it did before multi-stage builds existed. Stages are purely additive — see [Multi-stage builds](#multi-stage-builds).

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
| Required quantity per BOM entry | `domain/builds/service.py::_required` | Folds in attrition. The **only** quantity authority — staged requirements are slices of its output, never re-derived from `project_entries.quantity`. |
| Shortage analysis | `domain/builds/service.py::shortage_analysis` | Per-entry: required vs available, plus substitute availability. Read-only — used by the BOM check page. |
| List consumable BOM entries | `domain/builds/service.py::_consumable_entries` | Excludes DNP, `non_part`, `unmatched`, and rows with NULL `part_id`. |
| Apply reservations | `domain/builds/service.py::apply_reservations` | Writes one `reserve` row per consumable entry, sized by `_required`. No lot/storage binding. |
| Release reservations | `domain/builds/service.py::release_reservations` | Writes counter `release` rows for the quantity still outstanding on each reserve row. Idempotent. |
| Release part of a reservation | `domain/builds/service.py::release_reservation_amounts` | Releases `{part_id: qty}` across the build's outstanding reserve rows. Per-stage consume only. |
| Validate + write consume lines | `domain/builds/service.py::apply_consume_lines` | Demand-aggregation pre-pass, substitute/lot/storage validation, coverage check. Shared by whole-build and per-stage consume. |
| Consume the build | `domain/builds/service.py::consume` | Single-pass. All-or-nothing. Bundles every lock up front. Optionally produces sub-assembly output lot. |
| Stage requirement allocation | `domain/builds/stages.py::stage_allocations` | `{stage_id: {entry_id: qty}}` — slices `_required` by portion, cumulatively. |
| Consume one stage | `domain/builds/stages.py::consume_stage` | All-or-nothing. Completes the build when the last stage lands. |

## Reservation lifecycle

`apply_reservations` (`backend/app/domain/builds/service.py:174`) writes one `StockEntry`:

- `status='reserved'`, `operation_type='reserve'`.
- `quantity_delta = _required(entry, part, build.quantity)`.
- `build_id`, `project_id` populated.
- **No** `lot_id` or `storage_location_id` — the consume step picks those.

`release_reservations` (`backend/app/domain/builds/service.py:311`) is idempotent: for every `reserve` row tied to the build it writes a counter row for the quantity still outstanding.

**"Outstanding" is measured in quantity, not row existence** (`_outstanding_reservations`, `backend/app/domain/builds/service.py:226`). A reserve row of 100 that has already been countered by 40 is outstanding for 60. Before multi-stage builds every release was all-or-nothing, so existence and quantity agreed and the old "skip any reserve row that has a `release` pointing at it" rule was equivalent. Per-stage consume releases only its own slice, so the accounting has to be quantity-based — the existence rule would let the next release write a *full* counter on a partly-released row and drive `reserved_quantity` negative.

The sum `SUM(quantity_delta) WHERE status='reserved' AND part_id=…` is what `domain/stock/service.py::reserved_quantity` returns; subtracting it from `current_quantity` gives `available_quantity`.

Both flows take `lock_parts_for_stock_write` first — for `apply_reservations` the lock set is the consumable BOM's `part_id`s; for `release_reservations` it's the parts of the outstanding reserve rows. Lock IDs are sorted UUID-string to prevent AB/BA deadlocks (BE2-008).

## Consume orchestration

`builds.consume` (`backend/app/domain/builds/service.py:708`) is the heaviest flow in the codebase. Steps 2 and 4–6 live in helpers that per-stage consume reuses verbatim, so both paths share one implementation. The order of operations is load-bearing:

1. **Refuse** if `build.status not in ('planned', 'in_progress')`. The route additionally refuses this endpoint outright when the build has stages.
2. **Lock everything up front** in deterministic UUID-string order (`lock_for_consume`, `backend/app/domain/builds/service.py:412`). The lock set is `bom_part_ids ∪ line_part_ids ∪ output_part_ids` so every inner write — the release pass, per-line consume, output-lot insert — re-acquires its lock as a re-entrant no-op (Postgres advisory locks are transaction-scoped). Without bundling, two concurrent consumes touching overlapping sets could acquire locks in different orders → AB/BA deadlock. Per-stage consume locks the **whole BOM**, not just its own slice, so a stage consume and a whole-build consume of the same project can never take the two lock sets in opposite orders.
3. **Release outstanding reservations** so the consumption itself isn't double-counted against `on_hand + reserved`.
4. **Aggregate demand per `(part_id, lot_id, storage_location_id)` tuple** before any per-line write. Without this, two BOM entries can each claim 60 of the same 100-piece reel and each pass `current_quantity >= line.quantity` independently — both lines write `-60` and the lot ends up at `-20` (BE CRIT-3, `apply_consume_lines`, `backend/app/domain/builds/service.py:462`).
5. **Per-line consume**: validate that `line.part_id` is the entry's part, a registered substitute, or (for meta entries) a meta member. Validate caller-supplied lot/storage against the workspace before the availability check. Write a `build_consume` row with `quantity_delta = -line.quantity`.
6. **Required-coverage check**: every entry the pass is responsible for must be covered to at least its required quantity. The aggregation key is `entry.id` so a single entry covered by multiple lines (different lots/storages) sums up. `required_by_entry` is what distinguishes the two paths: whole-build consume passes `_required(entry, part, build.quantity)` for every consumable entry, per-stage consume passes that same number sliced by portion.
7. **Optional sub-assembly output** (`produce_output`, `backend/app/domain/builds/service.py:614`): if `project.associated_subassembly_part_id` is set, create a `Lot(source_type='build', source_build_id=build.id)` and write a `build_produce` row with `quantity_delta = build.quantity`. The output lot is set on `Build.output_lot_id`. The destination storage is checked against `enforce_storage_constraints` first; a violation of `single_part_only` or `existing_parts_only` raises `StockConflictError` (→ `409 stock.conflict_error`) and rolls back the entire consume (PR #299, issue #280).
8. **Mark complete** (`complete_build`, `backend/app/domain/builds/service.py:688`): `build.status = 'complete'`, set `started_at` (if not set) and `completed_at` to `now`.

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

## Multi-stage builds

Track B2, migration 0076. A build may be assembled across several stages instead of one all-at-once consume, mirroring PartsBox's multi-stage builds: each stage consumes a defined subset (and portion) of the BOM, so a partially-built device is tracked accurately and stock is drawn down progressively.

### Schema shape

Two tables hanging off the `Build` aggregate (`backend/app/domain/builds/models.py:39`, `:83`):

| Table | Columns beyond the `WorkspaceOwned` mixin |
|---|---|
| `build_stages` | `build_id` (FK, CASCADE), `name`, `sequence`, `status` (`planned \| in_progress \| complete`), `started_at`, `completed_at`, `comments`. Unique `(build_id, sequence)` among active rows. |
| `build_stage_lines` | `build_stage_id` (FK, CASCADE), `project_entry_id` (FK, CASCADE), `portion_pct` `NUMERIC(7,4)` with `CHECK (portion_pct > 0 AND portion_pct <= 100)`. Unique `(build_stage_id, project_entry_id)`. |

Why stages belong to the **build**, not the project: two builds of the same project may be staged differently (a prototype run vs. a production run), and a stage's status describes *this* physical pass. Putting stages on the project would make status a shared mutable property of the BOM.

Why `portion_pct` and not an absolute quantity: an absolute per-stage quantity is a second copy of the BOM number that silently drifts when `project_entries.quantity`, `parts.attrition_percentage`, `project_entries.attrition_pct` or `builds.quantity` changes. A percentage keeps `_required` the single quantity authority.

`stock_entries.build_stage_id` (nullable, `ON DELETE SET NULL`, partial index `ix_stock_ws_build_stage`) tags the ledger rows a per-stage consume writes so the trail shows what each stage took. It is NULL for every row a single-pass build emits. `SET NULL` mirrors `build_id` — a hard-deleted build cascades its stages away but must not delete independent stock history ([ADR-0028](../adr/0028-hard-delete-policy-and-workspace-trigger-contract.md)).

Three defence-in-depth workspace triggers ship with 0076 — `build_stages_workspace_fk_check`, `build_stage_lines_workspace_fk_check`, `stock_entries_build_stage_workspace_check` — following the 0064 contract: validate every parent ref on INSERT, only changed refs on UPDATE, raise SQLSTATE `WS001`. Isolation is still enforced in code first; the triggers only stop raw SQL.

### Per-stage requirement allocation

`stage_allocations` (`backend/app/domain/builds/stages.py:117`) returns `{stage_id: {project_entry_id: quantity}}`. For each BOM entry it takes `total = _required(entry, part, build.quantity)` — the same attrition-compounded, ceil-rounded integer the single-pass path uses — then splits it across the stages that reference it, in sequence order, **cumulatively**:

```
stage_n = ceil(total * Σportions[0..n] / 100) - ceil(total * Σportions[0..n-1] / 100)
```

Rounding each stage independently would either lose or invent units: `ceil(103 × 50%) × 2 = 104 ≠ 103`. Cumulative ceiling makes the parts sum to exactly `total` whenever the portions sum to 100 — the property that keeps a staged build consuming exactly what the equivalent single-pass build consumes. Every boundary rounds **up**, the same direction and for the same reason as `_required` (integer-only stock).

Worked example — `quantity=100`, `part.attrition_percentage=10`, `entry.attrition_pct=25`, `build.quantity=1`, two 50% stages:

```
total = ceil(100 × 1.10 × 1.25) = ceil(137.5) = 138
stage 1 = ceil(138 × 0.50) = 69
stage 2 = 138 - 69          = 69
```

### Reservations are up-front, released slice by slice

**Reservations are taken once, at build creation, for the whole build.** Creating a stage writes no ledger row. This is the explicit answer to "per-stage or up-front": per-stage reservation would double-count against `stock/service.py::reserved_quantity`, because the up-front `apply_reservations` has already reserved the full `_required` for every consumable entry.

Each stage consume therefore releases only its own slice, via `release_reservation_amounts` (`backend/app/domain/builds/service.py:349`):

- The amounts are grouped by the BOM entry's **own** `part_id` — the part the reservation was written against — even when the operator consumes a registered substitute.
- Reserve rows carry no `project_entry_id`, so the release is applied per part across that part's outstanding reserve rows, oldest first, partial on the last. That is exactly the granularity `reserved_quantity` reads.
- Over-asking is clamped to what is actually outstanding, so a stage cannot drive the reserved total negative.
- When the last stage completes, `release_reservations` frees whatever remains — stages whose portions sum to less than 100% would otherwise leak a permanent reservation.

`tests/test_build_stages.py::test_reservations_are_up_front_and_not_double_counted` pins both directions.

### Stage consume flow

`consume_stage` (`backend/app/domain/builds/stages.py:367`):

1. **Refuse** if the build is not `planned`/`in_progress`, is archived, or the stage is already `complete`.
2. **Refuse out-of-order consumption** — every earlier stage by `sequence` must be `complete`. Consuming stage 3 before stage 1 would report a physically impossible assembly state.
3. **Lock** the whole BOM (see step 2 of the consume orchestration above).
4. **Release this stage's reservation slice.**
5. **Apply the consume lines** through the shared `apply_consume_lines`, with `required_by_entry` = this stage's allocation and `build_stage_id` = the stage. A line pointing at a BOM entry outside the stage is rejected (`"project entry … is not in this stage"`).
6. **Mark the stage complete**; move the build `planned → in_progress`.
7. **If no stage remains**, produce the sub-assembly output (once, with `quantity = build.quantity`), complete the build, and release any reservation remainder.

Guards on the rest of the builds API:

- `POST /api/builds/{id}/consume` returns `400 build.has_stages` once the build has stages — the whole-BOM endpoint would draw every stage's stock at once while leaving the stages reported as un-built.
- `PATCH /api/builds/{id}` refuses a `quantity` change after any stage has been consumed (`400 build.read_only`): the change re-derives the up-front reservation and would re-reserve material a completed stage already consumed.

## `Build` row

`Build` (`backend/app/domain/builds/models.py:21`):

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
- **Never set `output_lot_id` outside `complete_build`.** It's the only writer (called by `consume` and by the final stage of `consume_stage`); tests assert that.
- **Never make reservation release existence-based again.** Partial releases are now real; "this reserve row already has a release row, skip it" would silently under-release, and "write a full counter regardless" would over-release. Both go through `_outstanding_reservations`.
- **Never derive a stage quantity from `project_entries.quantity`.** Stage portions slice `_required`'s output. Re-deriving skips attrition and makes the stages disagree with the whole-build shortage the operator planned against.
- **Never write reserve rows when a stage is created.** Reservations are up-front for the whole build; a per-stage reservation double-counts.
