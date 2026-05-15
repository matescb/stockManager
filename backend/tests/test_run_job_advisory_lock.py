from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.cli.run_job import JobSpec, run_job

pytestmark = pytest.mark.real_db


def test_concurrent_same_job_skips(engine, tmp_path) -> None:
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    entered: list[int] = []
    completed: list[int] = []
    errors: list[BaseException] = []
    results: dict[str, int] = {}
    second_elapsed: list[float] = []

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
            started = time.monotonic()
            if label == "second":
                started = time.monotonic()
            result = run_job(
                "example",
                jobs=jobs,
                session_factory=SessionLocal,
                heartbeat_dir=tmp_path,
            )
            if label == "second":
                second_elapsed.append(time.monotonic() - started)
            results[label] = result
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=invoke, args=("first",), daemon=True)
    first.start()
    assert first_entered.wait(timeout=5)

    second = threading.Thread(target=invoke, args=("second",), daemon=True)
    second.start()
    second.join(timeout=1)
    assert not second.is_alive()
    with state_lock:
        assert entered == [1]
        assert completed == []
    assert results == {"second": 0}
    assert second_elapsed and second_elapsed[0] < 1

    release_first.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert errors == []
    assert results == {"first": 1, "second": 0}
    assert completed == [1]
    assert (tmp_path / "example").read_text(encoding="utf-8") == "ok\n"


def test_concurrent_different_jobs_do_not_block_each_other(engine, tmp_path) -> None:
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    second_completed = threading.Event()
    errors: list[BaseException] = []
    results: dict[str, int] = {}

    def _first_job(db: Session) -> int:
        first_entered.set()
        if not release_first.wait(timeout=5):
            raise AssertionError("timed out waiting to release the first job")
        return 10

    def _second_job(db: Session) -> int:
        second_completed.set()
        return 20

    jobs = {
        "first": JobSpec(
            name="first",
            owner="tests",
            cadence="manual",
            idempotency="test-only",
            run=_first_job,
        ),
        "second": JobSpec(
            name="second",
            owner="tests",
            cadence="manual",
            idempotency="test-only",
            run=_second_job,
        ),
    }

    def invoke(job_name: str) -> None:
        try:
            results[job_name] = run_job(
                job_name,
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
    second.join(timeout=1)
    assert second_completed.is_set()
    assert not second.is_alive()
    assert results == {"second": 20}

    release_first.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert errors == []
    assert results == {"first": 10, "second": 20}
    assert (tmp_path / "first").read_text(encoding="utf-8") == "ok\n"
    assert (tmp_path / "second").read_text(encoding="utf-8") == "ok\n"
