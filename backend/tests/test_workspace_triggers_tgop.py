from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError


def _insert_workspace(db, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO users
                (id, email, name, password_hash, locale, timezone, created_at)
            VALUES
                (:user_id, :email, :name, 'x', 'en', 'UTC', now())
            """
        ),
        {
            "user_id": user_id,
            "email": f"{label}-{user_id.hex}@example.com",
            "name": f"{label} tester",
        },
    )
    db.execute(
        text(
            """
            INSERT INTO workspaces
                (id, name, kind, owner_user_id, currency_default,
                 lot_control_enabled, serial_tracking_enabled, catalog_enabled,
                 parts_provider, created_at)
            VALUES
                (:workspace_id, :name, 'organization', :user_id,
                 'USD', true, false, false, 'none', now())
            """
        ),
        {"workspace_id": workspace_id, "name": f"{label} workspace", "user_id": user_id},
    )
    return user_id, workspace_id


def _insert_part(db, workspace_id: uuid.UUID, user_id: uuid.UUID, label: str) -> uuid.UUID:
    part_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO parts
                (id, workspace_id, part_type, name, attrition_percentage,
                 attrition_min_quantity, default_storage_mandatory, serialized,
                 published, description_locally_edited, created_at, updated_at,
                 created_by, updated_by)
            VALUES
                (:part_id, :workspace_id, 'local', :name, 0, 0, false, false,
                 false, false, now(), now(), :user_id, :user_id)
            """
        ),
        {
            "part_id": part_id,
            "workspace_id": workspace_id,
            "name": f"{label} part",
            "user_id": user_id,
        },
    )
    return part_id


def _insert_storage(db, workspace_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    storage_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO storage_locations
                (id, workspace_id, name, single_part_only, existing_parts_only,
                 is_full, created_at, updated_at, created_by, updated_by)
            VALUES
                (:storage_id, :workspace_id, 'Foreign bin', false, false,
                 false, now(), now(), :user_id, :user_id)
            """
        ),
        {"storage_id": storage_id, "workspace_id": workspace_id, "user_id": user_id},
    )
    return storage_id


def _insert_lot(
    db,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    part_id: uuid.UUID,
) -> uuid.UUID:
    lot_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO lots
                (id, workspace_id, part_id, name, source_type, created_at,
                 updated_at, created_by, updated_by)
            VALUES
                (:lot_id, :workspace_id, :part_id, 'Foreign lot', 'manual',
                 now(), now(), :user_id, :user_id)
            """
        ),
        {
            "lot_id": lot_id,
            "workspace_id": workspace_id,
            "part_id": part_id,
            "user_id": user_id,
        },
    )
    return lot_id


def _insert_project_build_order(
    db,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    part_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    project_id = uuid.uuid4()
    build_id = uuid.uuid4()
    order_id = uuid.uuid4()
    order_entry_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO projects
                (id, workspace_id, name, created_at, updated_at,
                 created_by, updated_by)
            VALUES
                (:project_id, :workspace_id, 'Foreign project', now(), now(),
                 :user_id, :user_id)
            """
        ),
        {"project_id": project_id, "workspace_id": workspace_id, "user_id": user_id},
    )
    db.execute(
        text(
            """
            INSERT INTO builds
                (id, workspace_id, project_id, name, quantity, status,
                 created_at, updated_at, created_by, updated_by)
            VALUES
                (:build_id, :workspace_id, :project_id, 'Foreign build', 1,
                 'planned', now(), now(), :user_id, :user_id)
            """
        ),
        {
            "build_id": build_id,
            "workspace_id": workspace_id,
            "project_id": project_id,
            "user_id": user_id,
        },
    )
    db.execute(
        text(
            """
            INSERT INTO orders
                (id, workspace_id, name, order_type, status, created_at,
                 updated_at, created_by, updated_by)
            VALUES
                (:order_id, :workspace_id, 'Foreign order', 'purchase',
                 'draft', now(), now(), :user_id, :user_id)
            """
        ),
        {"order_id": order_id, "workspace_id": workspace_id, "user_id": user_id},
    )
    db.execute(
        text(
            """
            INSERT INTO order_entries
                (id, workspace_id, order_id, part_id, quantity_ordered,
                 quantity_received, order_index, created_at, updated_at,
                 created_by, updated_by)
            VALUES
                (:order_entry_id, :workspace_id, :order_id, :part_id, 1, 0,
                 0, now(), now(), :user_id, :user_id)
            """
        ),
        {
            "order_entry_id": order_entry_id,
            "workspace_id": workspace_id,
            "order_id": order_id,
            "part_id": part_id,
            "user_id": user_id,
        },
    )
    return project_id, build_id, order_id, order_entry_id


def _insert_stock_entry(
    db,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    part_id: uuid.UUID,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO stock_entries
                (id, workspace_id, part_id, quantity_delta, status,
                 operation_type, occurred_at, created_by, created_at)
            VALUES
                (:entry_id, :workspace_id, :part_id, 1, 'on_hand',
                 'audit_probe', now(), :user_id, now())
            """
        ),
        {
            "entry_id": entry_id,
            "workspace_id": workspace_id,
            "part_id": part_id,
            "user_id": user_id,
        },
    )
    return entry_id


def test_unrelated_update_skips_fk_revalidation(db) -> None:
    user_a, workspace_a = _insert_workspace(db, "a")
    user_b, workspace_b = _insert_workspace(db, "b")
    part_a = _insert_part(db, workspace_a, user_a, "a")
    part_b = _insert_part(db, workspace_b, user_b, "b")
    storage_a = _insert_storage(db, workspace_a, user_a)
    lot_a = _insert_lot(db, workspace_a, user_a, part_a)
    project_a, build_a, order_a, order_entry_a = _insert_project_build_order(
        db,
        workspace_a,
        user_a,
        part_a,
    )
    entry_id = _insert_stock_entry(db, workspace_b, user_b, part_b)
    changed_fk_entry_id = _insert_stock_entry(db, workspace_b, user_b, part_b)

    try:
        db.execute(
            text(
                "ALTER TABLE stock_entries "
                "DISABLE TRIGGER stock_entries_workspace_fk_check"
            )
        )
        db.execute(
            text(
                """
                UPDATE stock_entries
                   SET lot_id = :lot_id,
                       storage_location_id = :storage_id,
                       project_id = :project_id,
                       build_id = :build_id,
                       order_id = :order_id,
                       order_entry_id = :order_entry_id
                 WHERE id = :entry_id
                """
            ),
            {
                "entry_id": entry_id,
                "lot_id": lot_a,
                "storage_id": storage_a,
                "project_id": project_a,
                "build_id": build_a,
                "order_id": order_a,
                "order_entry_id": order_entry_a,
            },
        )
    finally:
        db.execute(
            text(
                "ALTER TABLE stock_entries "
                "ENABLE TRIGGER stock_entries_workspace_fk_check"
            )
        )

    db.execute(
        text(
            """
            UPDATE stock_entries
               SET workspace_id = :workspace_id
             WHERE id = :entry_id
            """
        ),
        {"workspace_id": workspace_b, "entry_id": entry_id},
    )

    row = db.execute(
        text(
            """
            SELECT workspace_id, lot_id, storage_location_id, project_id,
                   build_id, order_id, order_entry_id
              FROM stock_entries
             WHERE id = :entry_id
            """
        ),
        {"entry_id": entry_id},
    ).mappings().one()
    assert row["workspace_id"] == workspace_b
    assert row["lot_id"] == lot_a
    assert row["storage_location_id"] == storage_a
    assert row["project_id"] == project_a
    assert row["build_id"] == build_a
    assert row["order_id"] == order_a
    assert row["order_entry_id"] == order_entry_a

    with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
        db.execute(
            text(
                """
                UPDATE stock_entries
                   SET lot_id = :lot_id
                 WHERE id = :entry_id
                """
            ),
            {"lot_id": lot_a, "entry_id": changed_fk_entry_id},
        )
        db.flush()
