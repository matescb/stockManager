"""`run_job --dry-run / --apply / --workspace` — the operator-run job
plumbing added for `category-seed` and `symbol-collapse`.

The point of these flags is that a job which changes data nobody asked
it to change on a timer cannot do so by accident. Two guards, tested
separately:

1. the job plans without writing, which is pinned in each job's own
   test file, and
2. `run_job` ends a dry run in ROLLBACK rather than COMMIT, which is
   pinned here — this is the one that holds even if a job forgets.

The scheduled sidecar jobs keep the old `run(db)` signature and must
refuse these flags outright rather than accept and ignore them: a
`--apply` that silently did nothing would be the worst possible answer.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy.orm import Session

from app.cli.run_job import (
    JOBS,
    JobConfigError,
    JobOptions,
    JobSpec,
    heartbeat_is_fresh,
    main,
    run_job,
)

_OPERATOR_JOBS = ("category-seed", "symbol-collapse")


class _FakeResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar(self) -> bool:
        return self.value


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement, params=None) -> _FakeResult:
        return _FakeResult(True)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _spec(run, **extra) -> dict[str, JobSpec]:
    return {
        "example": JobSpec(
            name="example",
            owner="tests",
            cadence="manual",
            idempotency="test-only",
            run=run,
            **extra,
        )
    }


def test_a_dry_run_rolls_back_instead_of_committing(tmp_path) -> None:
    session = _FakeSession()
    seen: list[JobOptions] = []

    def _job(db: Session, options: JobOptions) -> int:
        seen.append(options)
        return 7

    affected = run_job(
        "example",
        jobs=_spec(_job, takes_options=True),
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
        options=JobOptions(apply=False),
    )

    assert affected == 7
    assert seen == [JobOptions(apply=False)]
    assert session.rolled_back is True
    assert session.committed is False


def test_apply_commits(tmp_path) -> None:
    session = _FakeSession()

    def _job(db: Session, options: JobOptions) -> int:
        return 3

    run_job(
        "example",
        jobs=_spec(_job, takes_options=True),
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
        options=JobOptions(apply=True),
    )

    assert session.committed is True
    assert session.rolled_back is False


def test_an_operator_job_defaults_to_a_dry_run(tmp_path) -> None:
    """No options at all still means "plan, don't write"."""
    session = _FakeSession()
    seen: list[JobOptions] = []

    def _job(db: Session, options: JobOptions) -> int:
        seen.append(options)
        return 0

    run_job(
        "example",
        jobs=_spec(_job, takes_options=True),
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert seen == [JobOptions(apply=False)]
    assert session.rolled_back is True


def test_a_scheduled_job_refuses_the_flags(tmp_path) -> None:
    session = _FakeSession()

    def _job(db: Session) -> int:
        raise AssertionError("must not run")

    with pytest.raises(JobConfigError, match="takes no"):
        run_job(
            "example",
            jobs=_spec(_job),
            session_factory=lambda: session,  # type: ignore[return-value]
            heartbeat_dir=tmp_path,
            options=JobOptions(apply=True),
        )


def test_a_scheduled_job_still_commits(tmp_path) -> None:
    session = _FakeSession()

    run_job(
        "example",
        jobs=_spec(lambda db: 1),
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert session.committed is True
    assert session.rolled_back is False


# ---------------------------------------------------------------------
# argv parsing
# ---------------------------------------------------------------------


def test_main_passes_apply_and_workspace_through(tmp_path) -> None:
    session = _FakeSession()
    seen: list[JobOptions] = []
    workspace_id = "3f4d1a1e-0f1e-4c3b-9b2a-5d6e7f809a0b"

    def _job(db: Session, options: JobOptions) -> int:
        seen.append(options)
        return 0

    code = main(
        ["example", "--apply", "--workspace", workspace_id],
        jobs=_spec(_job, takes_options=True),
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert code == 0
    assert seen[0].apply is True
    assert str(seen[0].workspace_id) == workspace_id
    assert session.committed is True


def test_main_rejects_a_non_uuid_workspace(tmp_path, capsys) -> None:
    code = main(
        ["example", "--workspace", "not-a-uuid"],
        jobs=_spec(lambda db, options: 0, takes_options=True),
        session_factory=_FakeSession,  # type: ignore[arg-type]
        heartbeat_dir=tmp_path,
    )

    assert code == 2
    assert "not a UUID" in capsys.readouterr().err


def test_main_rejects_apply_on_a_scheduled_job(tmp_path, capsys) -> None:
    code = main(
        ["example", "--apply"],
        jobs=_spec(lambda db: 0),
        session_factory=_FakeSession,  # type: ignore[arg-type]
        heartbeat_dir=tmp_path,
    )

    assert code == 2
    assert "takes no" in capsys.readouterr().err


def test_apply_cannot_ride_along_with_a_config_probe(tmp_path) -> None:
    """`--print-interval` answers a question and never runs the job, so an
    `--apply` beside it would be discarded without a word."""
    code = main(
        ["example", "--print-interval", "--apply"],
        jobs=_spec(lambda db, options: 0, takes_options=True),
        session_factory=_FakeSession,  # type: ignore[arg-type]
        heartbeat_dir=tmp_path,
    )

    assert code == 2


def test_dry_run_and_apply_are_mutually_exclusive(tmp_path) -> None:
    code = main(
        ["example", "--dry-run", "--apply"],
        jobs=_spec(lambda db, options: 0, takes_options=True),
        session_factory=_FakeSession,  # type: ignore[arg-type]
        heartbeat_dir=tmp_path,
    )

    assert code == 2


# ---------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------


@pytest.mark.parametrize("job_name", _OPERATOR_JOBS)
def test_the_operator_jobs_are_registered_and_unscheduled(job_name: str) -> None:
    """They take options, and they have no settings-backed interval —
    nothing in `docker-compose.prod.yml` should ever start them."""
    job = JOBS[job_name]
    assert job.takes_options is True
    assert job.interval_setting is None
    assert "--apply" in job.idempotency


def test_the_operator_jobs_are_exactly_these_two() -> None:
    """A set equality, not a subset: a third job quietly gaining
    `takes_options` would otherwise slip past every check here, and the
    deployment docs name these two by hand."""
    assert {name for name, job in JOBS.items() if job.takes_options} == set(
        _OPERATOR_JOBS
    )


@pytest.mark.parametrize("job_name", sorted(JOBS))
def test_every_job_signature_matches_its_takes_options_flag(job_name: str) -> None:
    """The flag decides how `run_job` calls the function, so a flag that
    disagrees with the signature is a `TypeError` at 3am, not at import."""
    job = JOBS[job_name]
    parameters = inspect.signature(job.run).parameters
    assert len(parameters) == 1 + int(job.takes_options)


@pytest.mark.parametrize("job_name", _OPERATOR_JOBS)
def test_an_operator_job_is_always_heartbeat_healthy(job_name: str) -> None:
    """Nothing schedules them, so they have no cadence to be late for.
    Answering `True` rather than raising is what lets a monitor iterate
    `list(JOBS)` without knowing which kind each job is."""
    assert heartbeat_is_fresh(job_name) is True


def test_an_operator_job_writes_no_heartbeat_file(tmp_path) -> None:
    """A file saying it ran once last March would read as healthy, and
    its absence as broken. Neither is a fact about a job a human runs."""
    session = _FakeSession()

    run_job(
        "example",
        jobs=_spec(lambda db, options: 0, takes_options=True),
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert list(tmp_path.iterdir()) == []


def test_a_scheduled_job_still_writes_a_heartbeat(tmp_path) -> None:
    run_job(
        "example",
        jobs=_spec(lambda db: 0),
        session_factory=lambda: _FakeSession(),  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert [p.name for p in tmp_path.iterdir()] == ["example"]


@pytest.mark.parametrize(
    "job_name", sorted(name for name, job in JOBS.items() if not job.takes_options)
)
def test_every_scheduled_job_commits(job_name: str, tmp_path, monkeypatch) -> None:
    """The ROLLBACK branch belongs to the operator-run jobs alone. A
    scheduled job that started rolling back would silently stop doing
    its work while still reporting `status=ok`."""
    session = _FakeSession()
    spec = JOBS[job_name]
    monkeypatch.setattr(
        "app.cli.run_job._job_interval_seconds", lambda job: None
    )

    run_job(
        job_name,
        jobs={job_name: JobSpec(
            name=spec.name,
            owner=spec.owner,
            cadence=spec.cadence,
            idempotency=spec.idempotency,
            run=lambda db: 0,
        )},
        session_factory=lambda: session,  # type: ignore[return-value]
        heartbeat_dir=tmp_path,
    )

    assert session.committed is True
    assert session.rolled_back is False


def test_no_scheduled_job_takes_options() -> None:
    scheduled = [
        name
        for name, job in JOBS.items()
        if job.interval_setting is not None and job.takes_options
    ]
    assert scheduled == []
