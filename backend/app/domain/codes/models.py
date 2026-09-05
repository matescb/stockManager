"""SQLAlchemy model for ``object_codes`` — the universal short-code table.

Every scannable object in a workspace (a part, a lot, a storage location,
an order, a build) can carry one short human-readable code. Scanning the
code — off a printed label, or typed by hand — resolves back to exactly
that object. This is the data half of PartsBox's "ID-Anything"; the label
rendering and printing sit in a later PR and read this table.

Why one central polymorphic table rather than a ``code`` column on five
tables:

* One uniqueness scope. ``UNIQUE (workspace_id, code)`` makes "is this
  code taken?" a single index probe. Five per-table columns would need a
  five-way check on every mint and could still collide across tables.
* One resolver. ``GET /api/codes/{code}`` is a single query; the
  alternative is a UNION over every codeable table, growing with each new
  entity type.
* Lazy minting. Most rows never get a code — only the ones someone
  labels. A nullable column on five hot tables pays for that everywhere;
  a side table pays only for the rows that exist.

The trade-off is the same one ``attachments`` / ``custom_fields`` /
``tag_links`` make: ``entity_id`` carries **no FK**, because a single FK
would bind it to one parent table. Hard-delete cleanup is therefore the
application's job — ``domain/_polymorphic_cleanup.py`` registers
``object_codes`` alongside the other three, so deleting a part takes its
code with it (CLAUDE.md, "Polymorphic cleanup on hard delete").

``entity_type`` is additionally pinned by a CHECK constraint. Unlike the
other polymorphic tables, the codeable set is deliberately closed: a code
is a physical-world handle, and only things that exist physically (or as
a purchase/production batch) get one. ``project`` is intentionally absent
— you do not stick a label on a project.
"""

from __future__ import annotations

import datetime
from typing import Literal, get_args

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base

# The closed set of codeable entity types. Kept as a `Literal` so the
# request schema, the CHECK constraint and the runtime allow-list all
# derive from one definition — adding a type means editing this line and
# writing a migration, and nothing else can drift.
CodeEntityType = Literal["build", "lot", "order", "part", "storage_location"]

CODE_ENTITY_TYPES: tuple[str, ...] = get_args(CodeEntityType)

# Rendered into the CHECK constraint in both the model and alembic 0073.
ENTITY_TYPE_CHECK = "entity_type IN (" + ", ".join(
    f"'{value}'" for value in CODE_ENTITY_TYPES
) + ")"

# Column width. Codes are 8 chars today (see `service.CODE_LENGTH`); the
# slack lets the length grow without a column migration.
CODE_MAX_LENGTH = 16


class ObjectCode(Base):
    __tablename__ = "object_codes"
    __table_args__ = (
        CheckConstraint(ENTITY_TYPE_CHECK, name="ck_object_codes_entity_type"),
        # The resolver's index: a code is unique within a workspace, so two
        # workspaces may independently mint the same string. Scoping to the
        # workspace keeps the code short — a global namespace would need
        # more entropy for the same collision odds.
        UniqueConstraint("workspace_id", "code", name="uq_object_codes_ws_code"),
        # One code per object, forever. Minting is get-or-create, and this
        # constraint is what makes that safe under concurrency: two parallel
        # mints for the same object race to the same row, and the loser
        # re-reads the winner's code instead of printing a second label.
        UniqueConstraint(
            "workspace_id",
            "entity_type",
            "entity_id",
            name="uq_object_codes_ws_entity",
        ),
        Index("ix_object_codes_workspace_id", "workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # CASCADE: a deleted workspace takes its codes with it.
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Polymorphic, un-constrained pointer — no FK (see module docstring).
    entity_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(CODE_MAX_LENGTH), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
