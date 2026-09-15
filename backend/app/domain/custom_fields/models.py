from __future__ import annotations

from sqlalchemy import Column, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class CustomField(WorkspaceOwned, Base):
    __tablename__ = "custom_fields"
    __table_args__ = (
        UniqueConstraint("workspace_id", "object_type", "object_id", "key", name="uq_cf_unique"),
        Index("ix_cf_object", "workspace_id", "object_type", "object_id"),
        # (workspace_id, archived_at) partial composite added in
        # alembic 0018 (DB-004) for the universal active-row filter.
        Index(
            "ix_custom_fields_ws_archived",
            "workspace_id",
            "archived_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
        # Sort / range-filter a parsed spec in the database (alembic 0081).
        # Partial, because only rows the value parser could read carry a
        # number and they are a minority of the table.
        Index(
            "ix_custom_fields_ws_key_value_num",
            "workspace_id",
            "key",
            "value_num",
            postgresql_where=text("value_num IS NOT NULL"),
        ),
    )

    object_type = Column(String(40), nullable=False)
    object_id = Column(UUID(as_uuid=True), nullable=False)
    key = Column(String(256), nullable=False)
    value = Column(String(1024), nullable=True)
    # provider — supplied by an external data source (e.g. Mouser).
    # manual   — user-entered. The default for legacy and new manual rows.
    # override — user-edited a row that was originally `provider`. The
    #            upstream value is preserved in `original_value`.
    source = Column(String(20), nullable=False, default="manual")
    original_value = Column(String(1024), nullable=True)
    # Which provider wrote this row (alembic 0081). NULL for every manual
    # row and for every provider row predating the column. `source` says
    # *how* the row was written; this says *who*, which is what scopes a
    # refresh's delete pass once two providers can write the same canonical
    # key (ADR-0034). Names come from `provider_fields.KNOWN_PROVIDER_NAMES`;
    # the width matches `parts.linked_provider` and `workspaces.parts_provider`.
    provider = Column(String(40), nullable=True)
    # Numeric sidecar for a parsed spec, in the SI base unit — `10 kΩ` is
    # stored as `value="10 kΩ"` plus `value_num=10000`. NULL whenever the
    # value is not a single number (a package code, a temperature range) or
    # the parser refused it. `Numeric`, not float: a femtofarad and a
    # petaohm both have to be exact and in range, and a value that does not
    # fit is rounded by Postgres without complaint. See
    # `domain/parts/spec_values.py`.
    value_num = Column(Numeric(36, 18), nullable=True)
