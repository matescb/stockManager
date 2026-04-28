from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Part(WorkspaceOwned, Base):
    __tablename__ = "parts"
    __table_args__ = (
        Index("ix_parts_ws_name", "workspace_id", "name"),
        Index("ix_parts_ws_mpn", "workspace_id", "manufacturer", "mpn"),
        Index("ix_parts_ws_ipn", "workspace_id", "internal_part_number"),
        Index("ix_parts_ws_archived", "workspace_id", "archived_at"),
    )

    part_type = Column(String(20), nullable=False, default="local")  # linked|local|meta|sub_assembly
    name = Column(String(300), nullable=False)
    internal_part_number = Column(String(120), nullable=True)
    manufacturer = Column(String(200), nullable=True)
    mpn = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    notes_markdown = Column(Text, nullable=True)
    footprint = Column(String(120), nullable=True)
    linked_external_id = Column(String(200), nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    low_stock_report_quantity = Column(Integer, nullable=True)
    attrition_percentage = Column(Numeric(8, 4), nullable=False, default=0)
    attrition_min_quantity = Column(Integer, nullable=False, default=0)
    default_storage_location_id = Column(
        UUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="SET NULL"), nullable=True
    )
    default_storage_mandatory = Column(Boolean, nullable=False, default=False)
    published = Column(Boolean, nullable=False, default=False)


class PartCadKey(Base):
    __tablename__ = "part_cad_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    cad_key = Column(String(300), nullable=False, index=True)
    source = Column(String(40), nullable=False, default="manual")


class PartMetaMember(Base):
    __tablename__ = "part_meta_members"
    __table_args__ = (
        UniqueConstraint("meta_part_id", "part_id", name="uq_meta_member"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meta_part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)


class PartSubstitute(Base):
    __tablename__ = "part_substitutes"
    __table_args__ = (
        UniqueConstraint("part_id", "substitute_part_id", name="uq_part_sub"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    substitute_part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    direction = Column(String(20), nullable=False, default="bidirectional")
