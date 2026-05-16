from __future__ import annotations

import os
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
    monkeypatch.setenv("SESSION_PURGE_INTERVAL_SECONDS", "123")
    monkeypatch.setenv("PASSWORD_RESET_PURGE_INTERVAL_SECONDS", "456")

    exit_code = _run_main(monkeypatch, job_name, "--print-interval")

    output = capsys.readouterr()
    if job_name == "session-purge":
        assert exit_code == 0
        assert output.out == "123\n"
        assert output.err == ""
    elif job_name == "password-reset-purge":
        assert exit_code == 0
        assert output.out == "456\n"
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
