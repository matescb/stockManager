from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.parts.models import Part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.sourcing.models import PurchasePlan, PurchasePlanLine
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace


def test_parent_delete_sets_null(db: Session) -> None:
    user = User(
        email=f"purchase-plan-fk-{uuid.uuid4().hex[:8]}@example.com",
        name="Purchase Plan FK Tester",
        password_hash="test",
    )
    db.add(user)
    db.flush()

    workspace = Workspace(
        name=f"purchase-plan-fk-ws-{uuid.uuid4().hex[:8]}",
        kind="organization",
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()

    project = Project(
        workspace_id=workspace.id,
        name=f"FK BOM {uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    part = Part(
        workspace_id=workspace.id,
        name=f"FK Part {uuid.uuid4().hex[:8]}",
        part_type="local",
        mpn=f"FK-{uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add_all([project, part])
    db.flush()

    entry = ProjectEntry(
        workspace_id=workspace.id,
        project_id=project.id,
        entry_type="part",
        part_id=part.id,
        name=part.name,
        quantity=5,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(entry)
    db.flush()

    now = utcnow()
    plan = PurchasePlan(
        workspace_id=workspace.id,
        project_id=project.id,
        build_quantity=1,
        strategy="lowest_total_price",
        status="draft",
        created_at=now,
        expires_at=now + timedelta(days=1),
        created_by=user.id,
    )
    line = PurchasePlanLine(
        project_entry_id=entry.id,
        part_id=part.id,
        mpn_searched=part.mpn,
        required_qty=5,
        internal_available_qty=0,
        shortage_qty=5,
        risk_flags=[],
    )
    plan.lines.append(line)
    db.add(plan)
    db.flush()
    line_id = line.id

    db.delete(entry)
    db.flush()
    db.expire_all()

    persisted = db.get(PurchasePlanLine, line_id)
    assert persisted is not None
    assert persisted.project_entry_id is None
