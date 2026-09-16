from __future__ import annotations

import os
import stat
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from app.cli import run_job as run_job_cli
from app.cli.run_job import JOBS, JobSpec
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    settings.cache_clear()
    yield
    settings.cache_clear()


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    *args: str,
    jobs: Mapping[str, JobSpec] = JOBS,
) -> int:
    monkeypatch.setattr(sys, "argv", ["python -m app.cli.run_job", *args])
    return run_job_cli.main(jobs=jobs)


@pytest.mark.parametrize("job_name", sorted(JOBS))
def test_print_interval_each_job(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    job_name: str,
) -> None:
    # One distinct value per settings-backed job, so the assertion below
    # proves --print-interval reads THAT job's setting and not a neighbour's.
    intervals = {
        "SESSION_PURGE_INTERVAL_SECONDS": "123",
        "PASSWORD_RESET_PURGE_INTERVAL_SECONDS": "456",
        "DATASHEET_BACKFILL_INTERVAL_SECONDS": "789",
    }
    for name, value in intervals.items():
        monkeypatch.setenv(name, value)

    exit_code = _run_main(monkeypatch, job_name, "--print-interval")

    output = capsys.readouterr()
    interval_setting = JOBS[job_name].interval_setting
    if interval_setting is not None:
        # Every settings-backed job must be reachable through the sidecar's
        # `--print-interval` gate; a new one that forgets its env var here
        # would silently never start.
        assert interval_setting in intervals, (
            f"{job_name} interval setting {interval_setting} is not covered here"
        )
        assert exit_code == 0
        assert output.out == f"{intervals[interval_setting]}\n"
        assert output.err == ""
    else:
        assert exit_code == 2
        assert output.out == ""
        assert f"job '{job_name}' does not define a settings interval" in output.err


@pytest.fixture
def heartbeat_job(monkeypatch: pytest.MonkeyPatch) -> Iterator[JobSpec]:
    monkeypatch.setenv("SESSION_PURGE_INTERVAL_SECONDS", "3600")
    name = f"test-main-heartbeat-{uuid.uuid4().hex}"
    heartbeat_path = run_job_cli.HEARTBEAT_DIR / name
    heartbeat_path.unlink(missing_ok=True)
    try:
        yield JobSpec(
            name=name,
            owner="tests",
            cadence="manual",
            idempotency="test-only",
            run=lambda db: 0,
            interval_setting="SESSION_PURGE_INTERVAL_SECONDS",
        )
    finally:
        heartbeat_path.unlink(missing_ok=True)


def _check_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    heartbeat_job: JobSpec,
    *extra_args: str,
) -> int:
    return _run_main(
        monkeypatch,
        heartbeat_job.name,
        "--check-heartbeat",
        *extra_args,
        jobs={heartbeat_job.name: heartbeat_job},
    )


def _write_heartbeat(path: Path, *, age_seconds: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ok\n", encoding="utf-8")
    if age_seconds:
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))


def test_check_heartbeat_fresh(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    heartbeat_job: JobSpec,
) -> None:
    _write_heartbeat(run_job_cli.HEARTBEAT_DIR / heartbeat_job.name)

    exit_code = _check_heartbeat(monkeypatch, heartbeat_job)

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out == ""
    assert output.err == ""


def test_check_heartbeat_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    heartbeat_job: JobSpec,
) -> None:
    exit_code = _check_heartbeat(monkeypatch, heartbeat_job)

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.out == ""
    assert output.err == (
        f"job={heartbeat_job.name} status=unhealthy reason=heartbeat\n"
    )


def test_check_heartbeat_stale(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    heartbeat_job: JobSpec,
) -> None:
    _write_heartbeat(run_job_cli.HEARTBEAT_DIR / heartbeat_job.name, age_seconds=120)

    exit_code = _check_heartbeat(
        monkeypatch,
        heartbeat_job,
        "--heartbeat-max-age-seconds",
        "60",
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.out == ""
    assert output.err == (
        f"job={heartbeat_job.name} status=unhealthy reason=heartbeat\n"
    )


def test_print_interval_rejects_heartbeat_max_age(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    heartbeat_job: JobSpec,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_job_cli._parse_args(
            [
                heartbeat_job.name,
                "--print-interval",
                "--heartbeat-max-age-seconds",
                "30",
            ]
        )

    output = capsys.readouterr()
    assert exc_info.value.code == 2
    assert output.out == ""
    assert "--heartbeat-max-age-seconds requires --check-heartbeat" in output.err

    exit_code = _run_main(
        monkeypatch,
        heartbeat_job.name,
        "--print-interval",
        "--heartbeat-max-age-seconds",
        "30",
        jobs={heartbeat_job.name: heartbeat_job},
    )

    output = capsys.readouterr()
    assert exit_code == 2
    assert output.out == ""
    assert "--heartbeat-max-age-seconds requires --check-heartbeat" in output.err


def test_check_heartbeat_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    heartbeat_job: JobSpec,
) -> None:
    heartbeat_path = run_job_cli.HEARTBEAT_DIR / heartbeat_job.name
    _write_heartbeat(heartbeat_path)

    exit_code = _check_heartbeat(monkeypatch, heartbeat_job)

    output = capsys.readouterr()
    assert exit_code == 0
    assert heartbeat_path.read_text(encoding="utf-8") == "ok\n"
    assert output.out == ""
    assert output.err == ""


# ---------------------------------------------------------------------------
# Backfill flags (A5) — `--dry-run` / `--apply` / `--workspace` / `--report`
#
# A one-off backfill is reviewed before it runs, so its flags are part of the
# runner rather than a second entry point. They are accepted for exactly the
# jobs that declare them: a flag that is silently ignored on a job that cannot
# honour it reads, from the shell, like a job that did what you asked.
# ---------------------------------------------------------------------------
class _NoopSession:
    def execute(self, statement, params=None):
        class _Result:
            @staticmethod
            def scalar() -> bool:
                return True

        return _Result()

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def backfill_job() -> tuple[JobSpec, list[run_job_cli.BackfillOptions]]:
    seen: list[run_job_cli.BackfillOptions] = []

    def _run(db, options: run_job_cli.BackfillOptions) -> int:
        seen.append(options)
        return 7

    return (
        JobSpec(
            name="test-backfill",
            owner="tests",
            cadence="manual",
            idempotency="test-only",
            run=_run,
            takes_backfill_options=True,
        ),
        seen,
    )


def _run_backfill(
    monkeypatch: pytest.MonkeyPatch, job: JobSpec, *args: str
) -> int:
    monkeypatch.setattr(sys, "argv", ["python -m app.cli.run_job", job.name, *args])
    return run_job_cli.main(
        jobs={job.name: job},
        session_factory=_NoopSession,
        heartbeat_dir=run_job_cli.HEARTBEAT_DIR,
    )


def test_a_backfill_defaults_to_a_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    backfill_job: tuple[JobSpec, list[run_job_cli.BackfillOptions]],
) -> None:
    job, seen = backfill_job

    assert _run_backfill(monkeypatch, job) == 0

    assert seen == [run_job_cli.BackfillOptions()]
    assert seen[0].apply is False


def test_a_backfill_passes_every_flag_through(
    monkeypatch: pytest.MonkeyPatch,
    backfill_job: tuple[JobSpec, list[run_job_cli.BackfillOptions]],
    tmp_path: Path,
) -> None:
    job, seen = backfill_job
    workspace_id = uuid.uuid4()
    report = tmp_path / "report.csv"

    exit_code = _run_backfill(
        monkeypatch,
        job,
        "--apply",
        "--workspace",
        str(workspace_id),
        "--report",
        str(report),
    )

    assert exit_code == 0
    assert seen == [
        run_job_cli.BackfillOptions(
            apply=True, workspace_id=workspace_id, report_path=report
        )
    ]


def test_dry_run_and_apply_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backfill_job: tuple[JobSpec, list[run_job_cli.BackfillOptions]],
) -> None:
    job, seen = backfill_job

    exit_code = _run_backfill(monkeypatch, job, "--dry-run", "--apply")

    assert exit_code == 2
    assert seen == []
    assert "not allowed with argument" in capsys.readouterr().err


def test_a_bad_workspace_id_is_refused_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backfill_job: tuple[JobSpec, list[run_job_cli.BackfillOptions]],
) -> None:
    job, seen = backfill_job

    exit_code = _run_backfill(monkeypatch, job, "--workspace", "not-a-uuid")

    assert exit_code == 2
    assert seen == []
    assert "--workspace is not a uuid" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flag", [("--apply",), ("--dry-run",), ("--workspace", "x"), ("--report", "x")]
)
def test_a_periodic_job_refuses_backfill_flags(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flag: tuple[str, ...],
) -> None:
    ran: list[str] = []
    job = JobSpec(
        name="test-periodic",
        owner="tests",
        cadence="hourly",
        idempotency="test-only",
        run=lambda db: ran.append("ran") or 0,
    )

    exit_code = _run_backfill(monkeypatch, job, *flag)

    assert exit_code == 2
    assert ran == []
    assert f"not accepted by job {job.name!r}" in capsys.readouterr().err


def test_spec_normalize_is_registered_as_a_backfill() -> None:
    """The sidecars pass no flags, so a job that took them without saying
    so would run its apply path from a cron loop."""
    assert JOBS["spec-normalize"].takes_backfill_options is True
    assert JOBS["spec-normalize"].interval_setting is None
    assert [
        name for name, job in JOBS.items() if job.takes_backfill_options
    ] == ["spec-normalize"]


# ---------------------------------------------------------------------------
# The report file's permissions
#
# A spec report names every part, key and value in a workspace, and lands in
# whatever directory the operator pointed at — `/tmp` on the prod container,
# which is world-readable by default. The mode is part of the contract, not
# the caller's problem.
# ---------------------------------------------------------------------------
def test_the_report_file_and_its_directory_are_private(
    monkeypatch: pytest.MonkeyPatch,
    backfill_job: tuple[JobSpec, list[run_job_cli.BackfillOptions]],
    tmp_path: Path,
) -> None:
    job, _ = backfill_job
    report = tmp_path / "reports" / "out.csv"

    with run_job_cli._report_stream(
        run_job_cli.BackfillOptions(report_path=report)
    ) as stream:
        assert stream is not None
        stream.write("x\n")

    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert stat.S_IMODE(report.parent.stat().st_mode) == 0o700
    assert report.read_text(encoding="utf-8") == "x\n"
    assert job.name == "test-backfill"


def test_no_report_path_yields_no_stream(
    backfill_job: tuple[JobSpec, list[run_job_cli.BackfillOptions]],
) -> None:
    """``None`` means stdout, and the job decides what that looks like."""
    with run_job_cli._report_stream(run_job_cli.BackfillOptions()) as stream:
        assert stream is None


def test_an_unwritable_report_path_is_a_usage_error(
    tmp_path: Path,
) -> None:
    """Not a traceback: the operator mistyped a path and can fix it."""
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(run_job_cli.JobConfigError) as exc_info:
        with run_job_cli._report_stream(
            run_job_cli.BackfillOptions(report_path=blocker / "nested" / "out.csv")
        ):
            pass

    assert "cannot write --report" in str(exc_info.value)
