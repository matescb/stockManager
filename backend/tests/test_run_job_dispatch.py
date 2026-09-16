"""Every registered job actually runs when dispatched through `main()`.

The gap this closes is specific and was real: a change to the CLI's
option plumbing made `main()` call `job.run(db, options)` for every job,
including the eight that take a session and nothing else. Nothing caught
it. The option tests only exercised jobs that *do* take options, the job
tests called the run functions directly, and the heartbeat tests never
dispatch. The five cron sidecars would have crashed on the first run
after deploy, with `TypeError` and no heartbeat.

So this file dispatches **every** name in the registry through the same
front door an operator and a sidecar use, with the work stubbed out. It
is deliberately indifferent to how options are plumbed: if a future
design changes the calling convention, this is the file that says
whether every registered job still survives the call.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from app.cli import run_job as run_job_cli
from app.cli.run_job import JOBS, JobSpec


class _FakeResult:
    def scalar(self) -> bool:
        return True


class _FakeSession:
    """Enough Session for the advisory lock and the commit."""

    def __init__(self) -> None:
        self.committed = False

    def execute(self, statement, params=None) -> _FakeResult:
        return _FakeResult()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:  # pragma: no cover - no job raises here
        pass

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every settings-backed job needs a non-zero interval to be loadable."""
    from app.core.config import settings

    settings.cache_clear()
    for name in (
        "SESSION_PURGE_INTERVAL_SECONDS",
        "PASSWORD_RESET_PURGE_INTERVAL_SECONDS",
        "DATASHEET_BACKFILL_INTERVAL_SECONDS",
    ):
        monkeypatch.setenv(name, "3600")
    yield
    settings.cache_clear()


def _is_operator_run(spec: JobSpec) -> bool:
    """Whether this job takes flags, however the registry spells that.

    Asked of the spec rather than hard-coded, because how a job declares
    its flags is exactly the thing this file must not depend on: the
    plumbing has already been redesigned once and is queued to be again.
    """
    for field in ("takes_options", "add_arguments"):
        if hasattr(spec, field):
            return bool(getattr(spec, field))
    raise AssertionError(
        "JobSpec no longer says which jobs take options; teach this helper "
        "the new spelling rather than deleting the distinction"
    )


@pytest.mark.parametrize("job_name", sorted(JOBS))
def test_every_registered_job_dispatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, job_name: str
) -> None:
    """`run_job <name>` with no flags reaches the job and exits 0."""
    # Arrange — the real spec, with only its `run` replaced, so the
    # dispatch path sees exactly the shape the registry declares.
    calls: list[object] = []

    def _run(db, *args, **kwargs) -> int:
        calls.append((args, kwargs))
        return 0

    # `replace`, not a field-by-field copy: the registry's declaration of
    # this job is the thing under test, and listing its fields here would
    # make the test fail for a renamed field rather than for a job that
    # cannot be dispatched.
    jobs = {job_name: dataclasses.replace(JOBS[job_name], run=_run)}
    monkeypatch.setattr(sys, "argv", ["python -m app.cli.run_job", job_name])

    # Act
    exit_code = run_job_cli.main(
        jobs=jobs,
        session_factory=_FakeSession,
        heartbeat_dir=tmp_path / "heartbeats",
    )

    # Assert
    assert exit_code == 0, f"{job_name} did not dispatch cleanly"
    assert len(calls) == 1, f"{job_name} was not called exactly once"


@pytest.mark.parametrize("job_name", sorted(JOBS))
def test_a_job_that_takes_only_a_session_is_called_with_only_a_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, job_name: str
) -> None:
    """The regression itself: a scheduled job's `run(db)` signature must
    not be handed a second argument."""
    # Arrange — a strict one-parameter callable, like every cron job's.
    if _is_operator_run(JOBS[job_name]):
        pytest.skip("operator-run; it takes flags, and the call above covers it")
    seen: list[object] = []

    def _run(db) -> int:
        seen.append(db)
        return 0

    jobs = {job_name: dataclasses.replace(JOBS[job_name], run=_run)}
    monkeypatch.setattr(sys, "argv", ["python -m app.cli.run_job", job_name])

    # Act
    exit_code = run_job_cli.main(
        jobs=jobs,
        session_factory=_FakeSession,
        heartbeat_dir=tmp_path / "heartbeats",
    )

    # Assert
    assert exit_code == 0
    assert len(seen) == 1
