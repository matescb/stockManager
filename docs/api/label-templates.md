# Label Templates API

Audience: engineer

A label template is a reusable layout: the stock geometry (what the media
physically is) plus an ordered list of placed elements (what gets drawn on
it). The render engine turns one, plus a binding context, into a complete cab
JScript program; `test-print` ships that program to the printer and records a
[print job](../domain/labels.md#print-jobs).

Every label carries the object's [short code](codes.md) — there is one code
system in this app and labels use it.

## Conventions

See [API conventions](./README.md) for envelope, errors, auth. Mounted at
`/api/label-templates` with `dependencies=_member_gate`
(`backend/app/main.py`), so **reads** need an active member. Every
**mutation** is admin+: a template is shared infrastructure — one row decides
what every label in the workspace looks like — and `test-print` drives
physical hardware, so managing them is an operator task, not a member one.

That admin check is expressed two ways, and the split is load-bearing
(BE2-009):

| Routes | Mechanism | Why |
|---|---|---|
| `POST ""`, `POST /defaults` | `Depends(require_role("admin"))` at the route | They name no resource, so there is nothing to resolve first. |
| `PATCH`/`DELETE`/`test-print` on `/{template_id}` | `_helpers.require_resource_access(…, role="admin")` in the handler | A route-level dependency runs *before* the handler, so a non-admin probing another workspace's id would get `403` — an oracle saying "this id exists somewhere, you just lack the role". Existence + workspace resolve first (both `404`), role second (`403`). |

`test-print` is rate-limited per workspace at `20/minute`.

## Model

`label_templates` (migration `0075`), `WorkspaceOwned`.

| Column | Notes |
|---|---|
| `entity_type` | `build` / `lot` / `order` / `part` / `storage_location` — the **same closed set** as `object_codes`, imported from it rather than re-declared. A label carries a code, so a type you cannot mint a code for is a type you cannot label. |
| `name` | Free text, ≤ 200 chars. |
| `is_default` | The template the print flows pick when none is named. |
| `width_mm`, `height_mm`, `gap_mm` | Stock geometry, `Numeric(6,2)`. Drives the JScript `S` command; pitch = `height + gap` for die-cut media. |
| `heat`, `speed`, `method`, `dpi` | Print parameters. `method` is `T` (thermal transfer) or `D` (direct thermal), CHECK-constrained. Drives the `H` command. |
| `elements` | JSONB list of placed elements. |

Geometry gets columns and elements gets JSONB deliberately: the renderer needs
the geometry to build the job header *before* it looks at a single element,
while the element list is read and written whole and never queried
element-by-element.

### One default per (workspace, entity type)

Partial unique index `uq_label_templates_ws_default` on
`(workspace_id, entity_type) WHERE is_default`. Partial, so any number of
**non**-default templates per type coexist; scoped to the workspace, so each
tenant holds its own default for the same type.

Promoting a template demotes the incumbent **inside the same transaction**
(`template_service.clear_existing_default`). A bare flip would hit the index
and surface as an unactionable `IntegrityError`.

### Elements

| `kind` | Renders as | Element-specific keys |
|---|---|---|
| `qr` | JScript `B` (QRCODE) | `dotsize_mm`, `ec` (`L`/`M`/`Q`/`H`) |
| `text` | JScript `T` | `font` (device id or downloaded TrueType name), `size_pt` |
| `barcode1d` | JScript `B` | `bc_type` (default `CODE128`), `height_mm`, `ne_mm` |
| `handwriting` | JScript `G` line — a rule to write on | `w_mm` (length), `h_mm` (thickness) |

Shared keys: `x_mm`, `y_mm` (top-left, millimetres), `rotation` (clockwise, as
the designer applies it — the renderer negates it for cab's counter-clockwise
convention), and either `text` (a literal, which may itself contain bindings)
or `binding` (a bare token). Unknown `kind` values are rejected on write with
`400 label_template.invalid`; the list is capped at 100 elements.

`y_mm` is the element's **top** edge for every kind. cab anchors text at its
font baseline, so the renderer shifts a `text` element down by the font ascent
— otherwise text prints one ascent higher than everything beside it.

## Bindings

An element's text or payload may contain `{{token}}`. Resolution is
context-driven; an **unknown token resolves to the empty string** rather than
raising, so a template outliving a binding rename prints a slightly emptier
label instead of failing the job.

| Token | Value |
|---|---|
| `code` | The object's short code, from [`POST /api/codes`](codes.md)'s service (mint-or-get). |
| `url` | `{APP_BASE_URL}/c/{code}` — the scan-to-open URL, i.e. the `/c/:code` frontend route. |
| `name` | The row's `name`. |
| `entity_type`, `workspace` | The type being labelled; the workspace name. |
| `mpn`, `manufacturer`, `description` | Part fields (a lot inherits its part's `mpn` / `manufacturer`). |
| `part_name`, `serial` | Lot fields. |
| `supplier`, `status` | Order fields (`status` is also set for a build). |
| `project_name`, `quantity` | Build fields. |

A `qr` element with neither `text` nor `binding` defaults to `{{url}}` — that
is what a QR on a label is for.

### JScript injection guard

JScript is line-oriented: CR/LF separate commands and `;` separates a
command's parameters from its data. A part named `widget\r\nA 500` pasted raw
into a `T` command would end that command and start a new one telling the
printer to run off 500 labels.

So **every resolved free-text fragment** — text, barcode payload, QR payload,
and downloaded-font names — goes through `label_render.sanitize`, which
replaces all C0 control characters with spaces and strips `;`. It runs *after*
binding substitution, so it covers a hostile literal in the template and a
hostile value arriving through a binding (a part name, a lot serial, an order
supplier: all user-controlled) alike. Fields are then capped at 2000
characters.

## Routes

### `GET /api/label-templates`

Optional `?entity_type=`. Returns the workspace's templates, default first
within each type.

### `POST /api/label-templates`

Admin+. `201` with the created template. Setting `is_default` demotes the
incumbent for the same entity type first.

Audit: `label_template.created`.

### `POST /api/label-templates/defaults`

Admin+. Materialises the **built-in default** template for every entity type
that does not already have one. Idempotent get-or-create — like `POST
/api/codes`, it returns `200` (not `201`) because most calls create nothing,
and it writes an audit row (`label_template.defaults_seeded`) only when it
actually creates. A default the operator has since edited is left alone.

The catalog itself lives in Python
(`backend/app/domain/printing/default_templates.py`), not in a migration
backfill: a migration would freeze a second copy that drifts on the first
tweak, and would give nothing to a workspace created afterwards.

### `GET /api/label-templates/{id}`

`404` for unknown **and** other-workspace ids alike.

### `PATCH /api/label-templates/{id}`

Admin+. Partial. `entity_type` is deliberately not patchable — retargeting a
template would silently invalidate every binding it places. An empty body is
`400 label_template.invalid`.

Audit: `label_template.updated`, comment lists the changed field names.

### `DELETE /api/label-templates/{id}`

Admin+. Audit: `label_template.deleted`.

### `GET /api/label-templates/{id}/jscript`

Returns `{ "jscript": "..." }` — the program this template renders to, for a
**sample** context. A pure read: it touches no rows and mints no code, so it
works on a workspace that has never labelled anything. The debug view for
"why is my label coming out like that?".

### `POST /api/label-templates/{id}/test-print`

Admin+. Body: optional `entity_id`, optional `copies` (1–20).

With no `entity_id`, renders sample data. With one, renders that object and
mints its code through the object-codes service — the same code
`GET /api/codes/{code}` resolves. A foreign `entity_id` is `404` (the mint
validates it before reading or minting anything).

Records a `print_jobs` row (`kind=on_demand`, fresh idempotency key per call —
a test print is an explicit "do it again") and walks it through
`queued → sent → printed`. Returns `{ print_job_id, status, code }`.

Audit: `label_template.test_printed`, or `label_template.test_print_failed`.

**Printer failure is a `409`, never a `500`:**

```json
{
  "data": null,
  "status": { "category": "conflict", "message": "the label printer is unreachable; …" },
  "code": "printer.unreachable",
  "print_job_id": "…"
}
```

The job row survives in status `failed` with the driver's reason in `error`.
That includes the unconfigured case: `PRINT_HOST` empty is the dev/CI default
and fails closed with the same `409`.

## Don't

- **Don't turn the printer-failure return into a `raise_http`.** `get_db`
  rolls back on any raised exception, so raising would roll back the very
  `print_jobs` row the response tells the operator to inspect. The route
  returns a `JSONResponse` built with `responses.err()` so the transaction
  commits; the body is identical to what `raise_http` would emit.
- **Don't do I/O in `label_render`.** It is pure template + context → string.
  The DB, the settings and the code mint all live in `template_service`.
- **Don't bypass `sanitize` for "trusted" values.** Every field on a label
  came from somewhere; the guard is cheap and total.
- **Don't invent a second code system or URL shape.** `{{code}}` comes from
  `object_codes` and `{{url}}` is `{APP_BASE_URL}/c/{code}`.
- **Don't flip `is_default` without clearing the incumbent** in the same
  transaction.
- **Don't widen `entity_type`** without widening `object_codes` first — the
  label set is imported from the code set.
- **Don't put `Depends(require_role(…))` on a `/{template_id}` route.** It
  runs before the handler and turns a foreign-id probe into a `403` oracle
  (BE2-009). Use `require_resource_access`.

## See also

- [codes](codes.md) — the code system labels print and scanners resolve
- [domain/labels](../domain/labels.md) — the printing domain, the driver, the job ledger
- `backend/app/domain/printing/README.md` — module orientation
