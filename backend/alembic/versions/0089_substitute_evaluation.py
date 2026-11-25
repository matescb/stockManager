"""Relation and evaluation state on substitute links (SM-012).

Five additive columns on `part_substitutes`:

* `relation` — `pin-compatible` | `footprint-compatible` | `functional`,
  NULL for "not stated" (every existing link).
* `evaluation` — `not-evaluated` | `evaluated-ok` | `rejected`, NOT NULL
  with `server_default 'not-evaluated'`, so every existing link reads as
  not evaluated without a data rewrite.
* `evidence` — free reference that justified the evaluation (e.g. a
  simulation id such as "SIM-012").
* `evaluated_by` / `evaluated_at` — who last changed relation,
  evaluation or evidence, and when. `ON DELETE SET NULL` on the user so
  removing an account keeps the evaluation.

CHECK constraints pin the two vocabularies.

**Additive only.** No existing column is touched; `direction` keeps its
meaning. The only value written into existing rows is the server default.

**Locks.** `ADD COLUMN ... DEFAULT <constant>` is metadata-only on
Postgres 11+ (no table rewrite). The CHECK constraints are added
`NOT VALID` and then validated, so validation runs under SHARE UPDATE
EXCLUSIVE rather than holding ACCESS EXCLUSIVE for the scan. The FK to
`users` takes SHARE ROW EXCLUSIVE on `users` for the instant of the DDL.
The table is small (one row per substitute link).

The workspace BEFORE trigger from 0053 checks only `part_id` /
`substitute_part_id`; these columns do not reference workspace-owned
rows, so it needs no change.

Reversibility: total — `downgrade()` drops the constraints and columns
(and with them any recorded evaluations).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

_TABLE = "part_substitutes"
_CK_RELATION = "ck_part_substitutes_relation"
_CK_EVALUATION = "ck_part_substitutes_evaluation"
_FK_EVALUATED_BY = "fk_part_substitutes_evaluated_by"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("relation", sa.String(30), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "evaluation",
            sa.String(20),
            nullable=False,
            server_default="not-evaluated",
        ),
    )
    op.add_column(_TABLE, sa.Column("evidence", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("evaluated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        _FK_EVALUATED_BY, _TABLE, "users", ["evaluated_by"], ["id"], ondelete="SET NULL"
    )
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CK_RELATION} CHECK "
        "(relation IS NULL OR relation IN "
        "('pin-compatible', 'footprint-compatible', 'functional')) NOT VALID"
    )
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CK_EVALUATION} CHECK "
        "(evaluation IN ('not-evaluated', 'evaluated-ok', 'rejected')) NOT VALID"
    )
    op.execute(f"ALTER TABLE {_TABLE} VALIDATE CONSTRAINT {_CK_RELATION}")
    op.execute(f"ALTER TABLE {_TABLE} VALIDATE CONSTRAINT {_CK_EVALUATION}")


def downgrade() -> None:
    op.drop_constraint(_CK_EVALUATION, _TABLE, type_="check")
    op.drop_constraint(_CK_RELATION, _TABLE, type_="check")
    op.drop_constraint(_FK_EVALUATED_BY, _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "evaluated_at")
    op.drop_column(_TABLE, "evaluated_by")
    op.drop_column(_TABLE, "evidence")
    op.drop_column(_TABLE, "evaluation")
    op.drop_column(_TABLE, "relation")
