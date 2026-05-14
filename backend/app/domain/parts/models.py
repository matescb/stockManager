from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Part(WorkspaceOwned, Base):
    __tablename__ = "parts"
    __table_args__ = (
        Index("ix_parts_ws_name", "workspace_id", "name"),
        # MPN is unique per workspace where present and active — see
        # alembic 0011. The partial predicate excludes NULL mpn rows
        # (manual / sub-assembly parts) and archived rows (archiving
        # frees up the MPN so a replacement can take over).
        Index(
            "uq_parts_ws_mpn",
            "workspace_id",
            "mpn",
            unique=True,
            postgresql_where=text("mpn IS NOT NULL AND archived_at IS NULL"),
        ),
        Index("ix_parts_ws_ipn", "workspace_id", "internal_part_number"),
        Index("ix_parts_ws_archived", "workspace_id", "archived_at"),
        # pg_trgm GIN indexes for ILIKE %q% search (alembic 0018, BE2-018).
        # Single-column GIN; planner bitmap-ANDs with the (workspace_id,
        # archived_at) btree above for the per-workspace filter.
        Index(
            "ix_parts_ws_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_parts_ws_mpn_trgm",
            "mpn",
            postgresql_using="gin",
            postgresql_ops={"mpn": "gin_trgm_ops"},
        ),
    )

    # linked|local|meta|sub_assembly
    part_type = Column(String(20), nullable=False, default="local")
    name = Column(String(300), nullable=False)
    internal_part_number = Column(String(120), nullable=True)
    manufacturer = Column(String(200), nullable=True)
    mpn = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    notes_markdown = Column(Text, nullable=True)
    footprint = Column(String(120), nullable=True)
    linked_external_id = Column(String(200), nullable=True)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    low_stock_report_quantity = Column(Integer, nullable=True)
    attrition_percentage = Column(Numeric(8, 4), nullable=False, default=0)
    attrition_min_quantity = Column(Integer, nullable=False, default=0)
    default_storage_location_id = Column(
        UUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="SET NULL"), nullable=True
    )
    default_storage_mandatory = Column(Boolean, nullable=False, default=False)
    serialized = Column(Boolean, nullable=False, default=False)
    published = Column(Boolean, nullable=False, default=False)
    # Provider linkage. linked_provider names which workspace-configured
    # data source owns the canonical fields (manufacturer/mpn/description);
    # linked_external_id (declared above) holds the upstream identifier
    # (e.g. Mouser's ManufacturerPartNumber after lookup). last_refresh_at
    # is updated on every successful provider fetch;
    # description_locally_edited flips true when a user edits the
    # description on a linked part so that subsequent refreshes won't
    # overwrite it.
    linked_provider = Column(String(40), nullable=True)
    last_refresh_at = Column(DateTime(timezone=True), nullable=True)
    description_locally_edited = Column(Boolean, nullable=False, default=False)


class PartCadKey(Base):
    __tablename__ = "part_cad_keys"
    __table_args__ = (
        Index("ix_part_cad_keys_ws_cad_key", "workspace_id", "cad_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cad_key = Column(String(300), nullable=False, index=True)
    source = Column(String(40), nullable=False, default="manual")


class PartMetaMember(Base):
    __tablename__ = "part_meta_members"
    __table_args__ = (
        UniqueConstraint("meta_part_id", "part_id", name="uq_meta_member"),
        Index("ix_part_meta_members_ws_meta", "workspace_id", "meta_part_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    meta_part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class PartSubstitute(Base):
    __tablename__ = "part_substitutes"
    __table_args__ = (
        UniqueConstraint("part_id", "substitute_part_id", name="uq_part_sub"),
        Index("ix_part_substitutes_ws_part", "workspace_id", "part_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    substitute_part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction = Column(String(20), nullable=False, default="bidirectional")


class BulkImportIdempotency(Base):
    """Idempotency cache for bulk-import-from-scan (BE2-003).

    Keyed on (workspace_id, key) — a composite PK that enforces workspace
    isolation at the DB level even though application code already filters
    by workspace_id. `result_json` holds the full API envelope so a cache
    hit can be returned verbatim without re-running any logic.

    TTL: rows older than 24 h are swept best-effort at the start of each
    request. This keeps the table bounded without a background cron job.
    """
    __tablename__ = "bulk_import_idempotency"
    __table_args__ = (
        Index("ix_bulk_import_idempotency_ws_created", "workspace_id", "created_at"),
    )

    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    # SHA-256 hex of (workspace_id + sorted row contents), or client-supplied UUID4.
    key = Column(String(64), primary_key=True, nullable=False)
    result_json = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
