from __future__ import annotations

from sqlalchemy.orm import Session

from app.cli.run_job import JobSpec, UnknownJobError, main, run_job


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
