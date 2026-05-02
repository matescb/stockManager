"""Schema invariants from alembic 0018 (DB-001/003/004 + BE2-018).

These tests pin the migration's effects against the actual database so
drift between the model declaration and the migration surfaces in CI.
The conftest runs `alembic upgrade head` against a fresh schema before
every test, so these checks reflect the post-0018 world.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


# ---------------------------------------------------------------------------
# DB-001 / BE2-002 — FKs on cross-table refs
# ---------------------------------------------------------------------------


_FK_EXPECTATIONS = [
    # (table, column, referenced_table, expected_fk_name)
    ("stock_entries", "order_id", "orders", "fk_stock_entries_order_id"),
    ("stock_entries", "order_entry_id", "order_entries", "fk_stock_entries_order_entry_id"),
    ("stock_entries", "build_id", "builds", "fk_stock_entries_build_id"),
    ("lots", "source_order_id", "orders", "fk_lots_source_order_id"),
    ("lots", "source_build_id", "builds", "fk_lots_source_build_id"),
]


@pytest.mark.parametrize("table,column,ref_table,fk_name", _FK_EXPECTATIONS)
def test_cross_table_ref_has_fk(db, table, column, ref_table, fk_name):
    """Every previously bare-UUID cross-table ref now has an FK with the
    expected name and `ON DELETE SET NULL`."""
    row = db.execute(
        text(
            """
            SELECT con.conname,
                   con.confdeltype,
                   target.relname AS ref_table
            FROM pg_constraint con
            JOIN pg_class src ON src.oid = con.conrelid
            JOIN pg_class target ON target.oid = con.confrelid
            JOIN pg_attribute a
                ON a.attrelid = con.conrelid
               AND a.attnum = ANY(con.conkey)
            WHERE con.contype = 'f'
              AND src.relname = :table
              AND a.attname = :column
            """
        ),
        {"table": table, "column": column},
    ).first()
    assert row is not None, f"no FK on {table}.{column}"
    name, deltype, ref = row
    assert name == fk_name
    # 'n' == SET NULL in pg_constraint.confdeltype
    assert deltype == "n", f"{fk_name} ondelete is {deltype!r}, expected 'n' (SET NULL)"
    assert ref == ref_table


# ---------------------------------------------------------------------------
# DB-003 — partial unique on storage_locations / tags
# ---------------------------------------------------------------------------


def _make_workspace(db) -> str:
    """Insert a minimal user + workspace pair so the FK from
    storage_locations / tags resolves. `workspaces.kind` and
    `workspaces.owner_user_id` are NOT NULL — the model defines a
    Python-side default for `kind` that doesn't fire on a raw
    `INSERT INTO workspaces (...)` so we set every required column
    explicitly here."""
    user_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users (id, email, name, password_hash, locale, "
            "                   timezone, created_at) "
            "VALUES (:i, :e, 't', 'x', 'en', 'UTC', now())"
        ),
        {"i": user_id, "e": f"u-{user_id.hex[:6]}@x.com"},
    )
    ws_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO workspaces "
            "(id, name, kind, owner_user_id, currency_default, "
            " lot_control_enabled, serial_tracking_enabled, "
            " catalog_enabled, parts_provider, scanner, created_at) "
            "VALUES (:i, :n, 'organization', :owner, 'USD', "
            "        true, false, false, 'none', 'zxing', now())"
        ),
        {"i": ws_id, "n": f"ws-{ws_id.hex[:6]}", "owner": user_id},
    )
    db.commit()
    return ws_id


def test_storage_location_archived_name_can_be_reused(db):
    ws_id = _make_workspace(db)

    # Active row holding the name.
    a_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO storage_locations (id, workspace_id, name, single_part_only, "
            "existing_parts_only, is_full, created_at, updated_at) "
            "VALUES (:i, :w, 'ShelfA', false, false, false, now(), now())"
        ),
        {"i": a_id, "w": ws_id},
    )
    db.commit()

    # Archive it; a new active row with the same name is now allowed.
    db.execute(
        text("UPDATE storage_locations SET archived_at = now() WHERE id = :i"),
        {"i": a_id},
    )
    db.commit()

    db.execute(
        text(
            "INSERT INTO storage_locations (id, workspace_id, name, single_part_only, "
            "existing_parts_only, is_full, created_at, updated_at) "
            "VALUES (:i, :w, 'ShelfA', false, false, false, now(), now())"
        ),
        {"i": uuid.uuid4(), "w": ws_id},
    )
    db.commit()


def test_storage_location_two_active_same_name_fails(db):
    ws_id = _make_workspace(db)

    db.execute(
        text(
            "INSERT INTO storage_locations (id, workspace_id, name, single_part_only, "
            "existing_parts_only, is_full, created_at, updated_at) "
            "VALUES (:i, :w, 'ShelfDup', false, false, false, now(), now())"
        ),
        {"i": uuid.uuid4(), "w": ws_id},
    )
    db.commit()

    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO storage_locations (id, workspace_id, name, single_part_only, "
                "existing_parts_only, is_full, created_at, updated_at) "
                "VALUES (:i, :w, 'ShelfDup', false, false, false, now(), now())"
            ),
            {"i": uuid.uuid4(), "w": ws_id},
        )
        db.commit()
    db.rollback()


def test_tag_archived_name_can_be_reused(db):
    ws_id = _make_workspace(db)

    a_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO tags (id, workspace_id, name, created_at, updated_at) "
            "VALUES (:i, :w, 'urgent', now(), now())"
        ),
        {"i": a_id, "w": ws_id},
    )
    db.commit()

    db.execute(
        text("UPDATE tags SET archived_at = now() WHERE id = :i"),
        {"i": a_id},
    )
    db.commit()

    db.execute(
        text(
            "INSERT INTO tags (id, workspace_id, name, created_at, updated_at) "
            "VALUES (:i, :w, 'urgent', now(), now())"
        ),
        {"i": uuid.uuid4(), "w": ws_id},
    )
    db.commit()


# ---------------------------------------------------------------------------
# DB-004 — composite (workspace_id, archived_at) indexes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table,index_name",
    [
        ("attachments", "ix_attachments_ws_archived"),
        ("tag_links", "ix_tag_links_ws_archived"),
        ("custom_fields", "ix_custom_fields_ws_archived"),
        ("bom_import_presets", "ix_bom_import_presets_ws_archived"),
        ("project_entries", "ix_project_entries_ws_archived"),
    ],
)
def test_archived_at_composite_index_exists(db, table, index_name):
    row = db.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = :t AND indexname = :i"
        ),
        {"t": table, "i": index_name},
    ).first()
    assert row is not None, f"missing index {index_name} on {table}"


# ---------------------------------------------------------------------------
# BE2-018 — pg_trgm + GIN search indexes
# ---------------------------------------------------------------------------


def test_pg_trgm_extension_installed(db):
    row = db.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'")
    ).first()
    assert row is not None, "pg_trgm extension not installed"


@pytest.mark.parametrize(
    "table,index_name",
    [
        ("parts", "ix_parts_ws_name_trgm"),
        ("parts", "ix_parts_ws_mpn_trgm"),
        ("storage_locations", "ix_storage_ws_name_trgm"),
        ("projects", "ix_projects_ws_name_trgm"),
        ("lots", "ix_lots_ws_name_trgm"),
        ("orders", "ix_orders_ws_name_trgm"),
    ],
)
def test_trgm_index_exists(db, table, index_name):
    row = db.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = :t AND indexname = :i"
        ),
        {"t": table, "i": index_name},
    ).first()
    assert row is not None, f"missing trgm index {index_name} on {table}"
    assert "gin" in row[0].lower()
    assert "gin_trgm_ops" in row[0]
