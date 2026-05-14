from __future__ import annotations

import uuid

from sqlalchemy import inspect, text


def test_stock_entries_part_fk_sets_null_on_part_delete(db, engine) -> None:
    insp = inspect(engine)
    part_column = next(col for col in insp.get_columns("stock_entries") if col["name"] == "part_id")
    part_fk = next(
        fk
        for fk in insp.get_foreign_keys("stock_entries")
        if fk["constrained_columns"] == ["part_id"] and fk["referred_table"] == "parts"
    )

    assert part_column["nullable"] is True
    assert part_fk.get("options", {}).get("ondelete") == "SET NULL"

    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    part_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    db.execute(
        text(
            """
            INSERT INTO users
                (id, email, name, password_hash, locale, timezone, created_at)
            VALUES
                (:user_id, :email, 'FK Tester', 'x', 'en', 'UTC', now())
            """
        ),
        {"user_id": user_id, "email": f"fk-{user_id.hex}@example.com"},
    )
    db.execute(
        text(
            """
            INSERT INTO workspaces
                (id, name, kind, owner_user_id, currency_default,
                 lot_control_enabled, serial_tracking_enabled, catalog_enabled,
                 parts_provider, created_at)
            VALUES
                (:workspace_id, 'FK Workspace', 'organization', :user_id,
                 'USD', true, false, false, 'none', now())
            """
        ),
        {"workspace_id": workspace_id, "user_id": user_id},
    )
    db.execute(
        text(
            """
            INSERT INTO parts
                (id, workspace_id, part_type, name, attrition_percentage,
                 attrition_min_quantity, default_storage_mandatory, serialized, published,
                 description_locally_edited, created_at, updated_at, created_by, updated_by)
            VALUES
                (:part_id, :workspace_id, 'local', 'Ledger preserved part',
                 0, 0, false, false, false, false, now(), now(), :user_id, :user_id)
            """
        ),
        {"part_id": part_id, "workspace_id": workspace_id, "user_id": user_id},
    )
    db.execute(
        text(
            """
            INSERT INTO stock_entries
                (id, workspace_id, part_id, quantity_delta, status,
                 operation_type, occurred_at, created_by, created_at)
            VALUES
                (:entry_id, :workspace_id, :part_id, 7, 'on_hand',
                 'add', now(), :user_id, now())
            """
        ),
        {
            "entry_id": entry_id,
            "workspace_id": workspace_id,
            "part_id": part_id,
            "user_id": user_id,
        },
    )

    db.execute(text("DELETE FROM parts WHERE id = :part_id"), {"part_id": part_id})

    row = db.execute(
        text(
            """
            SELECT id, workspace_id, part_id, quantity_delta, operation_type
              FROM stock_entries
             WHERE id = :entry_id
            """
        ),
        {"entry_id": entry_id},
    ).mappings().one()

    assert row["id"] == entry_id
    assert row["workspace_id"] == workspace_id
    assert row["part_id"] is None
    assert row["quantity_delta"] == 7
    assert row["operation_type"] == "add"
