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

**A3 (2026-09-16) wired it in.** `services/spec_reconcile.py` is the single
writer for both ingest paths — the create path (`services/provider_import.py`)
and the refresh route (`api/routes/parts_refresh.py`), which no longer has a
reconciler of its own. Three decisions landed with it:

- **Canonical keys are un-namespaced from both provider tiers**, so ownership
  for the delete pass moved from the key prefix to `custom_fields.provider`.
  See the A3 amendment in [ADR-0031](0031-primary-and-secondary-parts-providers.md).
- **`uq_cf_unique` was NOT widened.** The ADR left the choice open between
  widening the constraint and picking a per-key winner before writing; the
  winner is what shipped, because the alternative stores two answers to a
  question with one answer and makes every reader — the Specs tab, the KiCad
  `Value` template, a future spec sort — pick between them at read time. One
  row per canonical key, stamped with who won.
- **Junk is archived, not deleted.** A customs code or a `-` value is never
  written, and an existing row for one gets `archived_at` rather than a hard
  delete: it is the one class of row this change removes from parts that have
  carried it for months, and an archived row can be read back and counted. The
  read paths (`GET /api/custom-fields/by-object/...`, the MCP part-detail tool)
  gained the `archived_at IS NULL` predicate that made the column mean
  something, and a manual upsert un-archives rather than writing into a row the
  user can no longer see — `uq_cf_unique` does not exclude archived rows.

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
  - **Don't infer provenance from the key prefix.** A3 has landed: a secondary
    writes canonical keys, so the prefix no longer identifies the writer.
    `custom_fields.provider` does, through
    `provider_fields.py::provider_wrote_custom_field_row`. Applying the same
    provenance test to NON-canonical keys is the mirror-image bug — it would
    leave a switched-over workspace unable to prune the old primary's bare rows,
    and would let unlinking a demoted primary delete the part's `image_url`.
    Claiming an UNSTAMPED canonical row is a write rule, never a delete rule.
  - **Don't let a lower-precedence provider overwrite a canonical value.**
    Mouser's parametric values are mined out of prose by nine regexes;
    DigiKey's come from a real attribute table. Refresh order must not decide
    which one a part keeps, which is what `provider_outranks` is for.
  - **Don't assign a category over one the user chose.** `apply_provider_category`
    only ever fills a NULL `category_id`, and resolves paths without creating
    anything — a vendor taxonomy we do not control must not grow a curated tree.
  - **Don't sort on `value_num` without `value_num IS NOT NULL` in the query.**
    The supporting index is partial; without the predicate Postgres seq-scans.
  - **Don't let the A5 backfill delete a row, or hand it
    `reconcile_provider_specs`.** Its payload is the current database state,
    so the reconcile's "delete everything absent from my payload" pass has
    nothing to mean and everything to take. See the A5 note below.
  - **Don't write a canonical row without `custom_fields.provider`.** An
    unstamped canonical row is claimable by whoever refreshes next, which is
    the ownership rule working as designed — and exactly why a backfill that
    leaves one behind gives a normalised value away.

## Follow-ups this ADR does not cover

- ~~**A3**~~ — landed 2026-09-16; see the Decision section above.
- ~~**A4**~~ — landed with A3. `spec_category_map.py::category_for_provider` maps
  a DigiKey or Mouser category string to one of our category name paths, and
  `categories/service.py::resolve_category_path_or_root` resolves that path
  against a workspace's own tree without creating anything. It stops at the
  class for MOSFETs and BJTs (`Transistors / MOSFET`, not `… / MOSFET N`):
  N- vs P-channel is in the `fet_type` SPEC, not in the vendor's category
  string, and a wrong category is worse than a coarse one.
- ~~**A5**~~ — landed 2026-09-16. `run_job spec-normalize` re-keys the 9,377
  existing rows in bulk, `--dry-run` by default; see
  [the runbook](../runbooks/spec-normalize.md). Until it is APPLIED on a given
  database, a part's rows are still normalised only by its next refresh, and
  every legacy row has a NULL `provider` — which is exactly the "unclaimed"
  case the ownership rule is written for. Three decisions it added:

  - **It is not `reconcile_provider_specs`.** That function's last pass
    deletes every row an upstream payload did not mention. Here the payload IS
    the current database state, so "absent" means nothing, and the delete pass
    would be a data-loss bug rather than a correctness one. It also
    re-namespaces a secondary's catalog keys, which on a table whose rows are
    all un-namespaced would duplicate them instead of moving them.
    `services/spec_normalize_rows.py` reuses `normalise()` — the alias table,
    the junk denylist and the parser are not re-implemented — and writes the
    result under backfill rules: nothing is deleted, junk and superseded
    aliases are archived, and a row whose canonical key a `manual` row already
    answers is left exactly where it is.
  - **A canonical row is never written without a provider.** A row with
    `provider IS NULL` is claimable by whoever refreshes next
    (`provider_owns_custom_field_row`), so a backfill that left one behind
    would hand a normalised value to the first provider through the door. The
    name comes from the key namespace, else `parts.linked_provider`, else the
    workspace primary; when none of the three answers, the canonical rewrite
    is skipped for that part and counted. Junk is still retired — a customs
    code is junk whoever wrote it.
  - **The description is not mined.** `normalise()` pulls Mouser's parametric
    values out of prose and a refresh wants that; a backfill does not, because
    it would write specs with no existing row behind them and therefore
    nothing for the operator to review in the CSV.

  A row this schema has already re-keyed is invisible to `normalise()` —
  `resistance` is not one of `Resistance`'s aliases — so
  `spec_schema.canonical_value` re-parses it by canonical key instead. Without
  that the job would read its own output as unmapped free text and never be
  idempotent. Two further things that idempotency turned out to rest on, both
  found in review:

  - **The sidecar only moves when the display moves.** On a second run the
    candidate is a re-parse of the display the job itself wrote, and a display
    carries fewer significant digits than the raw vendor value: `1/3W` stores
    `333.3333 mW` with `value_num` `0.333333333333333333`, and re-parsing the
    display gives `0.3333333`. Overwriting on that difference makes every such
    row a change on every run, and `±0.00001%` — which displays as `0%` — has
    its number replaced by zero. `value_num` is therefore written only when
    the display changes or when the row has none.
  - **Two rows that strip to one payload key are collapsed.** `Resistance`
    next to `mouser:Resistance` is what a workspace that promoted a secondary
    to primary carries. Only one can hold the canonical key; the loser is
    archived like any superseded alias. Leaving it live would make it the sole
    answer on the NEXT run, which would then change a value the operator had
    already approved. Non-canonical keys are not collapsed — `Features` and
    `mouser:Features` are two providers' answers to one question.
- **A7** renders mandatory-but-missing keys on the Specs tab. It should also
  close a sharp edge this ADR widens: `isCatalogKey` classifies by key name
  alone, so a user who types `MOQ` or `Availability` as a manual spec gets a
  row that renders on the read-only Sourcing tab and can no longer be edited
  or deleted from the UI. The catalog list grew by nine ordinary commercial
  words here, which raises the odds of hitting it. Gating the classification
  on `source !== "manual"`, or refusing a catalog key in the add-spec form,
  fixes it — both are Specs-tab behaviour changes and belong with A7.
- **B1/B2** add `part_categories.value_template` and render the KiCad `Value`
  from canonical specs.

**Landed since**: A6/B3 (the `category-seed` job and the `Device:*` defaults
it carries) and B4 (`symbol-collapse`). The seed is where the canonical keys
in this ADR become per-category `value_template` and `kicad_fields` values,
which is why `backend/tests/test_category_seed.py` resolves every seeded
category path back through `category_slug_for` and refuses a placeholder the
schema does not define — a made-up key renders empty forever and fails
nothing at runtime. See [`docs/domain/categories.md`](../domain/categories.md).
Neither job re-keys existing `custom_fields` rows; that is still A5.

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
  `backend/app/domain/parts/spec_values.py`,
  `backend/app/domain/parts/spec_category_map.py`,
  `backend/app/domain/parts/services/spec_reconcile.py`,
  `backend/app/domain/categories/seed.py`,
  `backend/app/domain/categories/seed_tables.py`,
  `backend/app/domain/eda/symbol_collapse.py`,
  `backend/app/domain/parts/services/spec_normalize.py`,
  `backend/app/domain/parts/services/spec_normalize_rows.py`,
  `backend/app/domain/parts/services/spec_normalize_report.py`
- Migration: `backend/alembic/versions/0081_custom_field_provider_value_num.py`
- Tests: `backend/tests/test_spec_schema.py`,
  `backend/tests/test_spec_values.py`,
  `backend/tests/test_custom_field_provider_value_num.py`,
  `backend/tests/test_spec_reconcile.py`,
  `backend/tests/test_category_for_provider.py`,
  `backend/tests/test_category_path_resolution.py`,
  `backend/tests/test_category_seed.py`,
  `backend/tests/test_symbol_collapse.py`,
  `backend/tests/test_spec_normalize.py`
- Related: `backend/app/domain/parts/provider_fields.py`,
  `web/src/lib/providerCatalog.ts`,
  `backend/app/domain/parts/providers/mouser.py`
- Runbook: [`docs/runbooks/spec-normalize.md`](../runbooks/spec-normalize.md)
- Related: [ADR-0007](0007-provider-catalog-vs-spec-split.md),
  [ADR-0021](0021-periodic-jobs-scheduler.md),
  [ADR-0031](0031-primary-and-secondary-parts-providers.md)
