"""Constrain workspace member roles.

Revision ID: 0048
Revises: 0047
Create Date: 2026-05-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_CK_WORKSPACE_MEMBERS_ROLE = "ck_workspace_members_role"
_VALID_ROLES = ("owner", "admin", "member", "viewer")


def upgrade() -> None:
    bind = op.get_bind()
    invalid_rows = bind.execute(
        sa.text(
            "SELECT count(*) FROM workspace_members "
            "WHERE role NOT IN :valid_roles"
        ).bindparams(sa.bindparam("valid_roles", expanding=True)),
        {"valid_roles": _VALID_ROLES},
    ).scalar_one()
    if invalid_rows:
        raise RuntimeError(
            "Cannot add ck_workspace_members_role while "
            f"{invalid_rows} workspace_members row(s) have invalid roles. "
            "Normalize roles to owner, admin, member, or viewer first."
        )

    op.create_check_constraint(
        _CK_WORKSPACE_MEMBERS_ROLE,
        "workspace_members",
        f"role IN {_VALID_ROLES!r}",
    )


def downgrade() -> None:
    op.drop_constraint(
        _CK_WORKSPACE_MEMBERS_ROLE,
        "workspace_members",
        type_="check",
    )
