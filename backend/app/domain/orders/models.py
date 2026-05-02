from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Order(WorkspaceOwned, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_ws_status", "workspace_id", "status"),
        Index("ix_orders_ws_archived", "workspace_id", "archived_at"),
        # pg_trgm GIN index for ILIKE %q% search (alembic 0018, BE2-018).
        Index(
            "ix_orders_ws_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    name = Column(String(200), nullable=False)
    order_type = Column(String(20), nullable=False, default="purchase")  # purchase|sales
    supplier = Column(String(300), nullable=True)
    status = Column(String(20), nullable=False, default="draft")  # draft|open|partial|received|cancelled
    ordered_on = Column(Date, nullable=True)
    expected_on = Column(Date, nullable=True)
    received_on = Column(Date, nullable=True)
    currency = Column(String(3), nullable=True)
    comments = Column(Text, nullable=True)


class OrderEntry(WorkspaceOwned, Base):
    __tablename__ = "order_entries"
    __table_args__ = (
        Index("ix_order_entries_order", "workspace_id", "order_id"),
        Index("ix_order_entries_part", "workspace_id", "part_id"),
        # DB-005 / migration 0032 — tighten non-negative invariant at DB level.
        CheckConstraint("quantity_ordered >= 0", name="ck_order_entries_qty_ordered_nonneg"),
        CheckConstraint("quantity_received >= 0", name="ck_order_entries_qty_received_nonneg"),
    )

    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(300), nullable=True)  # free-text fallback when part_id is null
    quantity_ordered = Column(Integer, nullable=False, default=0)
    quantity_received = Column(Integer, nullable=False, default=0)
    unit_price = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(3), nullable=True)
    comments = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)
