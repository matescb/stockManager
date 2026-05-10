"""Global FX reference-rate snapshots."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import Column, Date, DateTime
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.time import utcnow
from app.infra.db import Base


class FxRateSnapshot(Base):
    """One ECB daily reference-rate snapshot per UTC date.

    ECB reference rates are public global data, so this table deliberately
    does not carry workspace_id.
    """

    __tablename__ = "fx_rate_snapshots"
    __table_args__ = (
        sa.UniqueConstraint("fetched_date", name="uq_fx_rate_snapshots_fetched_date"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    fetched_date = Column(Date, nullable=False)
    rates = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=sa.func.now(),
    )

