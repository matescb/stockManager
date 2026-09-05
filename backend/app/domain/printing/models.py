"""SQLAlchemy models for the label-printing domain.

Two tables live here:

* ``print_jobs`` — the print ledger (below).
* ``label_templates`` — the reusable label layouts the renderer turns into
  JScript (:class:`LabelTemplate`, at the bottom of this module).

``print_jobs`` — the label-print ledger.

Adapted from the sibling skladVA project's ``print_job`` model
(/mnt/data/WORK/sklad, ``backend/app/domain/print_job/models.py``). A physical
print and a DB write cannot be one atomic transaction, so every print attempt
is recorded as a row and reconciled through its lifecycle rather than being
treated as falsely "atomic":

    queued -> sent -> printed | failed

Differences from skladVA (single-tenant) — this project is MULTI-TENANT:

* ``workspace_id`` (FK ``workspaces.id`` ON DELETE CASCADE) scopes every row to
  a workspace, and ``idempotency_key`` is unique **per workspace** (partial? no
  — a plain composite unique index), so two workspaces may reuse the same key
  independently. Workspace isolation is code-enforced in
  :mod:`app.domain.printing.print_service`; the composite unique index is the
  only DB-level scoping.
* ``target_type`` / ``target_id`` replace skladVA's single ``item_id`` FK. They
  are a *polymorphic, un-constrained* pointer (no FK — like this codebase's
  ``attachments`` / ``custom_fields`` / ``tag_links`` ``object_id``): a print
  job may target a part, a lot, a storage location or a future object-code, and
  the resolver that interprets them ships in a later PR. Kept nullable so a
  blank/batch label with no target is representable.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.domain._mixins import WorkspaceOwned
from app.domain.codes.models import CODE_ENTITY_TYPES
from app.infra.db import Base

# Element kinds a template may place. The single source of truth shared by
# the renderer (``label_render``) and the API validation layer — a kind the
# renderer cannot draw must not be storable.
ELEMENT_KINDS: tuple[str, ...] = ("qr", "text", "barcode1d", "handwriting")

# A label targets one of the five CODEABLE entity types, deliberately the same
# closed set as ``object_codes`` (#892) rather than a second list: every label
# carries that object's code, so a type you cannot mint a code for is a type
# you cannot label. Imported, not re-declared, so the two cannot drift.
LABEL_ENTITY_TYPES: tuple[str, ...] = CODE_ENTITY_TYPES

_LABEL_ENTITY_TYPE_CHECK = "entity_type IN (" + ", ".join(
    f"'{value}'" for value in LABEL_ENTITY_TYPES
) + ")"


class PrintJob(Base):
    __tablename__ = "print_jobs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('on_demand', 'batch_blank')",
            name="ck_print_jobs_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'sent', 'printed', 'failed')",
            name="ck_print_jobs_status",
        ),
        # idempotency_key is unique PER WORKSPACE (not globally): a retried
        # print within a workspace dedupes to one job, while a different
        # workspace may legitimately reuse the same key.
        Index(
            "uq_print_jobs_ws_idem",
            "workspace_id",
            "idempotency_key",
            unique=True,
        ),
        # Supports the maintenance sweeps: dispatch (status='queued') and
        # reconcile (status='sent').
        Index("ix_print_jobs_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # CASCADE: a deleted workspace takes its print history with it.
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Polymorphic, un-constrained target pointer (no FK — see module docstring).
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rendered JScript for a deferred (background-dispatched) job. Set when the
    # job is enqueued ``queued`` and shipped later by the cron dispatcher; NULL
    # for synchronous jobs that render at send time.
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # onupdate bumps updated_at on every lifecycle transition so the reconcile
    # sweep can measure "stuck in sent" from when it went to sent, not from
    # creation. utcnow (client-side) matches the WorkspaceOwned mixin.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=utcnow,
    )


class LabelTemplate(WorkspaceOwned, Base):
    """A reusable label layout: stock geometry + a list of placed elements.

    Adapted from the sibling skladVA project's ``label_template`` model
    (/mnt/data/WORK/sklad, ``backend/app/domain/label_template/models.py``),
    made workspace-scoped — skladVA is single-tenant, so its "one default per
    entity type" was a global rule; here it is per workspace.

    Stock geometry (``width_mm`` / ``height_mm`` / ``gap_mm`` / ``heat`` /
    ``speed`` / ``method`` / ``dpi``) lives in dedicated columns so
    :mod:`app.domain.printing.label_render` can build the JScript ``H`` / ``S``
    job header without parsing the element blob. The variable part — the placed
    elements — is a single ``elements`` JSONB list: it is read and written
    whole by the renderer and (later) the designer and never queried
    element-by-element, so a document column is the right shape rather than a
    child table.

    One default per (workspace, entity type)
    ----------------------------------------
    ``is_default`` marks the template the print flows pick when no explicit
    template is named. At most one row per ``(workspace_id, entity_type)`` may
    be default, enforced by the PARTIAL unique index
    ``uq_label_templates_ws_default ... WHERE is_default``. Partial, so any
    number of NON-default templates per type coexist. Promoting a template
    demotes the incumbent inside the same transaction (see
    ``template_service.clear_existing_default``) — a bare flip would hit the
    index.
    """

    __tablename__ = "label_templates"
    __table_args__ = (
        CheckConstraint(
            _LABEL_ENTITY_TYPE_CHECK, name="ck_label_templates_entity_type"
        ),
        CheckConstraint("method IN ('T', 'D')", name="ck_label_templates_method"),
        # At most one default per (workspace, entity_type). PARTIAL: the
        # uniqueness only binds rows where is_default is true.
        Index(
            "uq_label_templates_ws_default",
            "workspace_id",
            "entity_type",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        # The list endpoint's access path: templates of one type in one
        # workspace, ordered by name.
        Index("ix_label_templates_ws_entity", "workspace_id", "entity_type"),
    )

    name = Column(String(200), nullable=False)
    entity_type = Column(String(40), nullable=False)

    width_mm = Column(Numeric(6, 2), nullable=False)
    height_mm = Column(Numeric(6, 2), nullable=False)
    gap_mm = Column(Numeric(6, 2), nullable=False, server_default=text("3"))
    heat = Column(Integer, nullable=False, server_default=text("100"))
    speed = Column(Integer, nullable=False, server_default=text("0"))
    # 'T' = thermal transfer (ribbon), 'D' = direct thermal.
    method = Column(String(1), nullable=False, server_default=text("'T'"))
    dpi = Column(Integer, nullable=False, server_default=text("300"))
    is_default = Column(Boolean, nullable=False, server_default=text("false"))
    elements = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
