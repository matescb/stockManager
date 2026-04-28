from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.infra.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StockEntry(Base):
    """Append-only stock ledger row. Current stock = SUM(quantity_delta) over filters."""

    __tablename__ = "stock_entries"
    __table_args__ = (
        Index("ix_stock_ws_part_status", "workspace_id", "part_id", "status"),
        Index("ix_stock_ws_lot", "workspace_id", "lot_id"),
        Index("ix_stock_ws_storage", "workspace_id", "storage_location_id"),
        Index("ix_stock_ws_occurred", "workspace_id", "occurred_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False)
    lot_id = Column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), nullable=True)
    storage_location_id = Column(
        UUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="SET NULL"), nullable=True
    )
    quantity_delta = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="on_hand")
    unit_price = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    operation_type = Column(String(40), nullable=False)
    related_entry_id = Column(UUID(as_uuid=True), ForeignKey("stock_entries.id", ondelete="SET NULL"), nullable=True)
    order_id = Column(UUID(as_uuid=True), nullable=True)
    order_entry_id = Column(UUID(as_uuid=True), nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    build_id = Column(UUID(as_uuid=True), nullable=True)
    comments = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
