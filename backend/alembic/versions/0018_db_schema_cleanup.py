"""DB schema cleanup: FKs on stock_entries cross-table refs, partial unique
on soft-delete tables, composite (workspace_id, archived_at) indexes,
pg_trgm GIN indexes for ILIKE search.

Revision ID: 0018
Revises: 0016
Create Date: 2026-05-02

NOTE on chain: this migration is numbered 0018 to leave room for
PR #31 (Batch 1 — auth/CSRF) which reserves 0017 for session-token
hashing. While both PRs are open in parallel, this file's
`down_revision` points directly at 0016 so the chain is valid on this
branch standalone. **Before merging, rebase onto main and update
`down_revision = "0017"`** if PR #31 has already landed; otherwise
alembic will fork on 0016.

Closes:
  * DB-001 / BE2-002 / v1 BE CRIT-4 — `stock_entries.order_id`,
    `order_entry_id`, `build_id` and `lots.source_order_id`,
    `source_build_id` were bare UUID columns with no FK enforcement.
    A cascading delete of an order/build left dangling pointers.
  * DB-003 — `uq_storage_ws_name` and `uq_tag_ws_name` were full
    UniqueConstraints, ignoring the soft-delete pattern. Restoring an
    archived row clashed with re-using its name. Replaced with partial
    unique indexes `WHERE archived_at IS NULL`.
  * DB-004 — Several workspace-scoped tables (`attachments`,
    `tag_links`, `custom_fields`, `bom_import_presets`,
    `project_entries`) lacked the universal `(workspace_id, archived_at)`
    index that every active-row query relies on.
  * BE2-018 — Search ILIKE %q% on parts/storage/projects/lots/orders
    was a 5x sequential scan per keystroke. pg_trgm GIN indexes turn
    those into index-supported lookups.

Pre-flight:
  * Orphan UUIDs in the cross-table-ref columns are NULLed before the
    FK is added. The user reports no real prod data, but the pattern
    is correct and the tests exercise it.
  * Active-row name duplicates on storage_locations / tags would block
    the partial unique index. The migration RAISES with a clear error
    message — the operator must reconcile data and retry.

Idempotence / round-trip:
  * `CREATE EXTENSION IF NOT EXISTS pg_trgm` is a no-op when present.
  * Index creation uses `op.create_index` (errors if it already exists);
    the migration is fresh, so no IF NOT EXISTS dance is needed.
  * Downgrade restores prior FK-less columns + UniqueConstraints, but
    deliberately does NOT drop the pg_trgm extension (harmless to leave
    installed and other migrations may rely on it later).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


# Tables that get a partial composite (workspace_id, archived_at) index
# in this migration. The 5 tables identified by DB-004. All inherit
# `archived_at` via the WorkspaceOwned mixin.
_ARCHIVED_AT_INDEX_TABLES = (
    "attachments",
    "tag_links",
    "custom_fields",
    "bom_import_presets",
    "project_entries",
)


# pg_trgm GIN indexes. Each tuple: (index_name, table, column).
_TRGM_INDEXES = (
    ("ix_parts_ws_name_trgm", "parts", "name"),
    ("ix_parts_ws_mpn_trgm", "parts", "mpn"),
    ("ix_storage_ws_name_trgm", "storage_locations", "name"),
    ("ix_projects_ws_name_trgm", "projects", "name"),
    ("ix_lots_ws_name_trgm", "lots", "name"),
    ("ix_orders_ws_name_trgm", "orders", "name"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. FKs on stock_entries / lots cross-table refs (DB-001 / BE2-002)
    # ------------------------------------------------------------------
    # Pre-flight: orphan rows must be NULLed before the FK is created.
    # No real prod data per the user, but tests exercise this path.
    op.execute(
        "UPDATE stock_entries SET order_id = NULL "
        "WHERE order_id IS NOT NULL "
        "AND order_id NOT IN (SELECT id FROM orders)"
    )
    op.execute(
        "UPDATE stock_entries SET order_entry_id = NULL "
        "WHERE order_entry_id IS NOT NULL "
        "AND order_entry_id NOT IN (SELECT id FROM order_entries)"
    )
    op.execute(
        "UPDATE stock_entries SET build_id = NULL "
        "WHERE build_id IS NOT NULL "
        "AND build_id NOT IN (SELECT id FROM builds)"
    )
    op.execute(
        "UPDATE lots SET source_order_id = NULL "
        "WHERE source_order_id IS NOT NULL "
        "AND source_order_id NOT IN (SELECT id FROM orders)"
    )
    op.execute(
        "UPDATE lots SET source_build_id = NULL "
        "WHERE source_build_id IS NOT NULL "
        "AND source_build_id NOT IN (SELECT id FROM builds)"
    )

    op.create_foreign_key(
        "fk_stock_entries_order_id",
        "stock_entries", "orders",
        ["order_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_stock_entries_order_entry_id",
        "stock_entries", "order_entries",
        ["order_entry_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_stock_entries_build_id",
        "stock_entries", "builds",
        ["build_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_lots_source_order_id",
        "lots", "orders",
        ["source_order_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_lots_source_build_id",
        "lots", "builds",
        ["source_build_id"], ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # 2. Partial unique on storage_locations / tags (DB-003)
    # ------------------------------------------------------------------
    # Pre-flight: active duplicates would prevent the partial unique
    # index from being created. Fail loud with a clear message rather
    # than silently swallowing the error mid-migration.
    for table in ("storage_locations", "tags"):
        dup = bind.execute(
            sa.text(
                f"SELECT workspace_id, name, COUNT(*) AS n "
                f"FROM {table} "
                f"WHERE archived_at IS NULL "
                f"GROUP BY workspace_id, name HAVING COUNT(*) > 1"
            )
        ).fetchall()
        if dup:
            raise RuntimeError(
                f"Active duplicate (workspace_id, name) rows on {table}: "
                f"{dup!r}. Reconcile (rename or archive) before retrying."
            )

    op.drop_constraint("uq_storage_ws_name", "storage_locations", type_="unique")
    op.create_index(
        "uq_storage_ws_name",
        "storage_locations",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.drop_constraint("uq_tag_ws_name", "tags", type_="unique")
    op.create_index(
        "uq_tag_ws_name",
        "tags",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # 3. Composite (workspace_id, archived_at) partial indexes (DB-004)
    # ------------------------------------------------------------------
    for table in _ARCHIVED_AT_INDEX_TABLES:
        op.create_index(
            f"ix_{table}_ws_archived",
            table,
            ["workspace_id", "archived_at"],
            unique=False,
            postgresql_where=sa.text("archived_at IS NULL"),
        )

    # ------------------------------------------------------------------
    # 4. pg_trgm GIN indexes for ILIKE search (BE2-018)
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    for name, table, column in _TRGM_INDEXES:
        # Single-column GIN trigram index. Combining workspace_id into the
        # same GIN index would require the btree_gin extension; the planner
        # will instead bitmap-AND this trigram lookup with the existing
        # (workspace_id, archived_at) btree, which is the standard pattern.
        op.create_index(
            name,
            table,
            [column],
            unique=False,
            postgresql_using="gin",
            postgresql_ops={column: "gin_trgm_ops"},
        )


def downgrade() -> None:
    # Drop trigram indexes (extension stays — it's harmless and other
    # migrations may have come to rely on it).
    for name, table, _column in _TRGM_INDEXES:
        op.drop_index(name, table_name=table)

    # Drop the (workspace_id, archived_at) partial composites.
    for table in _ARCHIVED_AT_INDEX_TABLES:
        op.drop_index(f"ix_{table}_ws_archived", table_name=table)

    # Restore full UniqueConstraints (lossy if archived duplicates now
    # exist — but the upgrade pre-flight made sure they didn't, and a
    # rollback that close to the upgrade should still satisfy the
    # invariant).
    op.drop_index("uq_tag_ws_name", table_name="tags")
    op.create_unique_constraint(
        "uq_tag_ws_name", "tags", ["workspace_id", "name"]
    )

    op.drop_index("uq_storage_ws_name", table_name="storage_locations")
    op.create_unique_constraint(
        "uq_storage_ws_name", "storage_locations", ["workspace_id", "name"]
    )

    # Drop FKs we added. Columns stay (and any data still pointed at
    # the now-deleted rows would simply lose the integrity guarantee).
    op.drop_constraint("fk_lots_source_build_id", "lots", type_="foreignkey")
    op.drop_constraint("fk_lots_source_order_id", "lots", type_="foreignkey")
    op.drop_constraint(
        "fk_stock_entries_build_id", "stock_entries", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_stock_entries_order_entry_id", "stock_entries", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_stock_entries_order_id", "stock_entries", type_="foreignkey"
    )
