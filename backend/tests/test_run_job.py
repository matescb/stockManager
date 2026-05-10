from __future__ import annotations

from sqlalchemy.orm import Session

from app.cli.run_job import JOBS, JobSpec, UnknownJobError, main, run_job


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_run_job_dispatches_allow_listed_job() -> None:
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
    )

    assert affected == 4
    assert calls == [session]
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True


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


def test_sourcing_alerts_evaluate_dispatches_to_module(monkeypatch) -> None:
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
    )

    assert affected == 7
    assert calls == [session]
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True
