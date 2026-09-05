# Label Designer & Print Actions

Audience: engineer

The `/settings/label-templates` designer and the Print label actions on the five codeable entity types. Covers the mm coordinate model, the element/binding contract the canvas shares with the renderer, and the printer-failure path. The REST surface itself is in [`../api/label-templates.md`](../api/label-templates.md); the render pipeline is in [`../domain/labels.md`](../domain/labels.md).

## Where the code lives

| File | Role |
|---|---|
| `web/src/routes/labels/LabelTemplates.tsx` | Route entry — template list (DataTable) + create / duplicate / delete / make-default / seed built-ins |
| `web/src/routes/labels/Editor.tsx` | Stock form, palette, canvas, property panel, Save, Test print, JScript panel |
| `web/src/routes/labels/Canvas.tsx` | mm-accurate WYSIWYG surface: ruler, grid, pointer drag/resize, keyboard nudge |
| `web/src/routes/labels/PropertyPanel.tsx` | Per-kind element editor |
| `web/src/routes/labels/Palette.tsx` | Add-element tiles |
| `web/src/routes/labels/QrPreview.tsx` | Dimensionally exact QR placeholder |
| `web/src/routes/labels/geometry.ts` | Pure mm maths, QR sizing, binding resolution |
| `web/src/routes/labels/types.ts` | Zod schemas mirroring the server contract |
| `web/src/routes/labels/factory.ts` | Element/template factories + request serialisation |
| `web/src/routes/labels/data.ts` | TanStack hooks + `printErrorMessage` |
| `web/src/routes/labels/PrintLabelButton.tsx` | Detail-page action |
| `web/src/routes/labels/BatchPrintDialog.tsx` | List multi-select action |

Ported in structure from the sibling skladVA project (`/mnt/data/WORK/sklad`, `frontend/src/routes/labels/`) — `Canvas.tsx`, `Editor.tsx`, `PropertyPanel.tsx`, `Palette.tsx`, `geometry.ts`, `factory.ts`, `BatchPrintDialog.tsx`, `index.tsx` and `geometry.test.ts` are its files, adapted to this codebase's conventions and to a multi-tenant, workspace-scoped backend.

## Geometry is millimetres, not pixels

`label_templates` stores `width_mm` / `height_mm` and each element carries `x_mm` / `y_mm`; `backend/app/domain/printing/label_render.py:368-399` feeds those straight into the cab JScript job, which runs in mm. The designer therefore authors in mm and multiplies by a single `pxPerMm` zoom at paint time (`geometry.ts:29-52`). There is no pixel model and no dpi scaling on screen — printer dpi changes the raster the head burns, not the physical layout.

Consequences worth knowing:

- Snap grid, clamping and keyboard nudge all work in mm (`snapMm`, `clampToLabel`, `geometry.ts:61-92`).
- `toElementPayload` rounds to 0.01 mm before sending, so a pointer drag never persists `12.700000000000001` into the JSONB (`factory.ts`).
- Text is anchored top-left in the preview. The cab `T` command anchors at the font baseline; `label_render._text_baseline_anchor` (`backend/app/domain/printing/label_render.py:221-234`) does that conversion server-side, so the designer must not pre-compensate.

## Element and binding contract

`types.ts` mirrors four server-side lists and cites each. Change one and the other must follow:

| Client | Server |
|---|---|
| `LABEL_ENTITY_TYPES` | `domain/codes/models.py::CODE_ENTITY_TYPES` |
| `ELEMENT_KINDS` | `domain/printing/models.py::ELEMENT_KINDS` |
| element fields | `domain/printing/schemas.py::ElementIn` + the per-kind knobs `label_render` reads |
| `COMMON_BINDINGS` / `ENTITY_BINDINGS` | `domain/printing/template_service.py::_base_context` and `_entity_fields` |

There is no `image` kind: the renderer has none, so offering one would let an operator build a template that prints nothing.

### The literal-vs-binding rule

`label_render._resolve_text` falls through to `binding` only when `text` is **absent** — `text: ""` renders an empty field. So:

- `PropertyPanel`'s Literal/Field switch sets `text: undefined`, never `""`.
- `factory.ts::toElementPayload` omits an empty `text` key entirely.
- `geometry.ts::resolveTextValue` resolves in the same order, so the canvas shows what the printer will produce.

`web/src/routes/labels/__tests__/factory.test.ts` and `__tests__/geometry.test.ts` pin both directions.

## Preview

Two previews, both honest about what they are:

1. **Canvas** — WYSIWYG for text and rules. QR and barcode blocks are *placeholders sized to the real printed footprint*: the printer generates the symbols itself from the JScript `B` command, so no encoder ships in the bundle. For QR the footprint is exact — `geometry.ts::qrModuleCount` derives the version from the payload's UTF-8 byte length and the EC level using the ISO 18004 byte-capacity table, and the printer picks the same version.
2. **JScript panel** — `GET /api/label-templates/{id}/jscript` renders the template against sample data server-side. This is the ground truth for "why is my label coming out like that?". Only available for a saved, unmodified template.

Sample data is mirrored from `template_service.sample_context` (`geometry.ts::sampleContext`), so canvas and JScript panel agree.

## Print actions

Both actions call `POST /api/label-templates/{id}/test-print` with an `entity_id`. That is the object-print path — it renders the object's real data and mints its short code (get-or-create) — not a second endpoint. No print endpoint was added by this feature.

- **Detail pages** (`PrintLabelButton`): mounted in `EntityHeader`'s `actions` on Part, Lot, Storage location, Order and Build. The template list is fetched only when the dialog opens.
- **Lists** (`BatchPrintDialog`): wired into `DataTable`'s `selectionAccessory` on Parts and Storage. There is no server batch endpoint, so the dialog loops one call per selected object, **sequentially** (one physical printer, one per-workspace rate bucket) and **stops at the first failure** rather than queuing a failed print job per row. The selection is capped at `MAX_BATCH = 20` to match the endpoint's `20/minute` limit.

Both are admin-only server-side (`label_templates.py` module docstring — a template is shared infrastructure and `test-print` drives hardware). A member gets a 403, surfaced as "Printing labels needs an admin role in this workspace." A member-accessible print endpoint would be a new server surface; it is not in this feature.

## The failure path

Printing is disabled in production: `PRINT_HOST` is empty by default and the tunnel is set up by hand (see [`../deployment.md`](../deployment.md) — "Label printer connectivity", and CLAUDE.md). So the realistic outcome of pressing Print today is:

1. `print_service.send_jscript` raises `PrinterUnreachable`.
2. The route marks the `print_jobs` row `failed` and **returns** (not raises) `409` with `code: "printer.unreachable"` and `print_job_id` on the body — returning keeps the failed job from being rolled back with the request transaction.
3. `data.ts::printErrorMessage` turns that into: *"Printer not configured or unreachable — nothing was printed. The attempt was recorded as a failed print job (`<id>`) so it can be reconciled."*

The dialog stays open on failure so the job id remains readable. It is never a crash, and never a silent success. Other statuses are mapped too: 403 (role), 404 (template gone), 429 (rate limit); anything else falls back to `ApiError.userMessage`.

`web/src/routes/labels/__dom__/PrintLabelButton.dom.test.tsx`, `__dom__/BatchPrintDialog.dom.test.tsx` and `__dom__/Editor.dom.test.tsx` pin all of it.

## Tests

```bash
cd web && npm test -- src/routes/labels
```

- `__tests__/geometry.test.ts` — mm/px, snap, clamp, QR version/module sizing, binding precedence, preview sanitisation.
- `__tests__/factory.test.ts` — serialisation invariants (no element `id`, no empty `text`, no `entity_type` on PATCH).
- `__dom__/Editor.dom.test.tsx` — canvas placement, palette, property panel, keyboard nudge, save payloads, test-print failure.
- `__dom__/PrintLabelButton.dom.test.tsx` — the 409/403/429 paths and the no-template case.
- `__dom__/BatchPrintDialog.dom.test.tsx` — per-object loop, stop-on-failure, rate-limit cap.
