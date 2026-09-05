"""Add print_jobs (label-print ledger).

The label-printing foundation: a workspace-scoped ledger of every cab SQUIX
print attempt. A physical print and a DB write cannot be one atomic
transaction, so each attempt is a row walking queued -> sent -> printed | failed
and is reconciled rather than treated as falsely atomic. Adapted from the
sibling skladVA project's single-tenant `print_job` table, made multi-tenant:
`workspace_id` scopes every row and `idempotency_key` is unique per workspace.

Revision ID: 0071
Revises: 0070
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "print_jobs",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        # Polymorphic, un-constrained target pointer (no FK — like attachments /
        # custom_fields / tag_links object_id). The resolver ships in a later PR.
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        # Rendered JScript for a deferred (background-dispatched) job; NULL for
        # synchronous jobs that render at send time.
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('on_demand', 'batch_blank')",
            name="ck_print_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'printed', 'failed')",
            name="ck_print_jobs_status",
        ),
        # CASCADE: a deleted workspace takes its print history with it.
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_print_jobs_workspace_id", "print_jobs", ["workspace_id"])
    # idempotency_key is unique PER WORKSPACE: a retried print within a
    # workspace dedupes to one job, while a different workspace may reuse the
    # same key.
    op.create_index(
        "uq_print_jobs_ws_idem",
        "print_jobs",
        ["workspace_id", "idempotency_key"],
        unique=True,
    )
    # Supports the maintenance sweeps: dispatch (status='queued') and reconcile
    # (status='sent').
    op.create_index("ix_print_jobs_status", "print_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_print_jobs_status", table_name="print_jobs")
    op.drop_index("uq_print_jobs_ws_idem", table_name="print_jobs")
    op.drop_index("ix_print_jobs_workspace_id", table_name="print_jobs")
    op.drop_table("print_jobs")
