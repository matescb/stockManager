from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.cli.run_job import JobSpec, run_job

pytestmark = pytest.mark.real_db


def test_concurrent_invocations_serialise(engine, tmp_path) -> None:
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    second_attempting = threading.Event()
    state_lock = threading.Lock()
    entered: list[int] = []
    completed: list[int] = []
    errors: list[BaseException] = []
    results: dict[str, int] = {}

    def _job(db: Session) -> int:
        with state_lock:
            run_index = len(entered) + 1
            entered.append(run_index)

        if run_index == 1:
            first_entered.set()
            if not release_first.wait(timeout=5):
                raise AssertionError("timed out waiting to release the first job")

        with state_lock:
            completed.append(run_index)
        return run_index

    jobs = {
        "example": JobSpec(
            name="example",
            owner="tests",
            cadence="manual",
            idempotency="test-only",
            run=_job,
        )
    }

    def invoke(label: str) -> None:
        try:
            if label == "second":
                second_attempting.set()
            results[label] = run_job(
                "example",
                jobs=jobs,
                session_factory=SessionLocal,
                heartbeat_dir=tmp_path,
            )
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=invoke, args=("first",), daemon=True)
    first.start()
    assert first_entered.wait(timeout=5)

    second = threading.Thread(target=invoke, args=("second",), daemon=True)
    second.start()
    assert second_attempting.wait(timeout=5)

    time.sleep(0.5)
    with state_lock:
        assert entered == [1]
        assert completed == []
    assert second.is_alive()

    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert results == {"first": 1, "second": 2}
    assert completed == [1, 2]
    assert (tmp_path / "example").read_text(encoding="utf-8") == "ok\n"
