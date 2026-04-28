from __future__ import annotations

from sqlalchemy import Column, Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Lot(WorkspaceOwned, Base):
    __tablename__ = "lots"
    __table_args__ = (
        Index("ix_lots_ws_part", "workspace_id", "part_id"),
        Index("ix_lots_ws_archived", "workspace_id", "archived_at"),
    )

    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=True)
    serial_number = Column(String(200), nullable=True, index=True)
    parent_lot_id = Column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), nullable=True)
    description = Column(Text, nullable=True)
    comments = Column(Text, nullable=True)
    expiration_date = Column(Date, nullable=True)
    source_type = Column(String(20), nullable=False, default="manual")
    source_order_id = Column(UUID(as_uuid=True), nullable=True)
    source_build_id = Column(UUID(as_uuid=True), nullable=True)
    purchase_quantity = Column(Integer, nullable=True)
    purchase_unit_cost = Column(Numeric(18, 6), nullable=True)
    purchase_currency = Column(String(3), nullable=True)
