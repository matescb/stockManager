# Phase 15 — Box intake and box audit

Audience: engineer

Two scan-driven modes on a storage location, so a drawer of old bags or a
fresh delivery can be loaded box by box and then checked box by box.

## Why

- Scan-to-import (phase 11) only *creates* parts. A bag whose part was
  already in the library came back `duplicate` and **no stock was
  added**. Loading boxes of known parts, the common case, went through
  the manual add-stock form one bag at a time.
- A bag only got its own lot when its label carried a lot or date code.
  Otherwise its quantity merged into the box's NULL-lot bucket, so
  nothing could later say "this bag is here, that one is not".
- There was no way to check a box against what the ledger says is in it.

## What shipped

| Piece | Where |
|---|---|
| Endpoints `scan/resolve`, `intake`, `bags`, `audit` under `/api/storage/{id}` | `backend/app/api/routes/storage_scan.py` |
| Logic | `backend/app/domain/stock/box_scan.py` |
| Schemas | `BoxScanResolveIn`, `BoxIntakeIn`, `BoxAuditIn` in `domain/stock/schemas.py` |
| Pages `/storage/:id/intake`, `/storage/:id/audit` | `web/src/routes/storage/boxScan/` |
| USB / Bluetooth keyboard-wedge reader | `web/src/components/scanner/keyboardWedge.ts`, `useKeyboardWedge.ts` |

No migration: lots and `stock_entries.bag_signature` already existed.

## Decisions

- **One scanned bag = one lot, always.** The lot is named from the
  label's lot/date code, else `Bag YYYY-MM-DD`; the `add` entry carries
  the bag signature. A box's contents are then its list of (part, lot)
  buckets, and an audit can be done per bag.
- **A bag recorded elsewhere is moved, not added.** Intake defaults to
  `on_existing: "move"`, which relocates every remaining unit of that lot
  into the box (`move_quantity`) and then sets the counted quantity
  (`adjust_stock`). `add_new` exists for two physical bags with
  identical labels. The UI asks before moving.
- **Re-scanning is idempotent.** Intake of a bag already in the box is
  `already_here` and only corrects the count, so a retried request or a
  double scan cannot double the stock.
- **Old untracked stock gets written off by default.** NULL-lot buckets
  show in the audit as "old record, not tracked per bag" and, unscanned,
  default to write-off. Stock entered before this phase is replaced box
  by box as each box is audited. There is no global wipe.
- **An audit applies all at once.** The page keeps the draft in
  `sessionStorage`. `POST /audit` applies every verdict in the request's
  transaction, and one failing line rolls the whole audit back. Bags found
  during an audit that are not recorded in the box are added through
  intake straight away. They are physically there, whatever the verdict
  on the rest.
- **Write-off is `adjust` to 0, not `remove`.** It is a count
  correction, and the ledger keeps the history.
- **Camera scanning is unchanged; the wedge reader is new.** Fast keystrokes ending
  in Enter, outside form fields, are a scan. Ctrl+^ / Ctrl+] / Ctrl+D map
  back to RS / GS / EOT, so the bag signature matches the camera's.

## Not done

- No server-side bag-code parsing: the client still parses (phase 11
  deferral stands); the server only verifies the signature.
- No per-box audit history beyond the `storage.audited` audit-log row and
  the ledger entries tagged `box audit`.
