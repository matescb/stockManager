from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.sourcing import alerts_evaluator
from app.domain.sourcing.alerts_evaluator import AlertVerdict, evaluate_all_alerts
from app.domain.sourcing.models import SourcingAlert
from app.domain.stock.models import StockEntry
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember


def _workspace(db: Session, *, role: str = "owner") -> tuple[Workspace, User]:
    user = User(
        email=f"alerts-{uuid.uuid4().hex[:8]}@example.com",
        name="Alert Tester",
        password_hash="test",
    )
    db.add(user)
    db.flush()
    workspace = Workspace(
        name=f"alerts-ws-{uuid.uuid4().hex[:8]}",
        kind="organization",
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role=role,
            status="active",
        )
    )
    db.flush()
    return workspace, user


def _part(db: Session, workspace: Workspace, user: User, *, mpn: str | None = None) -> Part:
    part = Part(
        workspace_id=workspace.id,
        name=f"Part {uuid.uuid4().hex[:8]}",
        part_type="local",
        mpn=mpn or f"MPN-{uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(part)
    db.flush()
    return part


def _project(db: Session, workspace: Workspace, user: User) -> Project:
    project = Project(
        workspace_id=workspace.id,
        name=f"Project {uuid.uuid4().hex[:8]}",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(project)
    db.flush()
    return project


def _stock(db: Session, workspace: Workspace, part: Part, qty: int, user: User) -> None:
    db.add(
        StockEntry(
            workspace_id=workspace.id,
            part_id=part.id,
            quantity_delta=qty,
            status="on_hand",
            operation_type="test",
            created_by=user.id,
        )
    )
    db.flush()


def _alert(
    db: Session,
    workspace: Workspace,
    user: User,
    *,
    alert_type: str,
    threshold: dict[str, Any],
    part: Part | None = None,
    project: Project | None = None,
    last_state: dict[str, Any] | None = None,
    last_notified_offset: timedelta | None = None,
    notify_user_ids: list[str] | None | object = ...,
    enabled: bool = True,
    archived: bool = False,
    cooldown_seconds: int = 60,
) -> SourcingAlert:
    if notify_user_ids is ...:
        notify_user_ids = [str(user.id)]
    alert = SourcingAlert(
        workspace_id=workspace.id,
        part_id=part.id if part is not None else None,
        project_id=project.id if project is not None else None,
        alert_type=alert_type,
        threshold=threshold,
        notify_user_ids=notify_user_ids,
        cooldown_seconds=cooldown_seconds,
        enabled=enabled,
        archived_at=utcnow() if archived else None,
        last_evaluation_state=last_state,
        last_notified_at=(
            utcnow() - last_notified_offset if last_notified_offset is not None else None
        ),
        created_by=user.id,
    )
    db.add(alert)
    db.flush()
    return alert


def _capture_mail(monkeypatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake_send(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(alerts_evaluator.mail, "send", fake_send)
    return sent


def _search_out(
    *,
    stock: int,
    price: str | None = None,
    currency: str | None = "USD",
) -> Any:
    distributor = SimpleNamespace(
        stock=stock,
        unit_price=float(price) if price is not None else None,
        currency=currency,
        unit_price_converted=None,
        currency_displayed=None,
        price_breaks=[],
        price_breaks_converted=None,
    )
    return SimpleNamespace(
        results=[
            SimpleNamespace(
                offers=[
                    SimpleNamespace(
                        distributors=[distributor],
                    )
                ]
            )
        ]
    )


def test_stock_below_triggers_when_qty_drops(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _stock(db, workspace, part, 4, user)
    alert = _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        last_state={"qty": 5},
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    db.refresh(alert)
    assert len(sent) == 1
    assert alert.last_checked_at is not None
    assert alert.last_notified_at is not None
    assert alert.last_evaluation_state == {"qty": 4}


def test_stock_above_triggers_when_qty_rises(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _stock(db, workspace, part, 6, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_above",
        threshold={"qty": 5},
        last_state={"qty": 5},
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert len(sent) == 1


def test_back_in_stock_initial_run_records_state_without_triggering(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    alert = _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="back_in_stock",
        threshold={},
        last_state=None,
    )
    monkeypatch.setattr(
        alerts_evaluator.sourcing_service,
        "search",
        lambda *args, **kwargs: _search_out(stock=0),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    db.refresh(alert)
    assert sent == []
    assert alert.last_notified_at is None
    assert alert.last_evaluation_state == {"had_stock": False}


def test_back_in_stock_triggers_on_zero_to_positive_transition(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="back_in_stock",
        threshold={},
        last_state={"had_stock": False},
    )
    monkeypatch.setattr(
        alerts_evaluator.sourcing_service,
        "search",
        lambda *args, **kwargs: _search_out(stock=22),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert len(sent) == 1


def test_back_in_stock_does_not_re_trigger_while_still_in_stock(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    alert = _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="back_in_stock",
        threshold={},
        last_state={"had_stock": True},
        last_notified_offset=timedelta(hours=2),
    )
    monkeypatch.setattr(
        alerts_evaluator.sourcing_service,
        "search",
        lambda *args, **kwargs: _search_out(stock=22),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    db.refresh(alert)
    assert sent == []
    assert alert.last_evaluation_state == {"had_stock": True}


def test_out_of_authorized_stock_triggers_on_positive_to_zero(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="out_of_authorized_stock",
        threshold={},
        last_state={"had_stock": True},
    )
    monkeypatch.setattr(
        alerts_evaluator.sourcing_service,
        "search",
        lambda *args, **kwargs: _search_out(stock=0),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert len(sent) == 1


def test_price_changed_triggers_on_delta_pct_breach(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="price_changed",
        threshold={"delta_pct": 10},
        last_state={"last_price": "1.00", "last_currency": "USD"},
    )
    monkeypatch.setattr(
        alerts_evaluator.sourcing_service,
        "search",
        lambda *args, **kwargs: _search_out(stock=10, price="1.25"),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert len(sent) == 1


def test_price_changed_currency_mismatch_resets_state_no_trigger(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    alert = _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="price_changed",
        threshold={"delta_pct": 10},
        last_state={"last_price": "1.00", "last_currency": "USD"},
    )
    monkeypatch.setattr(
        alerts_evaluator.sourcing_service,
        "search",
        lambda *args, **kwargs: _search_out(stock=10, price="1.25", currency="EUR"),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    db.refresh(alert)
    assert sent == []
    assert alert.last_evaluation_state == {"last_price": "1.25", "last_currency": "EUR"}


def test_bom_buyable_triggers_on_not_buyable_to_buyable_transition(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    project = _project(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        project=project,
        alert_type="bom_buyable",
        threshold={"build_quantity": 3},
        last_state={"is_buyable": False},
    )

    def fake_source_bom(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            capacity=SimpleNamespace(can_build_after_purchase=3),
        )

    monkeypatch.setattr(alerts_evaluator.sourcing_service, "source_bom", fake_source_bom)
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert len(sent) == 1


def test_cooldown_suppresses_second_notification(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        last_notified_offset=timedelta(seconds=10),
        cooldown_seconds=60,
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert sent == []


def test_cooldown_elapsed_re_triggers(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        last_notified_offset=timedelta(seconds=120),
        cooldown_seconds=60,
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert len(sent) == 1


def test_smtp_failure_does_not_crash_loop(db: Session, monkeypatch, caplog) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )

    def fail_send(**_kwargs: Any) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(alerts_evaluator.mail, "send", fail_send)

    assert evaluate_all_alerts(db) == 1

    assert "sourcing_alert.smtp_failed" in caplog.text


def test_recipients_default_to_workspace_admins_when_notify_user_ids_null(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db, role="admin")
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        notify_user_ids=None,
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    assert [item["to"] for item in sent] == [user.email]


def test_no_recipients_logs_warning_and_continues(
    db: Session,
    monkeypatch,
    caplog,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    alert = _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        notify_user_ids=[],
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    db.refresh(alert)
    assert sent == []
    assert alert.last_notified_at is None
    assert "sourcing_alert.no_recipients" in caplog.text


def test_evaluator_uses_cache_default_true_for_sourcing_alerts(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    project = _project(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="back_in_stock",
        threshold={},
        last_state={"had_stock": False},
    )
    _alert(
        db,
        workspace,
        user,
        project=project,
        alert_type="bom_buyable",
        threshold={"build_quantity": 2},
        last_state={"is_buyable": False},
    )
    search_cached: list[bool | None] = []
    bom_cached: list[bool | None] = []

    def fake_search(*args: Any, **kwargs: Any) -> Any:
        search_cached.append(kwargs.get("use_cached_data"))
        return _search_out(stock=1)

    def fake_source_bom(*args: Any, **kwargs: Any) -> Any:
        bom_cached.append(kwargs.get("use_cached_data"))
        return SimpleNamespace(
            capacity=SimpleNamespace(can_build_after_purchase=2),
        )

    monkeypatch.setattr(alerts_evaluator.sourcing_service, "search", fake_search)
    monkeypatch.setattr(alerts_evaluator.sourcing_service, "source_bom", fake_source_bom)
    _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 2

    assert search_cached == [True]
    assert bom_cached == [True]


def test_workspace_isolation_two_alerts_same_mpn_different_workspaces(
    db: Session,
    monkeypatch,
) -> None:
    workspace_a, user_a = _workspace(db)
    workspace_b, user_b = _workspace(db)
    part_a = _part(db, workspace_a, user_a, mpn="SHARED-MPN")
    part_b = _part(db, workspace_b, user_b, mpn="SHARED-MPN")
    _alert(
        db,
        workspace_a,
        user_a,
        part=part_a,
        alert_type="back_in_stock",
        threshold={},
        last_state={"had_stock": False},
    )
    _alert(
        db,
        workspace_b,
        user_b,
        part=part_b,
        alert_type="back_in_stock",
        threshold={},
        last_state={"had_stock": False},
    )
    seen: list[uuid.UUID] = []

    def fake_search(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["workspace"].id)
        return _search_out(stock=1)

    monkeypatch.setattr(alerts_evaluator.sourcing_service, "search", fake_search)
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 2

    assert set(seen) == {workspace_a.id, workspace_b.id}
    assert len(sent) == 2


def test_archived_alerts_skipped(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        archived=True,
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 0

    assert sent == []


def test_disabled_alerts_skipped(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        enabled=False,
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )
    sent = _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 0

    assert sent == []


def test_last_notified_at_updates_only_on_actual_email_send(
    db: Session,
    monkeypatch,
) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    alert = _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
        notify_user_ids=[],
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(True, "Triggered", {}, {"qty": 1}),
    )
    _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    db.refresh(alert)
    assert alert.last_checked_at is not None
    assert alert.last_notified_at is None


def test_no_persistent_alert_history_table() -> None:
    assert "sourcing_alert_history" not in SourcingAlert.metadata.tables
    assert "sourcing_alert_events" not in SourcingAlert.metadata.tables


def test_all_alerts_are_persisted_as_json_state(db: Session, monkeypatch) -> None:
    workspace, user = _workspace(db)
    part = _part(db, workspace, user)
    alert = _alert(
        db,
        workspace,
        user,
        part=part,
        alert_type="stock_below",
        threshold={"qty": 5},
    )
    monkeypatch.setitem(
        alerts_evaluator.EVALUATORS,
        "stock_below",
        lambda _db, _ws, _alert: AlertVerdict(False, "State", {}, {"opaque": "value"}),
    )
    _capture_mail(monkeypatch)

    assert evaluate_all_alerts(db) == 1

    stored = db.execute(select(SourcingAlert).where(SourcingAlert.id == alert.id)).scalar_one()
    assert stored.last_evaluation_state == {"opaque": "value"}
