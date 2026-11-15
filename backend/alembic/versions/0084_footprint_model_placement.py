"""`eda_footprint_models.placement`: where a linked 3D model sits.

A footprint's own `(model …)` node carries `offset` / `rotate` /
`scale`. A model linked on the CAD tab has no node, so the KiCad package
appended it at the identity placement — and SnapEDA STEPs, typically
Y-up and centred on their own origin, then stood on edge half inside the
board (14 of 19 such links in production). The placement now lives on the
link row: `{"offset": [x, y, z], "rotate": [rx, ry, rz], "scale": [sx,
sy, sz]}`, mm and degrees, in KiCad's 3D frame. It is filled from the
model's geometry (`domain/eda/model_fit.py`) when a link is made, and by
the operator-run `model-placement-fit` job for links that predate this.

NULL means "no placement known": the package keeps appending such a
model at the identity, exactly as before, so the deploy changes nothing
until a placement is written. A footprint whose own bytes already place
the model ignores the column — the vendor's node wins.

**Why JSONB.** One small fixed-shape record read and written whole,
never queried into; same reasoning as 0082/0083's JSONB columns. The
shape is validated in `model_fit.Placement.from_json`, which reads
anything malformed as NULL rather than failing the package build.

**Locks.** One nullable `ADD COLUMN` with no default: a catalog-only
change, no table rewrite.

Reversibility: total. `downgrade()` drops the column; packages go back to
the identity placement for linked models.
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
        "eda_footprint_models",
        sa.Column("placement", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eda_footprint_models", "placement")
