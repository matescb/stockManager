from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.parts.models import Part
from app.domain.sourcing.models import SourcingAlert
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember


def test_updated_by_persists_through_orm(db: Session) -> None:
    user = User(
        email=f"sourcing-alert-{uuid.uuid4().hex[:8]}@example.com",
        name="Sourcing Alert Tester",
        password_hash="test",
    )
    db.add(user)
    db.flush()

    workspace = Workspace(
        name=f"sourcing-alerts-{uuid.uuid4().hex[:8]}",
        kind="organization",
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
            status="active",
        )
    )

    part = Part(
        workspace_id=workspace.id,
        name=f"Part {uuid.uuid4().hex[:8]}",
        part_type="local",
        mpn=f"MPN-{uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(part)
    db.flush()

    alert = SourcingAlert(
        workspace_id=workspace.id,
        part_id=part.id,
        alert_type="stock_below",
        threshold={"qty": 5},
        notify_user_ids=[str(user.id)],
        cooldown_seconds=60,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(alert)
    db.flush()

    alert_id = alert.id
    db.expunge(alert)

    reloaded = db.execute(select(SourcingAlert).where(SourcingAlert.id == alert_id)).scalar_one()

    assert reloaded.updated_by == user.id
