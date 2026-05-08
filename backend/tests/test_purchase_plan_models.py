from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.parts.models import Part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.sourcing.models import PurchasePlan, PurchasePlanLine
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace


def _workspace_user(db: Session) -> tuple[Workspace, User]:
    user = User(
        email=f"purchase-plan-{uuid.uuid4().hex[:8]}@example.com",
        name="Purchase Plan Tester",
        password_hash="test",
    )
    db.add(user)
    db.flush()
    workspace = Workspace(
        name=f"purchase-plan-ws-{uuid.uuid4().hex[:8]}",
        kind="organization",
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    return workspace, user


def _project_and_part(db: Session) -> tuple[Workspace, User, Project, ProjectEntry, Part]:
    workspace, user = _workspace_user(db)
    project = Project(
        workspace_id=workspace.id,
        name=f"Optimizer BOM {uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    part = Part(
        workspace_id=workspace.id,
        name=f"STM32 {uuid.uuid4().hex[:8]}",
        part_type="local",
        mpn="STM32F103C8T6",
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
    return workspace, user, project, entry, part


def _purchase_plan(
    db: Session,
    *,
    strategy: str = "lowest_total_price",
    status: str = "draft",
    line_kwargs: dict | None = None,
    created_delta: timedelta = timedelta(),
    expires_delta: timedelta = timedelta(days=1),
) -> PurchasePlan:
    workspace, user, project, entry, part = _project_and_part(db)
    created_at = utcnow() + created_delta
    plan = PurchasePlan(
        workspace_id=workspace.id,
        project_id=project.id,
        build_quantity=2,
        strategy=strategy,
        country_code="CZ",
        currency_code="EUR",
        preferred_distributors=["DigiKey"],
        status=status,
        created_at=created_at,
        expires_at=created_at + expires_delta,
        created_by=user.id,
    )
    line_values = {
        "project_entry_id": entry.id,
        "part_id": part.id,
        "mpn_searched": "STM32F103C8T6",
        "required_qty": 10,
        "internal_available_qty": 2,
        "shortage_qty": 8,
        "selected_distributor": "DigiKey",
        "selected_qty": 10,
        "selected_unit_price": 1.25,
        "selected_currency": "EUR",
        "selected_packaging": "Tape",
        "selected_moq": 1,
        "selected_lead_time_days": 3,
        "selected_url": "https://example.test/offer",
        "risk_flags": [],
    }
    if line_kwargs:
        line_values.update(line_kwargs)
    plan.lines.append(PurchasePlanLine(**line_values))
    db.add(plan)
    return plan


def test_check_constraint_rejects_8_day_ttl(db: Session) -> None:
    _purchase_plan(db, expires_delta=timedelta(days=8))

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_strategy_check_rejects_invalid_value(db: Session) -> None:
    _purchase_plan(db, strategy="magic")

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_status_check_rejects_invalid_value(db: Session) -> None:
    _purchase_plan(db, status="stale")

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_cascade_delete_project_removes_plans_and_lines(db: Session) -> None:
    plan = _purchase_plan(db)
    db.flush()
    project_id = plan.project_id

    project = db.get(Project, project_id)
    assert project is not None
    db.delete(project)
    db.flush()

    assert db.execute(select(func.count()).select_from(PurchasePlan)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(PurchasePlanLine)).scalar_one() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required_qty", -1),
        ("internal_available_qty", -1),
        ("shortage_qty", -1),
        ("selected_qty", -1),
        ("selected_moq", 0),
    ],
)
def test_negative_quantities_rejected(db: Session, field: str, value: int) -> None:
    _purchase_plan(db, line_kwargs={field: value})

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
