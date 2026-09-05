"""SQLAlchemy model for ``print_jobs`` — the label-print ledger.

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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.infra.db import Base


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
