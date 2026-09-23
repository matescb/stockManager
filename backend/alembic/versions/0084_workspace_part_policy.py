"""Workspace part policy: `workspaces.part_policy`.

The workspace, not the client tool, decides which fields a part must
carry — "every IC has a manufacturer", "every passive has a datasheet
before it is complete". Agents creating parts through the netsch CLI or
MCP read the policy and the structured `missing` list it produces
instead of each tool hard-coding its own idea of a complete part.
Validation, the rule shape and evaluation live in
`app/domain/part_policy/`; ADR-0035 records why it is enforced where it
is.

**Why a JSONB column on `workspaces` and not a rules table.** The policy
is a small document that is only ever read whole and replaced whole
(`PUT /api/part-policy`), and it belongs to exactly one workspace — the
same shape `sourcing_preferred_distributors` and the `active_*` lists
already use on this table. A rules table would add a join to every part
create for no query the application makes. The rule shape is validated
in Pydantic (`domain/part_policy/schemas.py`), which is where the field
vocabulary has to live anyway.

Nullable, no default, no backfill: NULL means "no rules", so every
existing workspace behaves exactly as before and the deploy is a no-op
until an admin writes a policy.

**Locks.** One `ADD COLUMN`, nullable with no default, so the table is
not rewritten; ACCESS EXCLUSIVE is held for the catalog update only.

Reversibility: total. `downgrade()` drops the column, which loses any
policy a workspace had written.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("part_policy", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "part_policy")
