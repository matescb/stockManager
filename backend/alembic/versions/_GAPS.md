# Alembic Revision Gaps

Audience: engineer

Known skipped revision IDs in `backend/alembic/versions/`.

The Alembic chain is linear, but the numeric IDs are not fully
contiguous. Do not renumber merged migrations to fill these gaps;
merged migration IDs may already exist in deployed databases.

| Missing ID | Why it is absent | Chain evidence |
|---|---|---|
| `0024` | `audit_log` was renumbered from `0024` to `0030` after a rebase. | `backend/alembic/versions/0030_audit_log.py:11-21` |
| `0026` | Skipped during the same migration rebase sequence; no merged migration claims this revision ID. | `backend/alembic/versions/0025_catalog_token_hash.py:35-36`, `backend/alembic/versions/0028_login_lockout.py:22-23` |
| `0027` | Skipped during the same migration rebase sequence; no merged migration claims this revision ID. | `backend/alembic/versions/0025_catalog_token_hash.py:35-36`, `backend/alembic/versions/0028_login_lockout.py:22-23` |

Current chain segment:

```text
0023 -> 0025 -> 0028 -> 0029 -> 0030
```

When adding a new migration, continue from the current head. See the
[migration workflow](../../../docs/development.md#migrations).
