"""Read-only script: report polymorphic-table orphan counts.

Prints per-(workspace, discriminator) orphan counts for every polymorphic
child table — rows whose parent-id column no longer resolves to a live row
in the parent table.

The set of child tables comes from
`app/domain/_polymorphic_cleanup.py::_child_tables()`, the same registry the
`before_delete` listeners use, so this script covers exactly what cleanup is
supposed to cover — no more hardcoded three-table list that silently
under-reports whichever table was added last (`object_codes` was, in #892).
That also handles the fact that `object_codes` names its pair
`entity_type` / `entity_id` while the other three use
`object_type` / `object_id`. Symmetric with
`scripts/purge_polymorphic_orphans.py`.

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


def _orphan_counts_for_table(
    db, table: str, type_col: str, id_col: str, parent_table: str
) -> list[dict]:
    """Return rows from `table` whose parent id has no match in `parent_table`.

    Groups by (workspace_id, <discriminator>) so the output is easy to scan.
    Uses a NOT EXISTS subquery rather than a LEFT JOIN so the query planner
    can use the ix_*_ws_objid_only index on the child side.

    `type_col` / `id_col` are parameters, not literals, because `object_codes`
    calls its pair `entity_type` / `entity_id`. They come from the cleanup
    registry, never from user input.
    """
    sql = text(f"""
        SELECT
            c.workspace_id,
            c.{type_col} AS object_type,
            count(*) AS orphan_count
        FROM {table} c
        WHERE NOT EXISTS (
            SELECT 1
            FROM {parent_table} p
            WHERE p.id = c.{id_col}
              AND p.workspace_id = c.workspace_id
        )
        GROUP BY c.workspace_id, c.{type_col}
        ORDER BY orphan_count DESC
    """)
    return [dict(row._mapping) for row in db.execute(sql)]


def main() -> None:
    from app.api._helpers import _polymorphic_resolvers
    from app.domain._polymorphic_cleanup import _child_tables

    resolvers = _polymorphic_resolvers()
    # Build a map of object_type -> parent table name
    type_to_table: dict[str, str] = {
        ot: model.__tablename__  # type: ignore[attr-defined]
        for ot, model in resolvers.items()
    }

    db = _build_session()
    try:
        # (table, discriminator column, parent-id column) straight from the
        # cleanup registry — attachments, custom_fields, tag_links AND
        # object_codes, and whatever is added to it next.
        tables = [
            (table_name, type_col, id_col)
            for _Model, table_name, type_col, id_col in _child_tables()
        ]

        total_orphans = 0
        for child_table, type_col, id_col in tables:
            print(f"\n=== {child_table} orphans ===")
            found_any = False
            for object_type, parent_table in type_to_table.items():
                rows = _orphan_counts_for_table(
                    db, child_table, type_col, id_col, parent_table
                )
                for row in rows:
                    if str(row["object_type"]) == object_type:
                        print(
                            f"  workspace={row['workspace_id']}  "
                            f"object_type={row['object_type']}  "
                            f"orphans={row['orphan_count']}"
                        )
                        total_orphans += int(row["orphan_count"])
                        found_any = True

            # Also surface rows with unrecognised discriminator values
            all_types_sql = text(f"""
                SELECT DISTINCT {type_col} FROM {child_table}
            """)
            all_types = {r[0] for r in db.execute(all_types_sql)}
            unknown = all_types - set(type_to_table.keys())
            for ot in sorted(unknown):
                count_sql = text(f"""
                    SELECT count(*) FROM {child_table} WHERE {type_col} = :ot
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
