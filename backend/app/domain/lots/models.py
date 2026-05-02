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
        # pg_trgm GIN index for ILIKE %q% search (alembic 0018, BE2-018).
        Index(
            "ix_lots_ws_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=True)
    serial_number = Column(String(200), nullable=True, index=True)
    parent_lot_id = Column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), nullable=True)
    description = Column(Text, nullable=True)
    comments = Column(Text, nullable=True)
    expiration_date = Column(Date, nullable=True)
    source_type = Column(String(20), nullable=False, default="manual")
    # Cross-table refs: FK + ON DELETE SET NULL added in alembic 0018
    # (DB-001 / BE2-002). Constraint names are pinned so downgrade can
    # drop them by name.
    source_order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL", name="fk_lots_source_order_id"),
        nullable=True,
    )
    source_build_id = Column(
        UUID(as_uuid=True),
        ForeignKey("builds.id", ondelete="SET NULL", name="fk_lots_source_build_id"),
        nullable=True,
    )
    purchase_quantity = Column(Integer, nullable=True)
    purchase_unit_cost = Column(Numeric(18, 6), nullable=True)
    purchase_currency = Column(String(3), nullable=True)
