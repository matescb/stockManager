from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.time import utcnow
from app.infra.db import Base


class StockEntry(Base):
    """Append-only stock ledger row. Current stock = SUM(quantity_delta) over filters."""

    __tablename__ = "stock_entries"
    __table_args__ = (
        Index("ix_stock_ws_part_status", "workspace_id", "part_id", "status"),
        Index("ix_stock_ws_lot", "workspace_id", "lot_id"),
        Index("ix_stock_ws_storage", "workspace_id", "storage_location_id"),
        Index("ix_stock_ws_occurred", "workspace_id", "occurred_at"),
        # Bag-rescan recognition: sha256 of the normalised raw bag code
        # captured when this entry was created via scan-import. Looking
        # the same bag up again should let the operator consume from
        # the lot it created instead of double-importing. See
        # alembic 0012 + bagSignature() in web/src/lib/bagCode.ts.
        # Partial predicate (DB-008 / alembic 0019) — only scan-import
        # rows ever populate `bag_signature`, so the index excludes the
        # ~99% of NULL rows and stops paying insert-time cost on every
        # ledger write.
        Index(
            "ix_stock_ws_bag_signature",
            "workspace_id",
            "bag_signature",
            postgresql_where=text("bag_signature IS NOT NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    part_id = Column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True
    )
    lot_id = Column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), nullable=True)
    storage_location_id = Column(
        UUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="SET NULL"), nullable=True
    )
    quantity_delta = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="on_hand")
    unit_price = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    operation_type = Column(String(40), nullable=False)
    related_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stock_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Cross-table refs: FK + ON DELETE SET NULL added in alembic 0018
    # (DB-001 / BE2-002). Constraint names are pinned so downgrade can
    # drop them by name.
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL", name="fk_stock_entries_order_id"),
        nullable=True,
    )
    order_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("order_entries.id", ondelete="SET NULL", name="fk_stock_entries_order_entry_id"),
        nullable=True,
    )
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    build_id = Column(
        UUID(as_uuid=True),
        ForeignKey("builds.id", ondelete="SET NULL", name="fk_stock_entries_build_id"),
        nullable=True,
    )
    comments = Column(Text, nullable=True)
    # sha256 hex digest of the raw bag code that produced this entry, only
    # set by the scan-import flow. Used to recognise re-scans so the
    # operator can consume from this bag's lot instead of double-importing.
    bag_signature = Column(String(64), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
