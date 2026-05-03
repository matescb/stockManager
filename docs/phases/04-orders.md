# Phase 4 — Purchase orders & receiving

Audience: engineer

Adds purchase orders, line-level receive flow, and the lot-of-purchase
audit trail that previous phases left scaffolded but unused.

## Domain

| Table          | What it is |
|----------------|------------|
| `orders`       | Header row: name, supplier, status, dates, currency. |
| `order_entries`| One per part being purchased: `quantity_ordered`, `quantity_received`, `unit_price`. Cached `quantity_received` is the source of truth for "what's still outstanding". |

`Order.status` advances automatically based on the sum of received vs.
ordered across entries: `draft` → `open` → `partial` → `received`.
`cancelled` is a manual terminal state. `archived_at` (from
`WorkspaceOwned`) is independent of status.

## Receiving

`POST /api/orders/{id}/receive` accepts a list of lines:

```json
{ "received_on": "2026-04-29",
  "lines": [
    { "order_entry_id": "…", "quantity": 100, "storage_location_id": "…", "lot_name": "PO-001#1" }
  ] }
```

For each line the service emits:

1. A `lots` row with `source_type='purchase'`, `source_order_id` set,
   and `purchase_quantity` / `purchase_unit_cost` / `purchase_currency`
   populated from the order entry. (`lot_name` is auto-derived from
   `<order.name>#<line index>` if not given.)
2. A `stock_entries` row with `operation_type='receive'`, `quantity_delta`
   positive, and `order_id` / `order_entry_id` linking back to the order.

Receiving is all-or-nothing per request: any error rolls the whole batch back.
Constraints enforced:

- Cannot receive an entry without a `part_id` — match it first.
- Cannot receive into an archived or `is_full` storage location.
- Cannot over-receive (`quantity > quantity_ordered − quantity_received`).
- Cannot receive against a `cancelled` order.

After every receive, `quantity_received` is updated and the order status
is recomputed.

## API surface

```
GET    /api/orders                     ?archived=&q=&order_status=
POST   /api/orders                     create (with optional initial entries)
GET    /api/orders/{id}                {order, entries[]}
PATCH  /api/orders/{id}                edit metadata + status
POST   /api/orders/{id}/archive
POST   /api/orders/{id}/restore
POST   /api/orders/{id}/entries        add a line
PATCH  /api/orders/{id}/entries/{eid}
DELETE /api/orders/{id}/entries/{eid}  blocked once any qty received
POST   /api/orders/{id}/receive        partial / full receive
```

Search (`/api/search?q=…`) now returns matched orders.

## UI

- `/orders` list with status badge + received-vs-ordered progress
- `/orders/create` — header form
- `/orders/{id}` — header card, line editor, receive form

## Tests

`backend/tests/test_orders.py` covers: empty draft → open transition,
partial-then-full receive (verifies on-hand stock + lot rows),
over-receive rejection, unmatched-entry rejection, delete-after-receive
rejection, archive/restore.
