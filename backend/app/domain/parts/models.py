from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.domain._mixins import WorkspaceOwned
from app.domain._quantity import DEFAULT_UNIT, UNIT_CODE_MAX_LENGTH
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
        Index(
            "ix_parts_category_id",
            "category_id",
            postgresql_where=text("category_id IS NOT NULL"),
        ),
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
    # Optional bucket the part belongs to. SET NULL rather than CASCADE: a
    # category going away must never take its parts with it.
    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("part_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Numeric(18,6) since alembic 0074 (units-of-measure step 1) so these
    # stay comparable with the widened ledger. Integer-only on the wire.
    low_stock_report_quantity = Column(Numeric(18, 6), nullable=True)
    attrition_percentage = Column(Numeric(8, 4), nullable=False, default=0)
    attrition_min_quantity = Column(Numeric(18, 6), nullable=False, default=0)
    # The part's canonical unit (alembic 0074). Not user-settable yet — no
    # route reads or writes it — but every ledger row stamps its own copy
    # (uom step 3) so history can never be reinterpreted by editing it.
    # Frozen once the part has any ledger row: the
    # `parts_unit_of_measure_change_check` trigger (alembic 0077) refuses
    # the UPDATE, because an append-only ledger keeps its old stamps
    # forever and a part whose ledger mixes units has no meaningful
    # `SUM(quantity_delta)`. Zero the stock out, change the unit, re-add.
    unit_of_measure = Column(
        String(UNIT_CODE_MAX_LENGTH),
        nullable=False,
        server_default=DEFAULT_UNIT,
        default=DEFAULT_UNIT,
    )
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


class WorkspaceProviderCredential(WorkspaceOwned, Base):
    """Per-workspace credentials for one SECONDARY parts provider.

    `workspaces.parts_provider_api_key` / `_api_secret` stay where they
    are and remain the PRIMARY provider's only store — this table is what
    lets a workspace configure a *second* provider alongside it, and
    holds nothing else. Migration 0070 backfills no rows and the PUT
    route refuses a payload naming the workspace's own `parts_provider`,
    so no provider ever has a key in both places (see
    `provider_credentials.py`).

    Both value columns hold Fernet ciphertext from `core/secrets.py`,
    never plaintext. Providers that need one credential (Mouser's search
    key) leave `api_secret_encrypted` NULL; DigiKey uses both as
    client_id / client_secret.
    """

    __tablename__ = "workspace_provider_credentials"
    __table_args__ = (
        # One active row per (workspace, provider). Archiving frees the
        # slot so a cleared credential can be re-added later without
        # colliding with its own tombstone.
        Index(
            "uq_workspace_provider_credentials_ws_provider",
            "workspace_id",
            "provider",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    provider = Column(String(40), nullable=False)
    api_key_encrypted = Column(Text, nullable=True)
    api_secret_encrypted = Column(Text, nullable=True)


class PartProviderLink(WorkspaceOwned, Base):
    """One part's link to one provider's catalog entry.

    Written for the primary provider too (alongside `parts.linked_*`,
    which stays the primary's source of truth for the part columns) so
    this table alone answers "which providers know this part".
    """

    __tablename__ = "part_provider_links"
    __table_args__ = (
        Index(
            "uq_part_provider_links_part_provider",
            "part_id",
            "provider",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_part_provider_links_ws_provider", "workspace_id", "provider"),
    )

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(40), nullable=False)
    external_id = Column(String(300), nullable=True)
    source_url = Column(String(500), nullable=True)
    last_refresh_at = Column(DateTime(timezone=True), nullable=True)


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
