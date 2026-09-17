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
| `list_columns` | Canonical spec keys the parts list shows as columns when filtered to this category, in order. Max 12. NULL inherits; `[]` means "no spec columns". Alembic 0083. |
| `list_sort` | `{"key": …, "dir": "asc"\|"desc"}` — that listing's default sort. NULL inherits. Alembic 0083. |

The last four columns all **inherit up `parent_id`, independently of each
other**, and all four read an explicit `[]` / a set value as a child
overriding its ancestors. `list_columns` and `list_sort` are the only two
that have nothing to do with KiCad: they exist because `DataTable`'s
hidden-column map is per-browser `localStorage`, and "resistors show
resistance, tolerance and power" is a fact about resistors that the whole
workspace should see. Their key vocabulary is validated against the
category's effective spec schema by
`backend/app/domain/parts/services/spec_columns.py`, which is also where the
schema's tree walk, the batched value fetch and the JOINed sort live.

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
a workspace the tree those defaults hang off — 28 rows, twelve of them
roots. Each carries `refdes_prefix`, footprint filters, `kicad_fields`, and
(where the class has a generic symbol) a `Device:*` `default_symbol_ref` and
a `value_template`.

The passive roots have children, because the sub-class changes the spec set:

| Root | Refdes | Children |
|---|---|---|
| Resistors | `R` | — |
| Capacitors | `C` | Ceramic / Electrolytic / Tantalum / Film |
| Inductors | `L` | Power / Ferrite bead / Common-mode choke |
| Diodes | `D` | Rectifier / Schottky / Zener / TVS / LED |
| Transistors | `Q` | BJT NPN / BJT PNP / MOSFET N / MOSFET P |

The active-component roots have none. A bare "Connectors" is still a
connector, so the root itself carries the slug and the fields:

| Root | Refdes | Symbol | Why that symbol |
|---|---|---|---|
| ICs | `U` | — | There is no `Device:U`; an IC's symbol is drawn per part. |
| Connectors | `J` | — | Depends on the pin count. |
| Crystals & Oscillators | `Y` | `Device:Crystal` | KiCad uses `Y` for crystals and `X` for oscillators. One category covers both, because no vendor taxonomy separates them reliably and the spec keys overlap; it takes the commoner prefix and an oscillator part overrides it on itself. |
| Fuses | `F` | `Device:Fuse` | Generic member of its family, like `Device:D`. |
| Switches | `SW` | — | A SPDT and a DIP-8 are not the same drawing. |
| Transformers | `T` | — | Winding count is part of the symbol. |
| Mechanical | `H` | — | Nothing to draw. Its `kicad_fields` is an explicit `[]`, so `package` is never emitted on a mounting hole. |

A default symbol that is wrong for every part in a class is worse than no
default: with none, the chooser falls through to the part's own symbol
instead of offering a two-pin box.

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
- **Refuses to guess between two categories with the same name.**
  `uq_part_categories_ws_name` is case-*sensitive*, so `Resistors` and
  `resistors` are two legal rows. The seed matches case-insensitively, sees
  two answers, and skips with "two active categories share this name" — a
  rename for a human, not a coin toss that a re-run could decide the other
  way.

It takes the workspace-tree advisory lock (`tree.py::lock_workspace_tree`)
before reading, but that lock does not cover every writer: `create_category`
only takes it when the new category has a parent, so a concurrent create of a
*root* can take a name between the check and the flush. That is a lost race,
not a bug — the workspace is rolled back to a savepoint, reported as
`skipped` with `another writer took a name or slug mid-run`, and the run
carries on with the next workspace. Re-run the job; it is idempotent.

The report is CSV on stdout (`workspace_id, workspace_name, path, action,
category_id, detail`); the logging goes to stderr, so
`… category-seed > seed.csv` gives a clean file. `category_id` is blank for a
row a dry run would create — the id exists only so children can be planned
against it, and printing a UUID that will never exist would be a lie in the
operator's report.

`--apply` writes one `audit_log` row per changed workspace, action
`category.seed`. `target_ids` names every category the run wrote, created
**and** updated: an update writes KiCad metadata onto a category a user made,
which is exactly what the audit trail is for. The comment is action counts
only, because a category name is user data.

A `--workspace` that names no workspace is an error, not an empty report.
Exit 0 with a header-only CSV reads as "there was nothing to do", which is
the one answer a typo in a UUID must not produce.

`--report <path>` sends the CSV to a file instead of stdout, on a dry run
as well as an apply. A path that cannot be opened is the same kind of
usage error as an unknown `--workspace`: exit 2 with a readable message.

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
whose category has no default, archived symbols (already falling through to
the default), and footprints — a footprint is per-package and genuinely
per-part.

"No default" means an empty string as well as NULL. `kicad_library.py:298`
reads the column for truthiness, so a category patched to `""` resolves to no
symbol at all; clearing `symbol_id` against it would leave those parts with
no symbol, and `_document` drops a part with no symbol out of the library
entirely. The predicate is `coalesce(default_symbol_ref, '') != ''` for that
reason and must stay that way. (`category-seed` repairs the underlying state:
a blank string counts as unset, so the seed fills it.)

It also re-checks each `part_eda` row against the symbol the report was built
from, and skips anything that changed in between — a `PUT /parts/{id}/eda`
between the dry run and the apply is a user decision. The audit row, the
comment count and the job's return value are all built from what was actually
cleared, so none of them can over-claim.

The report adds `action` (`would_clear` / `cleared` / `skipped`) and `detail`
to the columns, so a skipped race is visible rather than silently missing.

The `eda_symbols` rows themselves survive. Another part may still reference
one, and they are the record of what was imported.

Source: `backend/app/domain/eda/symbol_collapse.py`, pinned by
`backend/tests/test_symbol_collapse.py`.

## See also

- [`api/categories.md`](../api/categories.md) — the REST surface
- [`eda.md`](eda.md) — the KiCad library tables and the naming contract
- [ADR-0034](../adr/0034-spec-schema.md) — canonical specs, value templates,
  per-category spec columns
- [`api/parts.md`](../api/parts.md#per-category-spec-columns) — the
  `spec_columns` / `sort=spec:<key>` query surface
- `backend/app/domain/categories/README.md` — the module's own hard rules
