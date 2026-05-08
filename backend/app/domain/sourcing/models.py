"""SQLAlchemy models for TrustedParts sourcing."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.time import utcnow
from app.infra.db import Base


class SourcingCache(Base):
    """Workspace-scoped short-lived cache for TrustedParts API responses."""

    __tablename__ = "sourcing_cache"
    __table_args__ = (
        CheckConstraint(
            "expires_at <= fetched_at + interval '7 days'",
            name="sourcing_cache_max_7_day_ttl",
        ),
        Index("uq_sourcing_cache_ws_qhash", "workspace_id", "query_hash", unique=True),
        Index("ix_sourcing_cache_expires_at", "expires_at"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_hash = Column(sa.CHAR(length=64), nullable=False)
    query_json = Column(JSONB, nullable=False)
    response_json = Column(JSONB, nullable=False)
    fetched_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=sa.func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=True)


class PurchasePlan(Base):
    """Workspace-scoped short-lived purchase plan offer snapshot."""

    __tablename__ = "purchase_plans"
    __table_args__ = (
        CheckConstraint(
            "build_quantity >= 1",
            name="purchase_plans_build_quantity_positive",
        ),
        CheckConstraint(
            "strategy IN ("
            "'lowest_total_price', "
            "'fewest_distributors', "
            "'fastest_availability', "
            "'preferred_first'"
            ")",
            name="purchase_plans_strategy_check",
        ),
        CheckConstraint(
            "status IN ('draft', 'refreshed', 'converted', 'expired')",
            name="purchase_plans_status_check",
        ),
        CheckConstraint(
            "expires_at <= created_at + interval '7 days'",
            name="purchase_plans_max_7_day_ttl",
        ),
        Index("ix_purchase_plans_expires_at", "expires_at"),
        Index("ix_purchase_plans_ws_project", "workspace_id", "project_id"),
        Index("ix_purchase_plans_ws_status", "workspace_id", "status"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    build_quantity = Column(sa.Integer, nullable=False)
    strategy = Column(sa.String(40), nullable=False)
    country_code = Column(sa.String(2), nullable=True)
    currency_code = Column(sa.String(3), nullable=True)
    preferred_distributors = Column(JSONB, nullable=True)
    status = Column(
        sa.String(20),
        nullable=False,
        default="draft",
        server_default="draft",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=sa.func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=True)

    lines = relationship(
        "PurchasePlanLine",
        back_populates="purchase_plan",
        cascade="all, delete-orphan",
    )


class PurchasePlanLine(Base):
    """One BOM shortage line and selected offer inside a purchase plan."""

    __tablename__ = "purchase_plan_lines"
    __table_args__ = (
        CheckConstraint(
            "required_qty >= 0",
            name="purchase_plan_lines_required_qty_nonnegative",
        ),
        CheckConstraint(
            "internal_available_qty >= 0",
            name="purchase_plan_lines_internal_available_qty_nonnegative",
        ),
        CheckConstraint(
            "shortage_qty >= 0",
            name="purchase_plan_lines_shortage_qty_nonnegative",
        ),
        CheckConstraint(
            "selected_qty IS NULL OR selected_qty >= 0",
            name="purchase_plan_lines_selected_qty_nonnegative",
        ),
        CheckConstraint(
            "selected_moq IS NULL OR selected_moq >= 1",
            name="purchase_plan_lines_selected_moq_positive",
        ),
        Index("ix_purchase_plan_lines_plan", "purchase_plan_id"),
        Index("ix_purchase_plan_lines_part", "part_id"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    purchase_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("purchase_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_entry_id = Column(UUID(as_uuid=True), nullable=True)
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    mpn_searched = Column(sa.String(255), nullable=False)
    required_qty = Column(sa.Integer, nullable=False)
    internal_available_qty = Column(sa.Integer, nullable=False)
    shortage_qty = Column(sa.Integer, nullable=False)
    selected_distributor = Column(sa.String(120), nullable=True)
    selected_qty = Column(sa.Integer, nullable=True)
    selected_unit_price = Column(sa.Numeric(18, 6), nullable=True)
    selected_currency = Column(sa.String(3), nullable=True)
    selected_packaging = Column(sa.String(120), nullable=True)
    selected_moq = Column(sa.Integer, nullable=True)
    selected_lead_time_days = Column(sa.Integer, nullable=True)
    selected_url = Column(sa.Text, nullable=True)
    risk_flags = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=sa.func.now(),
    )

    purchase_plan = relationship("PurchasePlan", back_populates="lines")
