# Alembic migration notes

## Server defaults in round-trip tests

`backend/tests/test_migrations.py` snapshots reflected column defaults during
the slow migration round-trip checks. When adding a persistent `server_default`,
make sure the downgrade path restores or removes that default exactly as the
upgrade path expects; a downgrade followed by re-upgrade must produce the same
reflected default.

Do not retro-edit merged migrations to fix default drift. Add a new migration
that moves the live schema back to the intended default.

## Workspace-isolation trigger function names

For new workspace-isolation triggers, name the trigger function with the
table-first pattern:

```sql
check_<table>_workspace_<scope>()
```

Use the plural table name exactly as it appears in the schema. Pick a short
`<scope>` that describes the checked relationship set, such as `fks`,
`owner`, or `default_storage`. Keep the trigger name table-first as well, for
example `<table>_workspace_<scope>_check`.

Merged migrations may keep their existing function names. In particular,
0036, 0050, 0054, and 0055 introduced these grandfathered names:

- `check_default_storage_workspace()`
- `check_stock_entries_workspace_fks()`
- `check_part_substitutes_workspace_fks()`
- `check_part_meta_members_workspace_fks()`
- `check_part_cad_keys_workspace()`

Do not add a migration only to rename those functions. Renaming a PostgreSQL
trigger function requires replacing the function used by existing triggers and
creates avoidable migration churn; use the convention above for new migrations
instead.
