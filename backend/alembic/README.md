# Alembic migration notes

## Server defaults in round-trip tests

`backend/tests/test_migrations.py` snapshots reflected column defaults during
the slow migration round-trip checks. When adding a persistent `server_default`,
make sure the downgrade path restores or removes that default exactly as the
upgrade path expects; a downgrade followed by re-upgrade must produce the same
reflected default.

Do not retro-edit merged migrations to fix default drift. Add a new migration
that moves the live schema back to the intended default.
