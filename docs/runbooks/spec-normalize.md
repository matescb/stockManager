# spec-normalize backfill runbook

Audience: engineer

## When

Once, to bring the provider `custom_fields` rows that pre-date the spec schema
onto it — and again after any bulk import that lands rows the schema has since
grown aliases for. The job re-keys existing rows, archives customs codes and
`-` placeholders, fills `custom_fields.provider` and `value_num`, and files
uncategorized parts from their provider's taxonomy. It talks to no provider
API. Background: [ADR-0034](../adr/0034-spec-schema.md).

Not an incident procedure. It rewrites a table on a system with no staging
environment, so it is a planned change with a review step.

## Severity

Routine. It becomes SEV-3 only if an `--apply` produced something unexpected,
in which case go to [Rollback](#rollback).

## TTR

20-40 minutes including the review and the dump. The job itself runs in
seconds at prod's scale (324 parts, 9,377 rows).

## Pre-flight

- Confirm nobody is mid-import: the job takes a session-level advisory lock
  against itself, but a provider refresh landing while it runs simply wins the
  key it touches, which makes the CSV you reviewed stale for that part.
- Know that the job **never deletes**. Junk keys and placeholder values get
  `archived_at`, so everything it retires can be read back and counted.
- Know what it will not touch: `manual` and `override` rows, a category a user
  already chose, and a canonical key on a part where no provider can be named
  (no `linked_provider` and no workspace `parts_provider`).

## Steps

1. **Dry run.** The default, so a missing flag cannot write anything.

   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec backend python -m app.cli.run_job spec-normalize \
       --dry-run --report /tmp/spec-normalize.csv
   ```

2. **Copy the CSV out** of the container and read it. The file is written
   `0600` and any directory the job creates for it `0700` — it names every
   part, key and value in the workspace, and `/tmp` in the container is
   world-readable.

   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec -T backend cat /tmp/spec-normalize.csv > ~/spec-normalize.csv
   ```

   Columns: `workspace_id, part_id, mpn, action, key, old_key, provider,
   old_value, new_value, category_path`. The actions are

   | Action | What it means |
   |---|---|
   | `rekey` | The row now carries its canonical key and the parsed value. `old_key == key` means only the value moved. |
   | `archive` | Retired: a customs code, or an alias superseded by the spelling that won the key. |
   | `drop` | Retired because the value was a placeholder (`-`). Same effect as `archive`; the column records the reason. |
   | `stamp` | A canonical row that had the right key and value but no `provider`. It may have filled an empty `value_num` in the same pass. |
   | `value_num` | A canonical row that had its `provider` already and only the numeric sidecar missing. |
   | `category` | A part with no category, filed from its provider's taxonomy. `category_path` names where. |
   | `add` | A NEW row. Only the schema's one-to-many alias produces these: `Size / Dimension` answers both `length` and `width`, and the part has one row for it, so the first key renames that row and the second gets a copy. `old_key` names the upstream key both were read from. |

   Two summary sections follow the changes, each after a blank line: counts
   per action per workspace, and the 30 most frequent raw keys the schema had
   no canonical home for. That second list is the input for extending
   `backend/app/domain/parts/spec_schema_tables.py` — adding an alias there
   and re-running the dry run is cheap.

3. **Take a `pg_dump`** — see [backup-restore](backup-restore.md). There is no
   staging environment and no bulk undo.

4. **Apply**, with the same `--workspace` scope you reviewed. `--report` is
   required here, not optional: the values it overwrites survive nowhere else.

   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec backend python -m app.cli.run_job spec-normalize \
       --apply --report /tmp/spec-normalize-applied.csv
   ```

   To go workspace by workspace instead, add `--workspace <uuid>` to both the
   dry run and the apply.

## Verify

- Re-run the dry run. A clean apply leaves nothing to do, so the change
  section of the new CSV is empty and the job logs `changes=0`.
- Open a part that appeared in the CSV. The Specs tab shows `resistance`
  rather than `Resistance`, no customs codes, and no `-` rows.
- One `audit_log` row per workspace with `action = part.specs_normalized`,
  carrying counts and key names. Values are never in it; they are in the CSV,
  which stays on your machine.

```bash
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec db psql -U stockmgr stockmgr -c \
    "SELECT workspace_id, comment FROM audit_log \
     WHERE action = 'part.specs_normalized' ORDER BY created_at DESC LIMIT 10"
```

## Rollback

Nothing was deleted, so most of a run can be undone from the CSV without
touching the dump. Every line describes exactly one row. A line is
identified by `part_id` + `old_key`, except an `add`, which is the one line
whose row did not exist before the run and is therefore found by `part_id`
+ `key`:

- An `archive` or `drop` line is reversed by clearing `archived_at` on that
  part's row for that `key`.
- A `rekey` line is reversed by setting `key` back to `old_key` and `value`
  back to `old_value` (and `value_num` to NULL). When `old_key` equals `key`
  only the value moved, so only the value needs restoring.
- A `stamp` or `value_num` line is reversed by clearing `provider` and
  `value_num` on that row. Both actions only ever FILL those two columns, and
  only on a row whose displayed value did not change, so clearing both is the
  right reversal for either line.
- A `category` line is reversed by clearing `parts.category_id`.
- An `add` line is the one line reversed by a DELETE, of that part's row for
  that `key`. It is safe because the row is new — nothing else has ever
  pointed at it — but it is a delete, so check `part_id` + `key` before you
  run it. Reverse it together with the `rekey` line above it that carries
  the same `old_key`: the two describe one upstream value split across two
  rows, and undoing half leaves the part with a `width` and no `length`.

Two rows answering one canonical key produce two lines — a `rekey` on the row
that kept the key and an `archive` on the one that was retired. Reverse both
or neither: restoring only the `archive` leaves two live rows for one key
again.

If the run is wrong in a way the CSV does not describe — a bug rather than a
bad alias — restore from the `pg_dump` taken in step 3 using
[backup-restore](backup-restore.md). A restore is the correct answer whenever
more than a handful of parts are involved.

## Post-mortem

Record the `--workspace` scope, the change counts, and the raw keys you added
to the alias table as a result. If the run was re-done because the first CSV
showed a wrong mapping, that mapping belongs in a test in
`backend/tests/test_spec_schema.py` before the alias is changed.
