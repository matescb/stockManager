from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.sourcing.models import SourcingAlert
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace


def _workspace_user(db: Session) -> tuple[Workspace, User]:
    user = User(
        email=f"sourcing-alert-{uuid.uuid4().hex[:8]}@example.com",
        name="Sourcing Alert Tester",
        password_hash="test",
    )
    db.add(user)
    db.flush()
    workspace = Workspace(
        name=f"sourcing-alert-ws-{uuid.uuid4().hex[:8]}",
        kind="organization",
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    return workspace, user


def _part(db: Session, workspace: Workspace, user: User, *, name: str = "STM32") -> Part:
    part = Part(
        workspace_id=workspace.id,
        name=f"{name} {uuid.uuid4().hex[:8]}",
        part_type="local",
        mpn=f"{name}-{uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(part)
    db.flush()
    return part


def _project(db: Session, workspace: Workspace, user: User) -> Project:
    project = Project(
        workspace_id=workspace.id,
        name=f"Alert BOM {uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(project)
    db.flush()
    return project


def _alert(
    workspace: Workspace,
    user: User,
    *,
    part: Part | None = None,
    project: Project | None = None,
    alert_type: str = "stock_below",
    threshold: dict | None = None,
    archived: bool = False,
    cooldown_seconds: int = 86400,
) -> SourcingAlert:
    return SourcingAlert(
        workspace_id=workspace.id,
        part_id=part.id if part is not None else None,
        project_id=project.id if project is not None else None,
        alert_type=alert_type,
        threshold=threshold if threshold is not None else {"qty": 10},
        cooldown_seconds=cooldown_seconds,
        archived_at=utcnow() - timedelta(minutes=1) if archived else None,
        created_by=user.id,
    )


def test_check_constraint_rejects_invalid_alert_type(db: Session) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    db.add(_alert(workspace, user, part=part, alert_type="bogus"))

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


@pytest.mark.parametrize(
    "alert_type",
    [
        "lifecycle_risk_changed",
        "supply_chain_risk_changed",
        "tariff_status_changed",
    ],
)
def test_check_constraint_accepts_gap_field_alert_types(
    db: Session,
    alert_type: str,
) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    db.add(_alert(workspace, user, part=part, alert_type=alert_type, threshold={}))

    db.flush()

    assert db.execute(select(func.count()).select_from(SourcingAlert)).scalar_one() == 1


def test_check_constraint_rejects_short_cooldown(db: Session) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    db.add(_alert(workspace, user, part=part, cooldown_seconds=10))

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_xor_part_id_project_id_check(db: Session) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    project = _project(db, workspace, user)
    db.add(_alert(workspace, user, part=part, project=project))

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    workspace, user = _workspace_user(db)
    db.add(_alert(workspace, user))

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    project = _project(db, workspace, user)
    db.add_all(
        [
            _alert(workspace, user, part=part),
            _alert(
                workspace,
                user,
                project=project,
                alert_type="bom_buyable",
                threshold={"build_quantity": 10},
            ),
        ]
    )
    db.flush()


def test_partial_unique_active_alert(db: Session) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    db.add_all(
        [
            _alert(workspace, user, part=part, threshold={"qty": 25}),
            _alert(workspace, user, part=part, threshold={"qty": 25}),
        ]
    )

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_partial_unique_allows_archived_duplicate(db: Session) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    db.add_all(
        [
            _alert(workspace, user, part=part, threshold={"qty": 25}, archived=True),
            _alert(workspace, user, part=part, threshold={"qty": 25}),
        ]
    )
    db.flush()

    assert db.execute(select(func.count()).select_from(SourcingAlert)).scalar_one() == 2


def test_cascade_delete_workspace(db: Session) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user)
    db.add(_alert(workspace, user, part=part))
    db.flush()

    db.delete(workspace)
    db.flush()

    assert db.execute(select(func.count()).select_from(SourcingAlert)).scalar_one() == 0


def test_cascade_delete_project(db: Session) -> None:
    workspace, user = _workspace_user(db)
    project = _project(db, workspace, user)
    db.add(
        _alert(
            workspace,
            user,
            project=project,
            alert_type="bom_buyable",
            threshold={"build_quantity": 10},
        )
    )
    db.flush()

    db.delete(project)
    db.flush()

    assert db.execute(select(func.count()).select_from(SourcingAlert)).scalar_one() == 0


def test_cascade_delete_part_nulls_other_alerts(db: Session) -> None:
    workspace, user = _workspace_user(db)
    part = _part(db, workspace, user, name="DeleteMe")
    other_part = _part(db, workspace, user, name="KeepMe")
    db.add_all(
        [
            _alert(workspace, user, part=part, threshold={"qty": 5}),
            _alert(workspace, user, part=other_part, threshold={"qty": 7}),
        ]
    )
    db.flush()

    db.delete(part)
    db.flush()

    remaining = db.execute(select(SourcingAlert)).scalar_one()
    assert remaining.part_id == other_part.id
    assert remaining.threshold == {"qty": 7}
