# ADR-0034: Per-category canonical spec schema for provider specs

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-09-15
- **Supersedes**: —
- **Superseded by**: —

## Context

Every row a provider lookup returns in `specs[]` is written verbatim as a
`custom_fields(source='provider')` row. There is no allow-list, no denylist and
no unit parsing. [ADR-0007](0007-provider-catalog-vs-spec-split.md) splits those
rows between the Specs and Sourcing tabs by key name, but it classifies only the
catalog side; everything it does not recognise falls through to the Specs tab.

Measured on prod, 2026-09-15:

| Fact | Value |
|---|---|
| Provider `custom_fields` rows | 9,377, all un-namespaced |
| `HTS code` / `ECCN` / `MSL` rows | 246 / 277 / 246 |
| Per-country customs codes | TARIC 29, CNHTS 31, USHTS 31, CAHTS 28, JPHTS 28, KRHTS 24, BRHTS 23, MXHTS 22 |
| Rows whose value is literally `-` | ~1,000 |
| Parts with no category | 118 of 324 |

So the Specs tab reads like a scrape of a customs declaration, and nothing on it
can be sorted or compared: the same resistance arrives as `10k`, `10 kOhms` or
`10.0 kOhm` depending on which provider and which field answered. There is also
no statement anywhere of what a resistor is *supposed* to have, so a part with
no tolerance and a part with every parameter look identical.

Three further things depend on having that statement:

- **KiCad `Value` and symbol fields** (Track B) need `10 kΩ 1% 0603`, which can
  only be rendered from named, parsed specs.
- **Loading from both DigiKey and Mouser** needs a rule for who wins per key.
  DigiKey is the only real parametric source — Mouser's `ProductAttributes` is
  packaging metadata and its parametric values are mined out of the description
  by nine regexes in `providers/mouser.py`.
- **Sorting and range-filtering specs** needs a number, not a string.

## Decision

Introduce a per-category canonical spec schema in
`backend/app/domain/parts/spec_schema.py` (+ `spec_schema_tables.py` for the
data, `spec_values.py` for the parser). It names the canonical keys for each
category, the provider field names that feed each key, a junk denylist, and the
catalog-vs-spec boundary; `normalise()` maps one provider payload onto it and
returns four disjoint buckets — `canonical`, `optional`, `catalog`, `dropped`.
Provider precedence for the same canonical key is **DigiKey > Mouser attribute >
Mouser description regex**, and a manual value is never overwritten.

`custom_fields` gains two nullable columns (alembic 0081): `provider`, so a
refresh can scope its delete pass by *who wrote the row* rather than by key
prefix, and `value_num` (`NUMERIC(36,18)`), the SI base-unit number behind
the display string.

## Consequences

- **Good**: one place answers "what is a resistor supposed to have", so
  `spec_incomplete` becomes computable, the KiCad `Value` template has named
  inputs, and `value_num` makes a spec sort a database operation. Adding a key
  or a provider alias is a one-line data edit in `spec_schema_tables.py`.
- **Good**: nothing parametric is lost. Any key that is neither junk, catalog
  nor canonical is kept verbatim under `optional`, so ICs and connectors —
  which have no canonical schema — behave exactly as they do today.
- **Trade-offs**: the alias tables are hand-maintained against DigiKey
  `ParameterText` strings we do not control. A renamed upstream field silently
  demotes a canonical key to `optional` rather than failing; the mandatory-key
  list is what surfaces that.
- **Trade-offs**: `parse_si` refuses rather than guesses, so a value it cannot
  read keeps its text and gets no `value_num`. That is deliberate — a wrong
  number is worse than no number.
- **What it forbids**:
  - **Don't add a key to `_UNITS` without deciding whether it scales.** `%`,
    `ppm/°C` and `°C` must never take an SI prefix — `0.5%` rendering as
    `500 m%` is the failure this table prevents, and `5 k` under a `%` key
    is unreadable rather than 5000%.
  - **Don't resolve a lone SI prefix case-insensitively.** `M` (mega)
    case-folds onto `m` (metre), so a case-folded lookup reads `10M` as ten
    metres — a silent factor of a million under every resistance key. The
    single-character case is resolved before the unit table, on the literal
    token.
  - **Don't classify a category by an adjective.** `film` and `ceramic` mean
    different things under different component classes;
    `category_slug_for` fixes the class from the component noun first and
    only then refines. A flat first-match pass made "Thick Film Resistors" a
    film capacitor.
  - **Don't store a parsed value whose unit is not the schema's.** A `"50 V"`
    that arrives under `resistance` keeps its text and gets no `value_num` —
    the index on that column exists so one key sorts as one quantity.
  - **Don't let `parse_si` raise.** Callers treat `None` as "keep the text";
    an exception would fail a whole provider import over one odd value.
  - **Don't add a catalog key on one side only.** `CATALOG_LITERAL_KEYS` in
    `spec_schema_tables.py` and in `web/src/lib/providerCatalog.ts` are checked
    against each other by `tests/test_spec_schema.py`; a one-sided edit puts
    the row on the wrong tab.
  - **Don't reuse one upstream alias for two canonical keys in one category.**
    The winner would depend on tuple order. Across categories it is fine and
    necessary: DigiKey files a ceramic capacitor's `X7R` under the same
    `Temperature Coefficient` name a resistor uses for its ppm/°C figure.
  - **Don't infer provenance from the key prefix once A3 lands.** After a
    secondary starts writing canonical keys, the prefix no longer identifies
    the writer; `custom_fields.provider` does.
  - **Don't sort on `value_num` without `value_num IS NOT NULL` in the query.**
    The supporting index is partial; without the predicate Postgres seq-scans.

## Follow-ups this ADR does not cover

- **A3** wires `normalise()` into `services/provider_import.py` and
  `api/routes/parts_refresh.py`, and updates
  `provider_fields.py::provider_owns_custom_field_key` and the ADR-0031
  contract. It must also widen `uq_cf_unique`
  (`workspace_id, object_type, object_id, key`) or pick a per-key winner before
  writing: two providers writing the same canonical key collide on that
  constraint today.
- **A4** maps provider category strings to our category tree. This ADR only
  maps *our* category names to schema slugs (`category_slug_for`).
- **A5** is the `spec-normalize` backfill that re-keys the 9,377 existing rows.
- **A7** renders mandatory-but-missing keys on the Specs tab.
- **B1/B2** add `part_categories.value_template` and render the KiCad `Value`
  from canonical specs.

## Alternatives considered

- **Keep storing specs verbatim and filter in the UI** — rejected: it leaves
  9,377 unsortable rows in the database, gives the KiCad library nothing to
  render a `Value` from, and pushes the same key-name knowledge into every
  consumer.
- **A units library (`pint`) instead of a hand-rolled parser** — rejected: the
  input is not unit expressions but vendor prose (`0.063W, 1/16W`,
  `26mOhm Max`, `1.8 A @ 100 kHz`). The work is in the prose handling, which a
  units library does not do, and it would add a dependency to parse strings we
  already have a 60-line table for.
- **A `spec_definitions` table instead of a Python module** — rejected for now:
  the schema is a product decision that ships with the code, review of a diff is
  the review we want, and a table would need seeding per workspace plus a
  migration for every key added. Revisit if workspaces need their own schemas.
- **Store `value_num` as `double precision`** — rejected: a picofarad is not
  exact in a double. `NUMERIC(36,18)` is, and the scale is deliberately wider
  than today's units need, because a value that does not fit is rounded by
  Postgres without complaint.
- **Drop unrecognised provider keys** — rejected: it would empty the Specs tab
  for every IC and connector, which have no canonical schema.

## References

- Source: `backend/app/domain/parts/spec_schema.py`,
  `backend/app/domain/parts/spec_schema_tables.py`,
  `backend/app/domain/parts/spec_values.py`
- Migration: `backend/alembic/versions/0081_custom_field_provider_value_num.py`
- Tests: `backend/tests/test_spec_schema.py`,
  `backend/tests/test_spec_values.py`,
  `backend/tests/test_custom_field_provider_value_num.py`
- Related: `backend/app/domain/parts/provider_fields.py`,
  `web/src/lib/providerCatalog.ts`,
  `backend/app/domain/parts/providers/mouser.py`
- Related: [ADR-0007](0007-provider-catalog-vs-spec-split.md),
  [ADR-0031](0031-primary-and-secondary-parts-providers.md)
