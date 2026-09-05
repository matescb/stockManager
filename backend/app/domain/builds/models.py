from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Build(WorkspaceOwned, Base):
    __tablename__ = "builds"
    __table_args__ = (
        Index("ix_builds_ws_status", "workspace_id", "status"),
        Index("ix_builds_ws_project", "workspace_id", "project_id"),
        Index("ix_builds_ws_archived", "workspace_id", "archived_at"),
    )

    name = Column(String(200), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="planned")  # planned|in_progress|complete|cancelled
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    output_lot_id = Column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), nullable=True)
    comments = Column(Text, nullable=True)


class BuildStage(WorkspaceOwned, Base):
    """One assembly stage of a multi-stage build (Track B2, migration 0075).

    A stage is a child of the ``Build`` aggregate, never of the project: two
    builds of the same project may be staged differently (prototype run vs.
    production run), and a stage's status is a property of *this* physical
    pass. The BOM subset a stage consumes lives in ``BuildStageLine`` rows.

    A build with **no** stages is the single-pass build that predates this
    table and behaves exactly as before — stages are purely additive.
    """

    __tablename__ = "build_stages"
    __table_args__ = (
        Index("ix_build_stages_ws_build", "workspace_id", "build_id"),
        Index("ix_build_stages_ws_archived", "workspace_id", "archived_at"),
        # Sequence is the consumption order and is unique per build among
        # active rows, so "stage 2" is unambiguous in the UI and in the
        # portion-allocation maths (which walks stages in sequence order).
        Index(
            "uq_build_stages_build_sequence",
            "build_id",
            "sequence",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        CheckConstraint(
            "status IN ('planned', 'in_progress', 'complete')",
            name="ck_build_stages_status",
        ),
        CheckConstraint("sequence >= 0", name="ck_build_stages_sequence_nonneg"),
    )

    build_id = Column(
        UUID(as_uuid=True), ForeignKey("builds.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(200), nullable=False)
    sequence = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="planned")  # planned|in_progress|complete
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    comments = Column(Text, nullable=True)


class BuildStageLine(WorkspaceOwned, Base):
    """The slice of one BOM line that a stage consumes (Track B2).

    ``portion_pct`` is a percentage of the BOM line's *whole-build*
    requirement — the attrition-adjusted integer that
    ``builds/service.py::_required`` returns. Stages therefore never
    re-derive quantities from ``project_entries.quantity``; they divide the
    one number planning, reservations and consumption already agree on.
    Portions across a build's stages for the same entry must sum to <= 100
    (enforced in ``builds/stages.py``).
    """

    __tablename__ = "build_stage_lines"
    __table_args__ = (
        Index("ix_build_stage_lines_ws_stage", "workspace_id", "build_stage_id"),
        Index("ix_build_stage_lines_ws_entry", "workspace_id", "project_entry_id"),
        Index(
            "uq_build_stage_lines_stage_entry",
            "build_stage_id",
            "project_entry_id",
            unique=True,
        ),
        # Strictly positive: a 0% line is just an absent line, and > 100%
        # would let one stage claim more than the whole-build requirement.
        CheckConstraint(
            "portion_pct > 0 AND portion_pct <= 100",
            name="ck_build_stage_lines_portion_range",
        ),
    )

    build_stage_id = Column(
        UUID(as_uuid=True), ForeignKey("build_stages.id", ondelete="CASCADE"), nullable=False
    )
    project_entry_id = Column(
        UUID(as_uuid=True), ForeignKey("project_entries.id", ondelete="CASCADE"), nullable=False
    )
    portion_pct = Column(Numeric(7, 4), nullable=False, server_default="100", default=100)
