"""Allow-listed backend maintenance job runner."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
JobCallable = Callable[[Session], int]
HEARTBEAT_DIR = Path("/tmp/stockmanager-job-heartbeats")
LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"


@dataclass(frozen=True)
class JobSpec:
    """Registered maintenance job metadata."""

    name: str
    owner: str
    cadence: str
    idempotency: str
    run: JobCallable


class UnknownJobError(ValueError):
    """Raised when the requested job name is not registered."""


def _is_lock_not_available(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    return (
        getattr(orig, "sqlstate", None) == LOCK_NOT_AVAILABLE_SQLSTATE
        or getattr(orig, "pgcode", None) == LOCK_NOT_AVAILABLE_SQLSTATE
    )


def _acquire_job_lock(db: Session, job_name: str) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:job_name))"),
        {"job_name": job_name},
    )


def _default_session_factory() -> Session:
    from app.infra.db import SessionLocal

    return SessionLocal()


def _run_sourcing_cache_sweep(db: Session) -> int:
    from app.domain.sourcing.cache import sweep_expired_all_workspaces

    return sweep_expired_all_workspaces(db)


def _run_sourcing_alerts_evaluate(db: Session) -> int:
    from app.domain.sourcing.alerts_evaluator import evaluate_all_alerts

    return evaluate_all_alerts(db)


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
}


def run_job(
    job_name: str,
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
    session_factory: SessionFactory = _default_session_factory,
    heartbeat_dir: Path = HEARTBEAT_DIR,
) -> int:
    """Run one registered job and return the job's affected-row count."""
    job = jobs.get(job_name)
    if job is None:
        available = ", ".join(sorted(jobs)) or "(none)"
        raise UnknownJobError(f"unknown job {job_name!r}; available jobs: {available}")

    db = session_factory()
    try:
        try:
            _acquire_job_lock(db, job.name)
        except DBAPIError as exc:
            if _is_lock_not_available(exc):
                db.rollback()
                logger.info("job=%s status=skipped reason=lock_denied", job.name)
                return 0
            raise
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
        run_job(args.job_name, jobs=jobs, session_factory=session_factory)
    except UnknownJobError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
