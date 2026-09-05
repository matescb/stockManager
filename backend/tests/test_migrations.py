"""Migration round-trip safety net (TEST-007 / issue #109).

`tests/conftest.py` only ever runs `command.upgrade(..., "head")`,
so a bad `downgrade()` is invisible until someone needs to roll back
in prod — i.e. when reproducing it locally is hardest. Combined with
auto-deploy + no staging (per CLAUDE.md), a botched migration with
broken downgrade is a one-way door.

This module pins the migration chain end-to-end:

  - `test_upgrade_head_then_downgrade_base_then_upgrade_head` — the
    full sweep, asserts every step exits cleanly and the final-upgrade
    schema matches the initial-upgrade schema.
  - `test_per_revision_round_trip` — for each revision, upgrade to it,
    snapshot, downgrade to its parent, then upgrade back; assert the
    snapshots match.
  - `test_downgrade_to_base_leaves_only_alembic_version` — after
    `downgrade base`, only `alembic_version` survives in the public
    schema.

The tests are slow (re-running migrations on a dedicated DB), so
they're marked `@pytest.mark.slow` and excluded by the default
`pytest` invocation. CI runs them via `pytest -m slow`.

The test uses a separate database (`stockmgr_migration_test`) so
concurrent test runs don't trample the regular suite's schema.

Per CLAUDE.md: if this test surfaces a broken `downgrade()` in a
shipped revision, the fix path is **a new migration that re-adds
whatever was incorrectly dropped**, not editing the existing file.
File new issues per migration.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Use a dedicated DB so the round-trip's drop-everything dance can't
# step on the regular fixture's schema. Override via env var if the
# operator wants to point at a specific DB (e.g. for debugging).
_DEFAULT_URL = os.environ.get(
    "MIGRATION_TEST_DATABASE_URL",
    None,
)


def _migration_db_url() -> str:
    """Return the DB URL the round-trip test should use. Derives a
    sibling DB name from `DATABASE_URL` so the test never touches the
    regular test DB by accident."""
    if _DEFAULT_URL:
        return _DEFAULT_URL
    base = os.environ.get(
        "DATABASE_URL",
        os.environ.get(
            "TEST_DATABASE_URL",
            # Fallback mirrors conftest.py default (issue #306) — host-side
            # 127.0.0.1, not the docker-network `db` hostname. In practice
            # conftest sets DATABASE_URL before this runs, so the fallback
            # is rarely hit, but it stays consistent.
            "postgresql+psycopg://stockmgr:stockmgr@127.0.0.1:5432/stockmgr_test",
        ),
    )
    head, db_name = base.rsplit("/", 1)
    # Append a suffix unique to this test module.
    return f"{head}/{db_name}_migration_rt"


def _alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@contextmanager
def _migration_database_url(url: str):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _upgrade(cfg: AlembicConfig, url: str, revision: str) -> None:
    with _migration_database_url(url):
        command.upgrade(cfg, revision)


def _downgrade(cfg: AlembicConfig, url: str, revision: str) -> None:
    with _migration_database_url(url):
        command.downgrade(cfg, revision)


def _ensure_db_exists(url: str) -> None:
    """Create the round-trip DB if it doesn't already exist."""
    eng = create_engine(url, future=True)
    try:
        with eng.connect():
            pass
    except Exception:
        head, db_name = url.rsplit("/", 1)
        admin_url = head + "/postgres"
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{db_name}"')
        admin.dispose()
    finally:
        eng.dispose()


def _reset_schema(url: str) -> None:
    eng = create_engine(url, future=True)
    try:
        with eng.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")
    finally:
        eng.dispose()


def _normalize_column_default(default: object) -> str | None:
    if default is None:
        return None
    return " ".join(str(default).split())


def _snapshot_schema(url: str) -> dict:
    """Capture a deterministic schema snapshot. Includes table names,
    column shapes including server defaults, indexes, and foreign keys
    — everything that a correct `downgrade()` followed by `upgrade()`
    should round-trip.

    Keys are sorted at every level so dict-equality compares the
    structural shape, not insertion order."""
    eng = create_engine(url, future=True)
    try:
        insp = inspect(eng)
        snap: dict = {}
        for table in sorted(insp.get_table_names()):
            cols = sorted(
                (
                    c["name"],
                    str(c["type"]),
                    bool(c.get("nullable", True)),
                    _normalize_column_default(c.get("default")),
                )
                for c in insp.get_columns(table)
            )
            indexes = sorted(
                (
                    i["name"],
                    tuple(i.get("column_names") or ()),
                    bool(i.get("unique", False)),
                )
                for i in insp.get_indexes(table)
            )
            fks = sorted(
                (
                    fk.get("name") or "",
                    fk.get("referred_table") or "",
                    tuple(fk.get("constrained_columns") or ()),
                    tuple(fk.get("referred_columns") or ()),
                )
                for fk in insp.get_foreign_keys(table)
            )
            snap[table] = {
                "columns": cols,
                "indexes": indexes,
                "foreign_keys": fks,
            }
        return snap
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def round_trip_url() -> Iterator[str]:
    url = _migration_db_url()
    _ensure_db_exists(url)
    _reset_schema(url)
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield url
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def test_workspace_member_role_check_constraint_enforced(round_trip_url: str) -> None:
    cfg = _alembic_config(round_trip_url)
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    member_id = uuid.uuid4()

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "head")

    eng = create_engine(round_trip_url, future=True)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, name, password_hash, locale, timezone, created_at) "
                    "VALUES "
                    "(:id, :email, 'Migration Tester', 'x', 'en', 'UTC', now())"
                ),
                {"id": user_id, "email": f"migration-{user_id.hex}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO workspaces "
                    "(id, name, kind, owner_user_id, currency_default, "
                    "lot_control_enabled, serial_tracking_enabled, "
                    "catalog_enabled, parts_provider, created_at) "
                    "VALUES "
                    "(:id, 'Migration Workspace', 'organization', :owner_user_id, "
                    "'USD', true, false, false, 'none', now())"
                ),
                {"id": workspace_id, "owner_user_id": user_id},
            )

        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO workspace_members "
                        "(id, workspace_id, user_id, role, status, created_at) "
                        "VALUES "
                        "(:id, :workspace_id, :user_id, 'superuser', 'active', now())"
                    ),
                    {
                        "id": member_id,
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                    },
                )
    finally:
        eng.dispose()


# ---------------------------------------------------------------------------
# The actual round-trip tests. All marked slow — excluded by default
# pytest invocation; run with `pytest -m slow`.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_upgrade_head_then_downgrade_base_then_upgrade_head(
    round_trip_url: str,
) -> None:
    """Full chain sweep. The most basic safety net: every revision in
    the chain must apply and reverse without raising, and the final
    schema must match the schema after a clean `upgrade head`."""
    cfg = _alembic_config(round_trip_url)

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "head")
    initial_snap = _snapshot_schema(round_trip_url)

    _downgrade(cfg, round_trip_url, "base")
    _upgrade(cfg, round_trip_url, "head")
    final_snap = _snapshot_schema(round_trip_url)

    assert initial_snap == final_snap, (
        "schema after downgrade-base + upgrade-head differs from "
        "initial upgrade-head; some downgrade() leaked state. "
        "Tables that diverged: "
        f"{sorted(set(initial_snap) ^ set(final_snap))}"
    )


@pytest.mark.slow
def test_downgrade_to_base_leaves_only_alembic_version(
    round_trip_url: str,
) -> None:
    """After `downgrade base`, the public schema should be empty
    except for `alembic_version` (alembic's own tracking table). If
    anything else remains, a `downgrade()` is leaking tables."""
    cfg = _alembic_config(round_trip_url)

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "head")
    _downgrade(cfg, round_trip_url, "base")

    eng = create_engine(round_trip_url, future=True)
    try:
        insp = inspect(eng)
        remaining = sorted(insp.get_table_names())
    finally:
        eng.dispose()

    # alembic_version may or may not survive depending on alembic's
    # version; some flavours drop it on the final downgrade. Any other
    # table is a leak.
    leftover = [t for t in remaining if t != "alembic_version"]
    assert leftover == [], (
        f"downgrade base left tables behind: {leftover}. Some "
        "migration's downgrade() is incomplete."
    )


@pytest.mark.slow
def test_invitation_token_hash_drop_round_trips(round_trip_url: str) -> None:
    """0049 drops the legacy SHA-256 invitation token_hash column."""
    cfg = _alembic_config(round_trip_url)

    def invitation_columns() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            insp = inspect(eng)
            return {col["name"] for col in insp.get_columns("workspace_invitations")}
        finally:
            eng.dispose()

    def invitation_unique_constraints() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            insp = inspect(eng)
            return {
                constraint["name"]
                for constraint in insp.get_unique_constraints("workspace_invitations")
            }
        finally:
            eng.dispose()

    _reset_schema(round_trip_url)
    command.upgrade(cfg, "0048")
    assert "token_hash" in invitation_columns()
    assert "uq_workspace_invitation_token_hash" in invitation_unique_constraints()

    command.upgrade(cfg, "0049")
    assert "token_hash" not in invitation_columns()
    assert "uq_workspace_invitation_token_hash" not in invitation_unique_constraints()

    command.downgrade(cfg, "0048")
    assert "token_hash" in invitation_columns()
    assert "uq_workspace_invitation_token_hash" in invitation_unique_constraints()

    command.upgrade(cfg, "0049")
    assert "token_hash" not in invitation_columns()
    assert "uq_workspace_invitation_token_hash" not in invitation_unique_constraints()


@pytest.mark.slow
def test_password_reset_requests_autovacuum_reloptions_round_trip(
    round_trip_url: str,
) -> None:
    cfg = _alembic_config(round_trip_url)
    expected = {
        "autovacuum_vacuum_scale_factor=0.05",
        "autovacuum_analyze_scale_factor=0.05",
        "autovacuum_vacuum_threshold=1000",
    }

    def password_reset_reloptions() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            with eng.connect() as conn:
                return {
                    row.reloption
                    for row in conn.execute(
                        text(
                            "SELECT unnest(coalesce(reloptions, ARRAY[]::text[])) "
                            "AS reloption "
                            "FROM pg_class "
                            "WHERE oid = 'password_reset_requests'::regclass"
                        )
                    )
                }
        finally:
            eng.dispose()

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "0063")
    assert expected <= password_reset_reloptions()

    _downgrade(cfg, round_trip_url, "0062")
    assert expected.isdisjoint(password_reset_reloptions())


@pytest.mark.slow
def test_build_stages_round_trip(round_trip_url: str) -> None:
    """0076 adds two tables, one ledger column and three workspace triggers.

    The column on `stock_entries` is the risky half: a downgrade that drops
    the tables but leaves `stock_entries.build_stage_id` (or its FK) behind
    would wedge the chain on the next upgrade. Assert both directions
    explicitly rather than relying on the whole-chain sweep to notice.
    """
    cfg = _alembic_config(round_trip_url)

    def tables() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            return set(inspect(eng).get_table_names())
        finally:
            eng.dispose()

    def stock_entry_columns() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            return {col["name"] for col in inspect(eng).get_columns("stock_entries")}
        finally:
            eng.dispose()

    def triggers() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            with eng.connect() as conn:
                return {
                    row.tgname
                    for row in conn.execute(
                        text(
                            "SELECT tgname FROM pg_trigger "
                            "WHERE NOT tgisinternal AND tgname IN ("
                            "'build_stages_workspace_fk_check', "
                            "'build_stage_lines_workspace_fk_check', "
                            "'stock_entries_build_stage_workspace_check')"
                        )
                    )
                }
        finally:
            eng.dispose()

    expected_triggers = {
        "build_stages_workspace_fk_check",
        "build_stage_lines_workspace_fk_check",
        "stock_entries_build_stage_workspace_check",
    }

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "0075")
    assert "build_stages" not in tables()
    assert "build_stage_id" not in stock_entry_columns()

    _upgrade(cfg, round_trip_url, "0076")
    after_upgrade = _snapshot_schema(round_trip_url)
    assert {"build_stages", "build_stage_lines"} <= tables()
    assert "build_stage_id" in stock_entry_columns()
    assert triggers() == expected_triggers

    _downgrade(cfg, round_trip_url, "0075")
    assert "build_stages" not in tables()
    assert "build_stage_lines" not in tables()
    assert "build_stage_id" not in stock_entry_columns()
    assert triggers() == set()

    _upgrade(cfg, round_trip_url, "0076")
    assert _snapshot_schema(round_trip_url) == after_upgrade
    assert triggers() == expected_triggers


@pytest.mark.slow
def test_unit_triggers_round_trip(round_trip_url: str) -> None:
    """0077 adds three trigger functions and their triggers, and no columns.

    `_snapshot_schema` only inspects tables, columns, indexes and foreign
    keys — it cannot see a trigger or a function at all, so the whole-chain
    sweep would happily pass on a `downgrade()` that dropped the triggers
    and orphaned the `check_*` functions (or vice versa). A leaked function
    then wedges the next `upgrade`'s `CREATE OR REPLACE`... silently, by
    keeping the *old* body. Assert both objects in both directions
    explicitly, the way `test_build_stages_round_trip` does for 0076.
    """
    cfg = _alembic_config(round_trip_url)

    expected_triggers = {
        "stock_entries_unit_match_check",
        "stock_entries_unit_immutable_check",
        "parts_unit_of_measure_change_check",
    }
    expected_functions = {
        "check_stock_entry_unit_matches_part",
        "check_stock_entry_unit_immutable",
        "check_part_unit_of_measure_change",
    }

    def triggers() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            with eng.connect() as conn:
                return {
                    row.tgname
                    for row in conn.execute(
                        text(
                            "SELECT tgname FROM pg_trigger "
                            "WHERE NOT tgisinternal AND tgname = ANY(:names)"
                        ),
                        {"names": sorted(expected_triggers)},
                    )
                }
        finally:
            eng.dispose()

    def functions() -> set[str]:
        eng = create_engine(round_trip_url, future=True)
        try:
            with eng.connect() as conn:
                return {
                    row.proname
                    for row in conn.execute(
                        text("SELECT proname FROM pg_proc WHERE proname = ANY(:names)"),
                        {"names": sorted(expected_functions)},
                    )
                }
        finally:
            eng.dispose()

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "0076")
    assert triggers() == set()
    assert functions() == set()

    _upgrade(cfg, round_trip_url, "0077")
    after_upgrade = _snapshot_schema(round_trip_url)
    assert triggers() == expected_triggers
    assert functions() == expected_functions

    _downgrade(cfg, round_trip_url, "0076")
    assert triggers() == set()
    assert functions() == set(), (
        "0077 downgrade dropped the triggers but leaked their functions"
    )

    _upgrade(cfg, round_trip_url, "0077")
    assert _snapshot_schema(round_trip_url) == after_upgrade
    assert triggers() == expected_triggers
    assert functions() == expected_functions


@pytest.mark.slow
def test_unit_match_trigger_is_live_after_upgrade_head(round_trip_url: str) -> None:
    """The trigger has to actually fire on a freshly migrated database.

    `test_unit_triggers_round_trip` proves the objects exist;  this proves
    they are wired to `stock_entries` and reject a mismatch, on a schema
    built by the migration chain rather than by the test fixtures.
    """
    cfg = _alembic_config(round_trip_url)
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    part_id = uuid.uuid4()

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "head")

    eng = create_engine(round_trip_url, future=True)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, name, password_hash, locale, timezone, created_at) "
                    "VALUES (:id, :email, 'Unit Tester', 'x', 'en', 'UTC', now())"
                ),
                {"id": user_id, "email": f"unit-{user_id.hex}@example.com"},
            )
            conn.execute(
                text(
                    "INSERT INTO workspaces "
                    "(id, name, kind, owner_user_id, currency_default, "
                    "lot_control_enabled, serial_tracking_enabled, "
                    "catalog_enabled, parts_provider, created_at) "
                    "VALUES (:id, 'Unit Workspace', 'organization', :owner_user_id, "
                    "'USD', true, false, false, 'none', now())"
                ),
                {"id": workspace_id, "owner_user_id": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO parts "
                    "(id, workspace_id, name, part_type, attrition_percentage, "
                    "attrition_min_quantity, default_storage_mandatory, "
                    "serialized, published, description_locally_edited, "
                    "created_at, updated_at) "
                    "VALUES (:id, :workspace_id, 'Wire', 'local', 0, 0, "
                    "false, false, false, false, now(), now())"
                ),
                {"id": part_id, "workspace_id": workspace_id},
            )

        def insert_entry(unit: str) -> None:
            with eng.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO stock_entries "
                        "(id, workspace_id, part_id, quantity_delta, unit, status, "
                        "operation_type, occurred_at, created_at) "
                        "VALUES (:id, :workspace_id, :part_id, 1, :unit, 'on_hand', "
                        "'add', now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "workspace_id": workspace_id,
                        "part_id": part_id,
                        "unit": unit,
                    },
                )

        # The part defaults to 'pcs', so this agrees and lands.
        insert_entry("pcs")

        with pytest.raises(IntegrityError) as excinfo:
            insert_entry("m")
        assert "does not match parts.unit_of_measure" in str(excinfo.value)

        # ...and now that the part has a ledger row, its unit is frozen.
        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(
                    text("UPDATE parts SET unit_of_measure = 'm' WHERE id = :id"),
                    {"id": part_id},
                )
    finally:
        eng.dispose()


@pytest.mark.slow
def test_snapshot_schema_captures_server_default(round_trip_url: str) -> None:
    _reset_schema(round_trip_url)
    eng = create_engine(round_trip_url, future=True)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE default_snapshot_probe ("
                    "id integer PRIMARY KEY, "
                    "status text NOT NULL DEFAULT 'pending'"
                    ")"
                )
            )

        before = _snapshot_schema(round_trip_url)

        with eng.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE default_snapshot_probe "
                    "ALTER COLUMN status SET DEFAULT 'ready'"
                )
            )

        after = _snapshot_schema(round_trip_url)
    finally:
        eng.dispose()

    assert before != after
    assert before["default_snapshot_probe"]["columns"] != after[
        "default_snapshot_probe"
    ]["columns"]
    before_status_default = next(
        column[3]
        for column in before["default_snapshot_probe"]["columns"]
        if column[0] == "status"
    )
    after_status_default = next(
        column[3]
        for column in after["default_snapshot_probe"]["columns"]
        if column[0] == "status"
    )
    assert before_status_default != after_status_default
    assert before_status_default is not None
    assert "pending" in before_status_default


@pytest.mark.slow
def test_per_revision_round_trip(round_trip_url: str) -> None:
    """For each revision, upgrade to it, snapshot, downgrade to its
    parent, then upgrade back to it. Assert the snapshots match. This
    isolates which revision's `downgrade()` leaks if anything fails."""
    cfg = _alembic_config(round_trip_url)
    script = ScriptDirectory.from_config(cfg)

    # Walk in dependency order — newest-to-oldest from `walk_revisions`.
    revisions = list(reversed(list(script.walk_revisions())))

    _reset_schema(round_trip_url)
    _upgrade(cfg, round_trip_url, "base")  # establishes alembic_version table

    for rev in revisions:
        # Upgrade to this revision.
        _upgrade(cfg, round_trip_url, rev.revision)
        before = _snapshot_schema(round_trip_url)

        # Downgrade to parent (or base if this is the first rev).
        target = rev.down_revision or "base"
        # Skip if the down_revision is a tuple (merge revision); this
        # repo's chain is linear so it shouldn't happen, but pin it.
        if isinstance(target, tuple):
            pytest.skip(f"merge revision {rev.revision} not supported")
        _downgrade(cfg, round_trip_url, target)

        # Re-upgrade and snapshot again.
        _upgrade(cfg, round_trip_url, rev.revision)
        after = _snapshot_schema(round_trip_url)

        assert before == after, (
            f"revision {rev.revision} ({rev.doc!r}) round-trip diverged. "
            f"Tables changed: {sorted(set(before) ^ set(after))}"
        )
