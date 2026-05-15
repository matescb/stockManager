from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.cli.run_job import JOBS, JobSpec, UnknownJobError, main, run_job
from app.core.time import utcnow
from app.domain.users.models import User, UserSession


class _FakeResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar(self) -> bool:
        return self.value


class _FakeSession:
    def __init__(self, *, execute_result: bool = True) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.executed: list[tuple[str, dict[str, object] | None]] = []
        self.execute_result = execute_result

    def execute(self, statement, params=None) -> _FakeResult:
        self.executed.append((str(statement), params))
        return _FakeResult(self.execute_result)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_run_job_dispatches_allow_listed_job(tmp_path) -> None:
    session = _FakeSession()
    calls: list[Session] = []

    def _job(db: Session) -> int:
        calls.append(db)
        return 4

    affected = run_job(
        "example",
        jobs={
            "example": JobSpec(
                name="example",
                owner="tests",
                cadence="manual",
                idempotency="test-only",
                run=_job,
            )
        },
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert affected == 4
    assert calls == [session]
    assert session.executed == [
        ("SELECT pg_try_advisory_xact_lock(hashtext(:job_name))", {"job_name": "example"})
    ]
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True
    assert (tmp_path / "example").read_text(encoding="utf-8") == "ok\n"


def test_run_job_rejects_unknown_job() -> None:
    try:
        run_job("missing", jobs={}, session_factory=lambda: _FakeSession())  # type: ignore[return-value]
    except UnknownJobError as exc:
        assert "unknown job 'missing'" in str(exc)
    else:
        raise AssertionError("unknown job should raise UnknownJobError")


def test_run_job_main_returns_nonzero_for_unknown_job(capsys) -> None:
    exit_code = main(["missing"], jobs={}, session_factory=lambda: _FakeSession())  # type: ignore[return-value]

    assert exit_code == 2
    assert "unknown job 'missing'" in capsys.readouterr().err


def test_sourcing_alerts_evaluate_registered() -> None:
    spec = JOBS["sourcing-alerts-evaluate"]

    assert spec.name == "sourcing-alerts-evaluate"
    assert spec.owner == "backend/sourcing"
    assert spec.cadence == "every 15 minutes"
    assert "enabled, non-archived sourcing_alerts" in spec.idempotency
    assert "cooldown" in spec.idempotency


def test_session_purge_registered() -> None:
    spec = JOBS["session-purge"]

    assert spec.name == "session-purge"
    assert spec.owner == "backend/auth-security"
    assert spec.cadence == "hourly"
    assert "expires_at" in spec.idempotency


def test_session_purge_idempotent(db, tmp_path) -> None:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.test",
        name="Session Purge",
        password_hash="hash",
    )
    now = utcnow()
    db.add(user)
    db.add_all(
        [
            UserSession(
                token_hash="a" * 64,
                user_id=user.id,
                expires_at=now - timedelta(seconds=1),
            ),
            UserSession(
                token_hash="b" * 64,
                user_id=user.id,
                expires_at=now + timedelta(hours=1),
            ),
        ]
    )
    db.flush()

    first = run_job(
        "session-purge",
        session_factory=lambda: db,
        heartbeat_dir=tmp_path,
    )
    second = run_job(
        "session-purge",
        session_factory=lambda: db,
        heartbeat_dir=tmp_path,
    )

    remaining = db.query(UserSession).all()
    assert first == 1
    assert second == 0
    assert [row.token_hash for row in remaining] == ["b" * 64]
    assert (tmp_path / "session-purge").read_text(encoding="utf-8") == "ok\n"


def test_sourcing_alerts_evaluate_dispatches_to_module(monkeypatch, tmp_path) -> None:
    session = _FakeSession()
    calls: list[Session] = []

    def _evaluate_all_alerts(db: Session) -> int:
        calls.append(db)
        return 7

    monkeypatch.setattr(
        "app.domain.sourcing.alerts_evaluator.evaluate_all_alerts",
        _evaluate_all_alerts,
    )

    affected = run_job(
        "sourcing-alerts-evaluate",
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert affected == 7
    assert calls == [session]
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True
    assert (tmp_path / "sourcing-alerts-evaluate").exists()


def test_run_job_skips_when_advisory_lock_denied(caplog, tmp_path) -> None:
    session = _FakeSession(execute_result=False)
    calls: list[Session] = []

    def _job(db: Session) -> int:
        calls.append(db)
        return 4

    with caplog.at_level(logging.INFO, logger="app.cli.run_job"):
        affected = run_job(
            "example",
            jobs={
                "example": JobSpec(
                    name="example",
                    owner="tests",
                    cadence="manual",
                    idempotency="test-only",
                    run=_job,
                )
            },
            session_factory=lambda: session,  # type: ignore[return-value]
            heartbeat_dir=tmp_path,
        )

    assert affected == 0
    assert calls == []
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True
    assert not (tmp_path / "example").exists()
    assert "job=example status=skipped reason=lock_denied" in caplog.text
