from __future__ import annotations

import uuid

from sqlalchemy import inspect, text


def test_part_hard_delete_preserves_lots_history(db, engine) -> None:
    insp = inspect(engine)
    part_column = next(col for col in insp.get_columns("lots") if col["name"] == "part_id")
    part_fk = next(
        fk
        for fk in insp.get_foreign_keys("lots")
        if fk["constrained_columns"] == ["part_id"] and fk["referred_table"] == "parts"
    )

    assert part_column["nullable"] is True
    assert part_fk.get("options", {}).get("ondelete") == "SET NULL"

    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    part_id = uuid.uuid4()
    lot_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    db.execute(
        text(
            """
            INSERT INTO users
                (id, email, name, password_hash, locale, timezone, created_at)
            VALUES
                (:user_id, :email, 'Lot FK Tester', 'x', 'en', 'UTC', now())
            """
        ),
        {"user_id": user_id, "email": f"lot-fk-{user_id.hex}@example.com"},
    )
    db.execute(
        text(
            """
            INSERT INTO workspaces
                (id, name, kind, owner_user_id, currency_default,
                 lot_control_enabled, serial_tracking_enabled, catalog_enabled,
                 parts_provider, created_at)
            VALUES
                (:workspace_id, 'Lot FK Workspace', 'organization', :user_id,
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
                 attrition_min_quantity, default_storage_mandatory, serialized,
                 published, description_locally_edited, created_at, updated_at,
                 created_by, updated_by)
            VALUES
                (:part_id, :workspace_id, 'local', 'Lot preserved part',
                 0, 0, false, false, false, false, now(), now(), :user_id,
                 :user_id)
            """
        ),
        {"part_id": part_id, "workspace_id": workspace_id, "user_id": user_id},
    )
    db.execute(
        text(
            """
            INSERT INTO lots
                (id, workspace_id, part_id, name, source_type, created_at,
                 updated_at, created_by, updated_by)
            VALUES
                (:lot_id, :workspace_id, :part_id, 'Lot survives',
                 'manual', now(), now(), :user_id, :user_id)
            """
        ),
        {
            "lot_id": lot_id,
            "workspace_id": workspace_id,
            "part_id": part_id,
            "user_id": user_id,
        },
    )
    db.execute(
        text(
            """
            INSERT INTO stock_entries
                (id, workspace_id, part_id, lot_id, quantity_delta, status,
                 operation_type, occurred_at, created_by, created_at)
            VALUES
                (:entry_id, :workspace_id, :part_id, :lot_id, 3, 'on_hand',
                 'add', now(), :user_id, now())
            """
        ),
        {
            "entry_id": entry_id,
            "workspace_id": workspace_id,
            "part_id": part_id,
            "lot_id": lot_id,
            "user_id": user_id,
        },
    )

    db.execute(text("DELETE FROM parts WHERE id = :part_id"), {"part_id": part_id})

    lot = db.execute(
        text(
            """
            SELECT id, workspace_id, part_id, name
              FROM lots
             WHERE id = :lot_id
            """
        ),
        {"lot_id": lot_id},
    ).mappings().one()
    entry = db.execute(
        text(
            """
            SELECT id, workspace_id, part_id, lot_id, quantity_delta
              FROM stock_entries
             WHERE id = :entry_id
            """
        ),
        {"entry_id": entry_id},
    ).mappings().one()

    assert lot["id"] == lot_id
    assert lot["workspace_id"] == workspace_id
    assert lot["part_id"] is None
    assert lot["name"] == "Lot survives"
    assert entry["id"] == entry_id
    assert entry["workspace_id"] == workspace_id
    assert entry["part_id"] is None
    assert entry["lot_id"] == lot_id
    assert entry["quantity_delta"] == 3
