# Phase 9 — Serial tracking

The schema had `Workspace.serial_tracking_enabled` and
`Lot.serial_number` since Phase 1, but nothing enforced them. Phase 9
adds:

- `parts.serialized` (new column, alembic 0004) — flag per part
- workspace-level toggle to switch enforcement on/off
- enforcement in `add_stock()` and the order-receive service
- UI affordances (workspace settings, part settings, add-stock form,
  order-receive form)

Expiring lots (the originally-paired feature) was already implemented
in Phase 6's reports — see `docs/phases/06-reports.md`.

## Migration

`0004_part_serialized.py`:

```python
op.add_column('parts',
    sa.Column('serialized', sa.Boolean(), nullable=False, server_default=sa.false()))
op.alter_column('parts', 'serialized', server_default=None)
```

The `server_default → drop` pattern lets the column be `NOT NULL` on a
non-empty table without leaving a Postgres-side default that diverges
from the SQLAlchemy model.

## Enforcement rule

When **both** `Workspace.serial_tracking_enabled` is true **and** the
target part has `Part.serialized = true`, every operation that creates
on-hand stock must:

1. Use exactly `quantity = 1` (one unit per lot per request line).
2. Provide a non-empty `serial_number`.

This applies to:

- `POST /api/stock/add` — checked in `domain/stock/service.py::add_stock`
- `POST /api/orders/{id}/receive` — checked per line in
  `domain/orders/service.py::receive`

If either side is off (workspace flag off, or part not flagged
serialized), the existing behaviour is unchanged.

## Endpoints

```
PATCH /api/workspaces/current   { name?, currency_default?,
                                  lot_control_enabled?, serial_tracking_enabled? }
```

`POST/PATCH /api/parts` and `LotInput` and `ReceiveLineIn` all gain
`serialized` / `serial_number` fields where appropriate.

## UI

- **Workspace settings** (`/settings/workspace`) — name + currency
  inputs, checkboxes for lot control and serial tracking, with a hint
  about what serial tracking enforces.
- **Part create / settings** — `Serialized` checkbox (also in PartCreate).
- **Part > Add stock** — Serial number input alongside Lot name.
- **Order detail > Receive** — Serial # column; serialized parts get a
  warning pill in the part column to remind the user.
- **Part > Lots** list shows the serial number column (already wired
  via the existing `LotPatch` schema).

## Tests

`backend/tests/test_serial_tracking.py`:

- workspace patch toggles the flag
- serial required when both flags on (missing serial → 400, qty>1 → 400,
  qty=1 with serial → 200)
- enforcement off when workspace flag is off, even if the part is
  flagged serialized
- order-receive: missing serial → 400, qty>1 → 400, two single-unit
  lines accepted (each producing its own lot with the right serial)
