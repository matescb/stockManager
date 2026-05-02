"""Read-only script: report polymorphic-table orphan counts.

Prints per-(workspace, object_type) orphan counts for attachments,
custom_fields, and tag_links — rows whose object_id no longer resolves
to a live row in the parent table.

Usage::

    DATABASE_URL=postgresql+psycopg://... python scripts/report_polymorphic_orphans.py

The script uses the _polymorphic_resolvers() registry from
app.api._helpers so the set of tracked object types stays in one place.
Only object types in the registry are checked — unknown legacy values
are reported under the "UNRECOGNISED" category.

This script is read-only and safe to run against prod.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from uuid import UUID

# Allow running from the backend directory without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SESSION_SECRET", "unused-for-read-only-script")

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import app.domain.all_models  # noqa: F401 — ensure all models are registered


def _build_session():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL environment variable is required")
    engine = create_engine(url, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    return Session()


def _orphan_counts_for_table(db, table: str, parent_table: str) -> list[dict]:
    """Return rows from `table` whose object_id has no match in `parent_table`.

    Groups by (workspace_id, object_type) so the output is easy to scan.
    Uses a NOT EXISTS subquery rather than a LEFT JOIN so the query planner
    can use the ix_*_ws_objid_only index on the child side.
    """
    sql = text(f"""
        SELECT
            c.workspace_id,
            c.object_type,
            count(*) AS orphan_count
        FROM {table} c
        WHERE NOT EXISTS (
            SELECT 1
            FROM {parent_table} p
            WHERE p.id = c.object_id
              AND p.workspace_id = c.workspace_id
        )
        GROUP BY c.workspace_id, c.object_type
        ORDER BY orphan_count DESC
    """)
    return [dict(row._mapping) for row in db.execute(sql)]


def main() -> None:
    from app.api._helpers import _polymorphic_resolvers

    resolvers = _polymorphic_resolvers()
    # Build a map of object_type -> parent table name
    type_to_table: dict[str, str] = {
        ot: model.__tablename__  # type: ignore[attr-defined]
        for ot, model in resolvers.items()
    }

    db = _build_session()
    try:
        tables = [
            ("attachments", "attachments"),
            ("custom_fields", "custom_fields"),
            ("tag_links", "tag_links"),
        ]

        total_orphans = 0
        for child_table, label in tables:
            print(f"\n=== {label} orphans ===")
            found_any = False
            for object_type, parent_table in type_to_table.items():
                rows = _orphan_counts_for_table(db, child_table, parent_table)
                for row in rows:
                    if str(row["object_type"]) == object_type:
                        print(
                            f"  workspace={row['workspace_id']}  "
                            f"object_type={row['object_type']}  "
                            f"orphans={row['orphan_count']}"
                        )
                        total_orphans += int(row["orphan_count"])
                        found_any = True

            # Also surface rows with unrecognised object_type values
            all_types_sql = text(f"""
                SELECT DISTINCT object_type FROM {child_table}
            """)
            all_types = {r[0] for r in db.execute(all_types_sql)}
            unknown = all_types - set(type_to_table.keys())
            for ot in sorted(unknown):
                count_sql = text(f"""
                    SELECT count(*) FROM {child_table} WHERE object_type = :ot
                """)
                cnt = db.execute(count_sql, {"ot": ot}).scalar_one()
                print(f"  UNRECOGNISED object_type={ot!r}  rows={cnt}")
                found_any = True

            if not found_any:
                print("  (none)")

        print(f"\nTotal orphan rows across all tables: {total_orphans}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
