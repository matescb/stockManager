# Domain & Data Model

Audience: engineer

Per-entity reference: what each domain owns, how entities relate, the invariants that the rest of the codebase assumes. The domain layout mirrors `backend/app/domain/`.

Start with [`data-model.md`](data-model.md) for the ER diagram and the full table of models. Then pick the page for the slice you're touching.

## Pages

| File | Subject |
|---|---|
| [data-model](data-model.md) | ER diagram + every model + every FK + the polymorphic surfaces |
| [ledger](ledger.md) | `stock_entries` deep dive — operation types, locking, current-quantity reads |
| [workspace-isolation](workspace-isolation.md) | The code-enforced isolation rule + the one DB-enforced exception |
| [parts](parts.md) | Part types (linked / local / meta / sub-assembly), MPN uniqueness, archival |
| [lots-and-serials](lots-and-serials.md) | Lot lifecycle, splits, parent_lot_id, serial-tracked workspaces |
| [builds-and-bom](builds-and-bom.md) | Reservation, consume, shortage analysis, output_lot creation |
| [orders-and-receive](orders-and-receive.md) | Receive orchestration → ledger writes + lot creation |
| [providers](providers.md) | Mouser + DigiKey, OAuth, catalog vs spec key split, per-workspace credentials |
| [scan-import](scan-import.md) | `bag_signature`, MIL-STD-130N parser, idempotency table |
| [polymorphic](polymorphic.md) | Attachments, tags, custom_fields — the no-FK surface and how cleanup works |

## Hard invariants — index

These live in `CLAUDE.md` and are codified as ADRs. The domain pages assume you know them.

- Append-only stock ledger ([ADR-0001](../adr/0001-append-only-stock-ledger.md))
- Code-enforced workspace isolation ([ADR-0002](../adr/0002-code-enforced-workspace-isolation.md))
- API envelope `{ data, status }` ([ADR-0003](../adr/0003-api-envelope-data-status.md))
- MPN uniqueness per workspace ([ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md))
- Content-addressed asset storage ([ADR-0005](../adr/0005-content-addressed-assets.md))
- `bag_signature` normalization ([ADR-0006](../adr/0006-bag-signature-normalization.md))
- Provider catalog vs spec key split ([ADR-0007](../adr/0007-provider-catalog-vs-spec-split.md))
