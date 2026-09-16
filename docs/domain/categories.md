# Categories

Audience: engineer

`part_categories` — the workspace-scoped bucket a part belongs to, the tree
it hangs in, and the KiCad metadata it carries. The REST surface is
[`api/categories.md`](../api/categories.md); this page is the model and the
rules, plus the `category-seed` job that fills a workspace's tree in.

## The row

| Column | What |
|---|---|
| `name`, `description`, `sort_order` | Display. `name` is unique per workspace among active rows. |
| `parent_id` | Adjacency-list parent, NULL for a root. `ON DELETE SET NULL` (alembic 0078). |
| `library_slug` | The `SM_{slug}.kicad_sym` filename stem. Unique per workspace, **not** sibling-scoped. |
| `refdes_prefix` | Schematic reference designator — `R`, `C`, `Q`. |
| `default_symbol_ref` / `default_footprint_ref` | KiCad `LibNick:Entry` strings used when the part names neither. |
| `footprint_filters` | Footprint-chooser globs. |
| `value_template` | Renders the schematic `Value` from the part's canonical specs. NULL inherits. |
| `kicad_fields` | Canonical spec keys emitted as hidden symbol fields. NULL inherits; `[]` means "emit none". |

Source: `backend/app/domain/categories/models.py`. The module's own rules —
uniqueness, slug stability, why the tree walks live in Python — are in
`backend/app/domain/categories/README.md`.

## Resolution order, and why the seed matters

`kicad_library.py:292-300` resolves a part's symbol in this
order:

1. `part_eda.symbol_ref_external` — a string the user typed.
2. `part_eda.symbol_id` — a symbol this workspace hosts.
3. the part's category `default_symbol_ref`.
4. nothing; the part gets no symbol.

Step 3 is the one the category owns, and it was NULL on every category in
production. Every vendor zip a workspace imports ships its own symbol and
lands at step 2, so eighty imported resistors meant eighty `R` symbols in
the KiCad chooser, all drawing the same two-terminal box. Pointing the
passive categories at KiCad's own `Device` library collapses that to one
symbol per class — and ships no symbol bytes at all, because a
`Device:R` is resolved by the KiCad install, not by us.

## `category-seed`

An operator-run [backend job](../deployment.md#operator-run-jobs) that gives
a workspace the passive tree those defaults hang off: Resistors; Capacitors
with Ceramic / Electrolytic / Tantalum / Film; Inductors with Power /
Ferrite bead / Common-mode choke; Diodes with Rectifier / Schottky / Zener /
TVS / LED; Transistors with BJT NPN / BJT PNP / MOSFET N / MOSFET P. Each
row carries `refdes_prefix`, a `Device:*` symbol, footprint filters, a
`value_template` and `kicad_fields`.

```bash
# dry run — prints a CSV of what it would do, writes nothing
docker compose -f docker-compose.dev.yml exec backend \
    python -m app.cli.run_job category-seed
# then, after reading it
docker compose -f docker-compose.dev.yml exec backend \
    python -m app.cli.run_job category-seed --apply
```

What it will and won't do:

- **Creates** a category only when the workspace has no active one with that
  name under that parent, matched case-insensitively. Roots match by name
  anywhere in the workspace, so a *Capacitors* the user filed under their own
  *Passives* umbrella keeps its place and the leaves hang off it.
- **Fills** `description`, `refdes_prefix`, `default_symbol_ref`,
  `footprint_filters`, `value_template` and `kicad_fields` on a category that
  already exists, but only where the column is still NULL (or blank, or an
  empty array). `kicad_fields = []` counts as set — it is how a category says
  "emit no symbol fields" against a parent that emits some.
- **Never** renames, re-parents, re-orders, overwrites or deletes anything.
- **Reports and skips** rather than working around a collision: a name or a
  `library_slug` already used elsewhere in the workspace, an archived
  category of that name, a parent that was itself skipped, or a nesting that
  would pass `tree.MAX_DEPTH`.

It takes the workspace-tree advisory lock (`tree.py::lock_workspace_tree`)
before reading, but that lock does not cover every writer: `create_category`
only takes it when the new category has a parent, so a concurrent create of a
*root* can take a name between the check and the flush. That is a lost race,
not a bug — the workspace is rolled back to a savepoint, reported as
`skipped` with `another writer took a name or slug mid-run`, and the run
carries on with the next workspace. Re-run the job; it is idempotent.

The report is CSV on stdout (`workspace_id, workspace_name, path, action,
category_id, detail`); the logging goes to stderr, so
`… category-seed > seed.csv` gives a clean file. `--apply` writes one
`audit_log` row per changed workspace, action `category.seed`, carrying the
created ids and a counts-only comment.

Source: `backend/app/domain/categories/seed.py`, data in
`seed_tables.py`, pinned by `backend/tests/test_category_seed.py`.

### The templates are checked against the spec schema

A `value_template` placeholder that is not a canonical spec key for that
category renders empty on every part, forever, and nothing fails — you find
out with a schematic open. So `test_category_seed.py::
test_template_and_field_keys_are_canonical_for_the_category` resolves each
seeded path through `spec_schema.category_slug_for` (the same function the
import path uses) and refuses a key the resulting schema does not define.
Adding a row to `seed_tables.py` with a made-up key fails the build.

See [ADR-0034](../adr/0034-spec-schema.md) for the spec schema itself.

### Apply the spec normalisation first

A `value_template` renders whatever placeholders resolve and drops the rest,
and `kicad_library.py` prefers a non-empty render over `parts.name`. So a
resistor that carries a `package` spec but no canonical `resistance` renders
a Value of `0603` — technically correct, useless on a schematic, and worse
than the provider description it replaced.

That is not a seed bug, it is an ordering one. Run the `spec-normalize`
backfill first, so the passives have canonical specs, and only then
`category-seed --apply`. Parts with no specs at all are unaffected: an empty
render falls back to the part name, which is the behaviour that was there
before.

## `symbol-collapse`

The companion job, for workspaces that already imported vendor zips. It
clears `part_eda.symbol_id` on exactly the parts where the symbol came from
a vendor zip (`eda_symbols.source != 'manual'`) **and** the part's category
has a `default_symbol_ref` to fall back on, so the category default finally
applies. Dry run by default, same as above.

```bash
docker compose -f docker-compose.dev.yml exec backend \
    python -m app.cli.run_job symbol-collapse
```

It leaves alone: hand-uploaded symbols, external refs the user typed, parts
whose category has no default (clearing there leaves the part with no symbol
at all), archived symbols (already falling through to the default), and
footprints — a footprint is per-package and genuinely per-part.

The `eda_symbols` rows themselves survive. Another part may still reference
one, and they are the record of what was imported.

Source: `backend/app/domain/eda/symbol_collapse.py`, pinned by
`backend/tests/test_symbol_collapse.py`.

## See also

- [`api/categories.md`](../api/categories.md) — the REST surface
- [`eda.md`](eda.md) — the KiCad library tables and the naming contract
- [ADR-0034](../adr/0034-spec-schema.md) — canonical specs, value templates
- `backend/app/domain/categories/README.md` — the module's own hard rules
