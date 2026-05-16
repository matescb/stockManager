"""Allow-listed backend maintenance job runner."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.advisory_locks import RUN_JOB_LOCK_CLASSID

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
JobCallable = Callable[[Session], int]
HEARTBEAT_DIR = Path("/tmp/stockmanager-job-heartbeats")
HEARTBEAT_MAX_AGE_SECONDS = 90 * 60


@dataclass(frozen=True)
class JobSpec:
    """Registered maintenance job metadata."""

    name: str
    owner: str
    cadence: str
    idempotency: str
    run: JobCallable
    interval_setting: str | None = None


class UnknownJobError(ValueError):
    """Raised when the requested job name is not registered."""


class JobConfigError(ValueError):
    """Raised when a job's settings-backed configuration is invalid."""


def _acquire_job_lock(db: Session, job_name: str) -> bool:
    # Class ID namespaces this feature from other hashtext-backed locks.
    # hashtext() still returns int4, so job names can theoretically collide
    # within the run-job namespace.
    result = db.execute(
        text(
            "SELECT pg_try_advisory_xact_lock("
            "CAST(:classid AS int4), CAST(hashtext(:job_name) AS int4)"
            ")"
        ),
        {"classid": RUN_JOB_LOCK_CLASSID, "job_name": job_name},
    )
    return bool(result.scalar())


def _default_session_factory() -> Session:
    from app.infra.db import SessionLocal

    return SessionLocal()


def _run_sourcing_cache_sweep(db: Session) -> int:
    from app.domain.sourcing.cache import sweep_expired_all_workspaces

    return sweep_expired_all_workspaces(db)


def _run_sourcing_alerts_evaluate(db: Session) -> int:
    from app.domain.sourcing.alerts_evaluator import evaluate_all_alerts

    return evaluate_all_alerts(db)


def _run_session_purge(db: Session) -> int:
    from app.core.auth import purge_expired_sessions

    return purge_expired_sessions(db)


def _run_password_reset_purge(db: Session) -> int:
    from app.core.auth import purge_password_reset_requests

    return purge_password_reset_requests(db)


JOBS: dict[str, JobSpec] = {
    "sourcing-cache-sweep": JobSpec(
        name="sourcing-cache-sweep",
        owner="backend/sourcing",
        cadence="hourly",
        idempotency="Deletes only rows whose expires_at is already in the past.",
        run=_run_sourcing_cache_sweep,
    ),
    "sourcing-alerts-evaluate": JobSpec(
        name="sourcing-alerts-evaluate",
        owner="backend/sourcing",
        cadence="every 15 minutes",
        idempotency=(
            "Reads enabled, non-archived sourcing_alerts; for each, compares "
            "current state to threshold; sends one notification per transition "
            "with cooldown enforced via last_notified_at. Re-running within "
            "cooldown is a no-op."
        ),
        run=_run_sourcing_alerts_evaluate,
    ),
    "session-purge": JobSpec(
        name="session-purge",
        owner="backend/auth-security",
        cadence="hourly (configurable)",
        idempotency="Deletes only session rows whose expires_at is already in the past.",
        run=_run_session_purge,
        interval_setting="SESSION_PURGE_INTERVAL_SECONDS",
    ),
    "password-reset-purge": JobSpec(
        name="password-reset-purge",
        owner="backend/auth-security",
        cadence="hourly (configurable)",
        idempotency="Deletes only password-reset request rows older than 30 days.",
        run=_run_password_reset_purge,
        interval_setting="PASSWORD_RESET_PURGE_INTERVAL_SECONDS",
    ),
}


def _get_job(job_name: str, jobs: Mapping[str, JobSpec]) -> JobSpec:
    job = jobs.get(job_name)
    if job is None:
        available = ", ".join(sorted(jobs)) or "(none)"
        raise UnknownJobError(f"unknown job {job_name!r}; available jobs: {available}")
    return job


def _job_interval_seconds(job: JobSpec) -> int | None:
    if job.interval_setting is None:
        return None

    from app.core.config import settings

    try:
        interval = int(getattr(settings(), job.interval_setting))
    except ValidationError as exc:
        raise JobConfigError(str(exc)) from exc
    if interval < 0:
        raise JobConfigError(f"{job.interval_setting} must be greater than or equal to 0")
    return interval


def job_interval_seconds(
    job_name: str,
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
) -> int:
    """Return the settings-backed interval for a scheduled job."""
    job = _get_job(job_name, jobs)
    interval = _job_interval_seconds(job)
    if interval is None:
        raise JobConfigError(f"job {job.name!r} does not define a settings interval")
    return interval


def heartbeat_is_fresh(
    job_name: str,
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
    heartbeat_dir: Path = HEARTBEAT_DIR,
    max_age_seconds: int = HEARTBEAT_MAX_AGE_SECONDS,
) -> bool:
    """Return True when a scheduled job is disabled or has a fresh heartbeat."""
    job = _get_job(job_name, jobs)
    interval = _job_interval_seconds(job)
    if interval is None:
        raise JobConfigError(f"job {job.name!r} does not define a settings interval")
    if interval == 0:
        return True

    heartbeat_path = heartbeat_dir / job.name
    try:
        heartbeat_mtime = heartbeat_path.stat().st_mtime
    except FileNotFoundError:
        return False
    return time.time() - heartbeat_mtime <= max_age_seconds


def run_job(
    job_name: str,
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
    session_factory: SessionFactory = _default_session_factory,
    heartbeat_dir: Path = HEARTBEAT_DIR,
) -> int:
    """Run one registered job and return the job's affected-row count."""
    job = _get_job(job_name, jobs)
    _job_interval_seconds(job)

    db = session_factory()
    try:
        if not _acquire_job_lock(db, job.name):
            db.rollback()
            logger.info("job=%s status=skipped reason=lock_denied", job.name)
            return 0
        affected = job.run(db)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("job=%s status=error", job.name)
        raise
    finally:
        db.close()

    _write_heartbeat(job.name, heartbeat_dir=heartbeat_dir)
    logger.info(
        "job=%s status=ok affected=%s cadence=%s owner=%s",
        job.name,
        affected,
        job.cadence,
        job.owner,
    )
    return affected


def _write_heartbeat(job_name: str, *, heartbeat_dir: Path = HEARTBEAT_DIR) -> None:
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_path = heartbeat_dir / job_name
    tmp_path = heartbeat_dir / f".{job_name}.{uuid4().hex}.tmp"
    tmp_path.write_text("ok\n", encoding="utf-8")
    tmp_path.replace(heartbeat_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an allow-listed backend job.")
    parser.add_argument("job_name", help="Registered job name, e.g. sourcing-cache-sweep")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--print-interval",
        action="store_true",
        help="Print the settings-backed interval for this scheduled job.",
    )
    mode.add_argument(
        "--check-heartbeat",
        action="store_true",
        help="Exit successfully when this scheduled job is disabled or healthy.",
    )
    parser.add_argument(
        "--heartbeat-max-age-seconds",
        type=int,
        default=HEARTBEAT_MAX_AGE_SECONDS,
        help="Maximum heartbeat age accepted by --check-heartbeat.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
    session_factory: SessionFactory = _default_session_factory,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        if args.print_interval:
            print(job_interval_seconds(args.job_name, jobs=jobs))
            return 0
        if args.check_heartbeat:
            if heartbeat_is_fresh(
                args.job_name,
                jobs=jobs,
                max_age_seconds=args.heartbeat_max_age_seconds,
            ):
                return 0
            print(f"job={args.job_name} status=unhealthy reason=heartbeat", file=sys.stderr)
            return 1
        run_job(args.job_name, jobs=jobs, session_factory=session_factory)
    except (UnknownJobError, JobConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
