"""One-off cleanup for orphaned polymorphic child rows.

Dry-run by default. Pass --apply to delete rows whose (object_type,
object_id, workspace_id) no longer resolves to a parent row.

Usage::

    DATABASE_URL=postgresql+psycopg://... python scripts/purge_polymorphic_orphans.py
    DATABASE_URL=postgresql+psycopg://... python scripts/purge_polymorphic_orphans.py --apply
"""
# ruff: noqa: E402,I001
from __future__ import annotations

import argparse
import os
import sys

# Allow running from the backend directory without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SESSION_SECRET", "unused-for-maintenance-script")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import app.domain.all_models  # noqa: F401,E402 — registers model metadata


# (table, discriminator column, parent-id column). `object_codes` names
# its pair `entity_type` / `entity_id`; the other three use
# `object_type` / `object_id`. Mirrors
# `app/domain/_polymorphic_cleanup.py::_child_tables`.
CHILD_TABLES = (
    ("attachments", "object_type", "object_id"),
    ("custom_fields", "object_type", "object_id"),
    ("tag_links", "object_type", "object_id"),
    ("object_codes", "entity_type", "entity_id"),
)


def _build_session():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL environment variable is required")
    engine = create_engine(url, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    return Session()


def _orphan_count_sql(child_table: str, type_col: str, id_col: str, parent_table: str):
    return text(f"""
        SELECT count(*)
        FROM {child_table} c
        WHERE c.{type_col} = :object_type
          AND NOT EXISTS (
            SELECT 1
            FROM {parent_table} p
            WHERE p.id = c.{id_col}
              AND p.workspace_id = c.workspace_id
          )
    """)


def _orphan_delete_sql(child_table: str, type_col: str, id_col: str, parent_table: str):
    return text(f"""
        DELETE FROM {child_table} c
        WHERE c.{type_col} = :object_type
          AND NOT EXISTS (
            SELECT 1
            FROM {parent_table} p
            WHERE p.id = c.{id_col}
              AND p.workspace_id = c.workspace_id
          )
    """)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete orphan rows; without this flag the script only reports counts",
    )
    args = parser.parse_args()

    from app.domain._polymorphic_cleanup import polymorphic_parent_models

    parents = {
        object_type: Model.__tablename__  # type: ignore[attr-defined]
        for object_type, Model in polymorphic_parent_models().items()
    }

    db = _build_session()
    total = 0
    try:
        for child_table, type_col, id_col in CHILD_TABLES:
            print(f"\n=== {child_table} ===")
            for object_type, parent_table in parents.items():
                count = int(
                    db.execute(
                        _orphan_count_sql(child_table, type_col, id_col, parent_table),
                        {"object_type": object_type},
                    ).scalar_one()
                )
                if not count:
                    continue
                total += count
                print(
                    f"  object_type={object_type} parent={parent_table} "
                    f"orphans={count}"
                )
                if args.apply:
                    result = db.execute(
                        _orphan_delete_sql(child_table, type_col, id_col, parent_table),
                        {"object_type": object_type},
                    )
                    print(f"    deleted={int(result.rowcount or 0)}")

        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(f"\nTotal orphan rows {'deleted' if args.apply else 'found'}: {total}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
