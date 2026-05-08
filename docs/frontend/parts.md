# Parts Frontend

Audience: engineer

Part-detail UI flows that are larger than one tab component.

## Authorized Supply To Order

The Authorized-supply tab renders one `Add to order` action per distributor row and opens `CreateOrderLineModal`, which lists draft orders with `GET /api/orders?order_status=draft` and submits through the documented order endpoints. Existing draft orders receive a line via `POST /api/orders/{order_id}/entries`; the create-new branch posts `POST /api/orders` with one initial entry. The saved entry `comments` value is the compliance-safe TrustedParts summary only; the distributor page is available as a modal link but is not persisted in comments. Source: `web/src/routes/parts/detail/AuthorizedSupplyTab.tsx:318-343`, `web/src/routes/parts/detail/CreateOrderLineModal.tsx:82-154`.
