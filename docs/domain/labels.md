# Labels & Printing

Audience: engineer

The printing domain: what turns a row in the database into a physical label on
a bin. Covers the vendored cab SQUIX driver, the `print_jobs` ledger, the
`label_templates` layouts and the render engine that joins them. The REST
surface is [api/label-templates](../api/label-templates.md); the codes labels
carry are [api/codes](../api/codes.md).

## The pipeline

```
LabelTemplate (geometry + elements)
        +
context  {code, url, name, …}   <- template_service (DB + settings + code mint)
        |
        v
   label_render.render()        <- pure string rendering
        |
        v
     JScript program            <- composed from the vendored Job/Text/Barcode classes
        |
        v
   print_service.send_jscript()  ->  CabPrinter (raw TCP :9100)
        |
        v
   print_jobs row: queued -> sent -> printed | failed
```

Each arrow is a module boundary that exists for a reason:

- **`label_render` does no I/O.** Template + context in, string out. It never
  reads the DB, the settings or the network, so a label layout is testable
  without a workspace or a printer, and the injection guard has one place to
  live.
- **`template_service` owns everything variable.** The workspace filter, the
  object-code mint, `APP_BASE_URL`. It is the only module that knows a label
  belongs to a tenant.
- **`print_service` owns the transport and the lifecycle**, not the HTTP shape
  and not the rendering.

## Modules

`backend/app/domain/printing/`

| File | What |
|---|---|
| `cab_squix/` | Vendored cab SQUIX driver: the `Job` / `Text` / `Barcode1D` / `Barcode2D` JScript element model and the `CabPrinter` TCP transport. Vendored unmodified — do not edit to fix a rendering bug. |
| `models.py` | `PrintJob` (the ledger) and `LabelTemplate` (the layouts), plus `ELEMENT_KINDS` / `LABEL_ENTITY_TYPES`. |
| `print_service.py` | `send_jscript`, `send_jscript_batch`, `get_or_create_job`, `dispatch_queued_batch`, `reconcile_stale_print_jobs`. |
| `label_render.py` | `render`, `sanitize` — template + context → JScript. |
| `template_service.py` | Workspace-scoped template queries, the default swap, `ensure_defaults`, and the binding-context builders. |
| `default_templates.py` | The built-in default layout per entity type. |
| `schemas.py` | Request/response shapes for the router. |

## Print jobs

A physical print and a DB write cannot be one transaction. So every attempt is
a `print_jobs` row walked through `queued → sent → printed | failed`, and the
row — not the HTTP response — is the reconciliation point.

- `idempotency_key` is unique **per workspace** (composite UNIQUE), so a retry
  within a workspace dedupes to one job while two workspaces may reuse the
  same key independently.
- `target_type` / `target_id` are a polymorphic, un-constrained pointer (no
  FK), like `attachments.object_id`. `test-print` sets them to
  `label_template` and the template id.
- `PRINT_HOST` empty — the dev/CI/test default — means *no print sink*:
  `send_jscript` raises `PrinterUnreachable` so the failure path is exercised
  deterministically rather than silently "succeeding".
- A job stuck in `sent` past five minutes is swept to `failed` by
  `reconcile_stale_print_jobs`; queued batch jobs are shipped off the request
  path by `dispatch_queued_batch`. Both are maintenance sweeps in the
  [ADR-0021](../adr/0021-periodic-jobs-scheduler.md) sense.

Because `get_db` rolls back the request transaction on any raised exception, a
route that records a failed job must **return** its 4xx rather than raise it —
otherwise the row it tells the operator to inspect is rolled back with it. See
[api/label-templates](../api/label-templates.md#dont).

## Label templates

`label_templates` (migration `0076`), workspace-owned. Geometry in columns
(the renderer needs it to build the JScript `H` / `S` job header before it
reads a single element); the placed elements in one `elements` JSONB list
(read and written whole, never queried element-by-element).

At most one default per `(workspace_id, entity_type)`, enforced by the partial
unique index `uq_label_templates_ws_default … WHERE is_default`. Promoting a
template demotes the incumbent in the same transaction.

`entity_type` is the **same closed set** as `object_codes`, imported from
`domain/codes/models.py` rather than re-declared: every label carries a code,
so a type you cannot mint a code for is a type you cannot label. `project` is
absent from both — you do not stick a label on a project.

Defaults are materialised per workspace on demand by
`template_service.ensure_defaults` (`POST /api/label-templates/defaults`),
from a catalog that lives in Python. The catalog is deliberately not a
migration backfill: that would freeze a second copy which drifts on the first
tweak, and would give nothing to a workspace created afterwards.

## The injection guard

JScript is line-oriented — CR/LF separate commands, `;` separates a command's
parameters from its data. Any user-controlled string interpolated into a
command is therefore an injection vector: a part named `widget\r\nA 500` would
close the `T` command and open a new one telling the printer to run off 500
labels.

`label_render.sanitize` replaces every C0 control character with a space and
strips `;`. It runs on the **resolved** value — after binding substitution —
so it covers both a hostile literal in the template and a hostile value
arriving through a binding, and it runs for text, barcode payloads, QR
payloads and downloaded-font names alike.

`backend/tests/test_label_render.py` drives it with five real attack shapes
(CRLF + `A 500`, bare LF + `J`, a forged `;` field, a `S` geometry redefinition,
and raw C0 bytes) and asserts the rendered job still has exactly the expected
number of lines.

## See also

- [api/label-templates](../api/label-templates.md) — REST reference, element and binding tables
- [api/codes](../api/codes.md) — the short codes labels carry
- [polymorphic](polymorphic.md) — the no-FK pointer contract `print_jobs.target_id` follows
- [workspace-isolation](workspace-isolation.md) — why every query here filters by `ws.id`
- `backend/app/domain/printing/README.md` — module orientation
