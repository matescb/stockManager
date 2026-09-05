# printing

Audience: engineer

Owns everything between a database row and a physical label: the vendored cab
SQUIX driver, the `print_jobs` ledger, the `label_templates` layouts, and the
render engine that turns a template plus a context into a JScript program.

## Files

| File | What |
|---|---|
| `cab_squix/` | Vendored cab SQUIX driver — `Job` / `Text` / `Barcode1D` / `Barcode2D` (the JScript element model) and `CabPrinter` (raw TCP :9100). Vendored **unmodified**. |
| `models.py` | `PrintJob`, `LabelTemplate`, `ELEMENT_KINDS`, `LABEL_ENTITY_TYPES` |
| `print_service.py` | `get_or_create_job`, `send_jscript`, `send_jscript_batch`, `dispatch_queued_batch`, `reconcile_stale_print_jobs` |
| `label_render.py` | `render`, `sanitize` — template + context → JScript. **Pure.** |
| `template_service.py` | Workspace-scoped template queries, `clear_existing_default`, `ensure_defaults`, `sample_context`, `context_for_entity`, `scan_url` |
| `default_templates.py` | `BUILT_IN_TEMPLATES` — the default layout per entity type |
| `schemas.py` | Request/response shapes for the router |

## Public surface

| Operation | Entry point |
|---|---|
| Render a template to JScript | `label_render.py::render` |
| Strip JScript separators from free text | `label_render.py::sanitize` |
| Build a label's bindings for a real object | `template_service.py::context_for_entity` |
| Materialise the built-in defaults | `template_service.py::ensure_defaults` |
| Ship JScript to the printer | `print_service.py::send_jscript` |

REST surface: `backend/app/api/routes/label_templates.py`
(`/api/label-templates`), documented in
[docs/api/label-templates.md](../../../../docs/api/label-templates.md).

## Hard rules (this module)

1. **`label_render` does no I/O.** No DB, no settings, no network — template +
   context in, string out. Everything variable is assembled by
   `template_service`, which is the only module here that knows about tenants.
2. **Every resolved free-text fragment goes through `sanitize`.** JScript is
   line-oriented (CR/LF separate commands, `;` separates parameters from
   data), so an unsanitised part name can forge a command. The guard runs
   *after* binding substitution, on text, barcode and QR payloads and font
   names alike.
3. **One default per `(workspace_id, entity_type)`**, partial unique index.
   Promote only via `clear_existing_default` in the same transaction.
4. **`LABEL_ENTITY_TYPES` is imported from `domain/codes/models.py`**, never
   re-declared. A label carries a code; the two sets cannot drift.
5. **`{{code}}` comes from `codes_service.mint_or_get`** and `{{url}}` is
   `{APP_BASE_URL}/c/{code}`. There is one code system and one URL shape.
6. **Don't edit `cab_squix/`.** It is vendored from the MIT-licensed toolkit
   and shared with the sibling skladVA project; a rendering bug belongs in
   `label_render.py`.

## See also

- [docs/api/label-templates.md](../../../../docs/api/label-templates.md) — REST reference, elements, bindings
- [docs/domain/labels.md](../../../../docs/domain/labels.md) — the pipeline and the job lifecycle
- [docs/api/codes.md](../../../../docs/api/codes.md) — the codes labels carry
- [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md) — workspace isolation

## Don't

- Don't `raise` a 4xx after recording a failed `print_jobs` row — `get_db`
  rolls back on any raised exception and the row goes with it. Return the
  response instead (`routes/label_templates.py`).
- Don't render "trusted" values without the guard. Every field on a label came
  from somewhere.
- Don't add a second scheduler for the print sweeps — they run through
  `run_job` per [ADR-0021](../../../../docs/adr/0021-periodic-jobs-scheduler.md).
