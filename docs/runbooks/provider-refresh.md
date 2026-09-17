# provider-refresh sweep runbook

Audience: engineer

## When

To re-ask the providers about parts that were imported before the
importer knew what it knows now. Every part in the catalogue was imported
once and then left alone, while the import path grew a category resolver
and a spec schema ([ADR-0034](../adr/0034-spec-schema.md)), a local
datasheet store ([ADR-0033](../adr/0033-datasheet-fetch-drops-host-allow-list.md)) and a
second provider tier ([ADR-0031](../adr/0031-primary-and-secondary-parts-providers.md)).
The job re-runs the MPN lookup for every active, linked part with an MPN
and writes back whatever the providers answer now.

Good reasons to run it:

- a batch of parts carries no category, no canonical specs, or no
  datasheet, because it was imported early;
- the spec schema has grown aliases and you want the vendors' current
  payloads read through them;
- a workspace has just gained a second provider's credentials and you
  want its parts linked to it (`--link-missing-providers`);
- parts were typed in or imported from a BOM and no provider has ever
  been asked about them (`--include-unlinked` — see
  [Linking local parts](#linking-local-parts)).

Not an incident procedure. It rewrites part columns and spec rows on a
system with no staging environment, and it spends a metered external
allowance, so it is a planned change with a review step.

## Severity

Routine. It becomes SEV-3 only if an `--apply` produced something
unexpected, in which case go to [Rollback](#rollback).

## TTR

30-60 minutes including the review and the dump. The job itself paces
itself at 750 ms between provider calls: prod's 290 links are about four
minutes, and `--link-missing-providers` roughly doubles that.

## Pre-flight

- **Know the quota.** DigiKey and Mouser free tiers sit near 1,000 calls
  a day, shared with every lookup the app itself makes. A dry run and an
  apply are two full passes and neither makes the other cheaper — budget
  both. `--limit N` runs the first N parts and is the normal way to
  start.
- **Nobody should be mid-import.** The job takes a session-level advisory
  lock against itself, but a provider refresh from the UI landing while
  it runs simply wins the keys it touches, which makes the CSV you
  reviewed stale for that part.
- Know what it will **not** touch: `manual` and `override` custom fields,
  a category a user already chose, a part with no MPN, an archived part,
  and — unless you pass `--include-unlinked` — a part nothing has ever
  linked to a provider. Rows `spec-normalize` archived stay archived.
- Know what it **will** rewrite: on the PRIMARY tier, `manufacturer`,
  `mpn`, `footprint` and (unless `description_locally_edited`)
  `description`, plus `linked_provider` / `linked_external_id` and
  `part_type`. A SECONDARY writes no part column at all.

## Steps

1. **Dry run.** The default, so a missing flag cannot write anything. The
   lookups are still real — that is what makes the CSV a plan rather than
   a guess — but nothing is written and no image or datasheet is
   downloaded.

   It does **not** make the apply cheaper. `provider_cache` lives in the
   process that ran, and the apply is a separate `exec`, so the two
   passes cost their calls independently. Budget both.

   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec backend python -m app.cli.run_job provider-refresh \
       --dry-run --workspace <uuid> --report /tmp/provider-refresh.csv
   ```

2. **Copy the CSV out** of the container and read it. The file is written
   `0600` and any directory the job creates for it `0700` — it names
   every part, provider and category in the workspace, and `/tmp` in the
   container is world-readable.

   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec -T backend cat /tmp/provider-refresh.csv > ~/provider-refresh.csv
   ```

   One line per (part, provider) pair. Columns: `workspace_id, part_id,
   mpn, provider, tier, action, part_columns_changed, specs_added,
   specs_updated, specs_restored, specs_removed, category_before,
   category_after, assets_fetched, assets_would_fetch, error`. The
   actions are

   | Action | What it means |
   |---|---|
   | `refreshed` | The provider answered and its payload was reconciled onto a part it was already linked to. |
   | `linked` | The same, on a provider that had no claim on the part at all. Only `--link-missing-providers` (a secondary joining a linked part) and `--include-unlinked` (any tier claiming a part nothing owned) produce these, and only on an exact-MPN hit. |
   | `miss` | The provider has never heard of this MPN, or answered with a different one. Nothing written, no link created. Not a failure — and the second case is common, because the sweep requires an EXACT MPN match (see below). |
   | `error` | The lookup raised, the provider reported it is out of quota, or the database rejected the write. The `error` column says which; a rejected write names the constraint, and `uq_parts_ws_mpn` means another part in the workspace already holds the MPN the payload spelled. |
   | `skipped` | There was nothing to ask: either the part is linked to a provider this workspace has no usable credentials for, or (under `--include-unlinked`) the part has no MPN. The no-MPN case is one line for the part with `provider` and `tier` blank — nobody was asked. |

   Exactly one of `assets_fetched` and `assets_would_fetch` is populated
   per row: a dry run downloads nothing and names what an apply would
   pull; an apply names what it stored. A secondary fills neither — the
   primary owns the part's files (ADR-0031).

   `specs_removed` counts both ways a row leaves the Specs tab: a key the
   provider stopped sending, hard-deleted, and a customs code or `-`
   placeholder retired with `archived_at`. The `part.specs_reconciled`
   audit row for that part carries the split if you need it.

   `part_columns_changed` lists column NAMES, not values — the values are
   on the part and in the `audit_log`. `tier` says which set of rules
   applied: `primary` owns the part columns and downloads assets,
   `secondary` writes canonical specs and its own `"{provider}:"`-prefixed
   catalog keys and not one column.

   Two summary sections follow the changes, each after a blank line:
   counts per action per provider per workspace, and the 30 most frequent
   raw keys the schema had no canonical home for. That second list is the
   input for extending `backend/app/domain/parts/spec_schema_tables.py`
   — it means the same thing, and is ranked the same way, as the one
   `spec-normalize` writes.

3. **Take a `pg_dump`** — see [backup-restore](backup-restore.md). This
   rewrites part columns from a remote payload and there is no staging
   environment and no bulk undo.

4. **Apply**, with the same scope you reviewed. `--report` is required
   here, not optional: the values it overwrites survive nowhere else.

   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec backend python -m app.cli.run_job provider-refresh \
       --apply --workspace <uuid> --report /tmp/provider-refresh-applied.csv
   ```

### Flags

| Flag | What it does |
|---|---|
| `--limit N` | Stop after N parts, across the whole run. Start here. |
| `--only-uncategorized` | Restrict the sweep to parts with no category — the cheapest way to file them without spending calls on parts that are already filed. |
| `--link-missing-providers` | Also ask every SECONDARY provider the workspace has credentials for that the part is not linked to, and link it on an exact-MPN hit. One extra call per part per provider. It does NOT widen the set of parts (a part nothing has ever linked stays out of scope) and it never adds the workspace's PRIMARY — see below. |
| `--include-unlinked` | Also visit active parts that no provider has ever been linked to, asking the PRIMARY first and then every secondary with credentials. On a hit the provider links the part; the primary may claim it and fills only what the part left empty. See [Linking local parts](#linking-local-parts). One call per part per provider. |
| `--sleep-ms MS` | Milliseconds between provider calls, default 750. `0` turns the throttle off; only do that against a provider you know has headroom. |

## Two rules worth knowing before you run it

**Only an exact MPN counts.** DigiKey falls back to a keyword search when
its exact-match endpoint misses, and Mouser matches partially, so a "hit"
is not necessarily this part. A human refreshing one part by name reads
the answer and catches that; a sweep across several hundred pairs does
not. So the job requires the provider to answer with the same MPN it was
asked, case-insensitively, and records anything else as `miss`. A part
whose MPN is stored in a different format than the vendor prints it
(`98266-0897` vs `0982660897`) will therefore show up as a `miss` line —
that is a row to fix by hand, not a bug.

**`--link-missing-providers` adds SECONDARIES only.** It never promotes
a provider to a part's primary. Doing so would run the primary path on a
part it has never owned and rewrite `manufacturer`, `mpn`, `footprint`,
`description`, `linked_provider` and `part_type` from a provider nobody
chose for that part — on our catalogue, where Mouser is primary and most
links are DigiKey secondaries, that would be most of the parts. It is
also not reversible the way [Rollback](#rollback) describes, because the
unlink route refuses the primary. Promoting a provider onto a part is a
per-part decision: use the Refresh button, or `POST
/api/parts/{id}/refresh-from-provider`.

## Linking local parts

`--include-unlinked` is the flag for parts that were typed in or imported
from a BOM and that no provider has ever been asked about. Without it
those parts are out of scope entirely: the default sweep only re-asks
providers a part is already known to. On our catalogue 34 unlinked parts
carry an MPN and one does not.

Each unlinked part is asked of the workspace **primary first, then every
secondary with credentials** — all of them, not just until one answers —
exact-MPN only, and every provider that answers links the part. So a
part both providers stock ends up with two `linked` lines and two link
rows, the primary having claimed the columns and the secondary having
written its own namespace. The cost is one call per part per provider,
so budget it the same way as `--link-missing-providers`.

**The primary may claim an unlinked part.** This is the one place it
runs on a part it has never owned, and the exception is narrow on
purpose: a part with no link row and no `linked_provider` has no primary
to displace and no provider-written column to overwrite. Everywhere else
— including `--link-missing-providers` — promoting a provider onto a part
stays a per-part human decision, for the reasons under
[Two rules worth knowing](#two-rules-worth-knowing-before-you-run-it).

**What the claim writes.** `linked_provider`, `linked_external_id`,
`last_refresh_at` and the `part_provider_links` row — those ARE the
claim. `part_type` flips `local` → `linked`, because it is derived from
`linked_provider`. A NULL `category_id` is filled if the vendor's
category resolves to a category the workspace already has, and the
canonical specs, the image and the datasheet arrive as on any primary
refresh.

**What the claim never overwrites.** `manufacturer`, `footprint` and
`description` are filled only where the part is *silent* — the column is
empty, or holds nothing but the part's own MPN, which is what a
scan-created part carries. A description somebody typed survives, and so
does one on a part with `description_locally_edited`. That is deliberately
different from a refresh of a part the primary already owns, where the
vendor drives those columns: there the vendor is the source of record,
here whoever typed them never asked a vendor to replace their words.

**The primary gets one chance per part, so read the misses.** The flag
offers the primary only while NOTHING is linked to the part. If the
primary misses tonight and a secondary hits, the part is no longer
unlinked, and a later `--include-unlinked` run will never offer it the
primary again — it will only refresh the secondary. That follows from
the rule above and is the right rule, but it means a `miss` line on the
primary tier for an unlinked part is a line worth acting on while it is
in front of you: usually the MPN is stored in a format the vendor does
not print (see [Two rules worth
knowing](#two-rules-worth-knowing-before-you-run-it)). The recovery is
the per-part Refresh button, or `POST
/api/parts/{id}/refresh-from-provider`, which is the same per-part human
decision promoting a provider always was — not a re-run of the sweep.

**A part with no MPN** is reported as `skipped` with
`part has no MPN to look up`, one line, no provider asked. It is in the
report rather than absent from it because supplying the MPN is the
operator's next action, and a run whose whole purpose was to find local
parts should not leave one out silently.

A `linked` line from this pass is reversed the same way as any other —
see [Rollback](#rollback). For a primary claim that is `PATCH
/api/parts/{id}` with `unlink_provider=true`, not the unlink route, which
refuses the primary by design.

## Concurrency

The job takes a session-level advisory lock. A second sweep started while
one is running exits **2** with `another provider-refresh is already
running` and touches nothing — including the `--report` file, which the
running sweep may still be writing. `run_job`'s own transaction-scoped
lock does not cover this: it is dropped at the first per-batch commit.

## One bad row does not lose the batch

Each (part, provider) pair writes inside its own SAVEPOINT, nested in the
batch's. A statement Postgres rejects aborts the transaction it runs in,
so without that a single bad row would take every part in its 25-part
batch with it and the sweep would commit nothing for them. Instead the
pair gets an `error` line naming the constraint, the savepoint is rolled
back, and the run carries on — it is not a halt, because the provider is
answering fine.

The realistic cause is `uq_parts_ws_mpn`. Two parts whose MPNs differ
only by case or padding are legal, because that index is exact; they stop
being legal the moment a refresh rewrites one of them to the vendor's
spelling. Fix it by hand — merge the duplicates, or correct the MPN on
the part that should not have it — and re-run.

## Quota, and exit 3

When a provider reports it is rate-limited or out of quota, the sweep
**stops**. It finishes the CSV, commits everything it completed, and
exits **3** with a message naming the provider. That is neither success
(0) nor a usage error (2), so a wrapper script can tell "stopped, re-run
later" from both.

DigiKey reports a 429 as a clean `"found": false` with `"DigiKey rate
limit reached"` as the message rather than as an error, so the job
matches the message as well as the status code. A part whose row reads
`error` with a rate-limit message is the last line of the run.

Re-running after a stop is safe and is the intended recovery: the parts
already done are no-ops on unchanged upstream data, and `--limit` lets
you walk the rest of the catalogue over several days.

## Verify

- Re-run the dry run over the same scope. A clean apply leaves nothing to
  rewrite, so the new CSV's `part_columns_changed` cells are empty and
  its `specs_added` / `specs_updated` columns are `0`. The rows
  themselves still appear — the job re-asked, it just had nothing to
  change — and `last_refresh_at` still moves. That is the record that
  the run happened, not a change.
- Open a part that appeared in the CSV with a `category_after`. It is
  filed, and its Specs tab shows canonical keys.
- One `audit_log` row per workspace with `action =
  part.providers_swept`, carrying counts and provider names, alongside
  the per-part `part.specs_reconciled` rows the refresh itself writes.

```bash
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec db psql -U stockmgr stockmgr -c \
    "SELECT workspace_id, comment FROM audit_log \
     WHERE action = 'part.providers_swept' ORDER BY created_at DESC LIMIT 10"
```

## Rollback

**Restore from the `pg_dump` taken in step 3**, using
[backup-restore](backup-restore.md). Unlike `spec-normalize`, this job
has no line-by-line reversal: the CSV records which columns changed but
not what they held, because a row carrying every overwritten
`description` would be a file nobody can read and a second copy of the
catalogue on the operator's laptop. The dump is the record.

Two things are recoverable without the dump, and only two:

- **A `linked` line** is reversed with `DELETE /api/parts/{part_id}/provider-links/{provider}`,
  which also removes that provider's namespaced fields and demotes its
  `override` rows to `manual`. It refuses the workspace's primary by
  design; a primary link is released with `PATCH /api/parts/{id}` and
  `unlink_provider=true`.
- **A `category_after` with an empty `category_before`** is reversed by
  clearing `parts.category_id` on that part.

If the run went wrong for more than a handful of parts — a provider
returning bad data, a mis-scoped `--workspace` — restore the dump. That
is the correct answer, not a per-part repair.

## Post-mortem

Record the scope, the flags, the counts, and the raw keys you added to
the alias table as a result. If the run surfaced a provider payload the
schema read wrongly, that mapping belongs in a test in
`backend/tests/test_spec_schema.py` before the alias is changed.
