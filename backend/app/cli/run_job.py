"""Allow-listed backend maintenance job runner."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

# Re-exported: every existing caller and test imports these from here,
# and which of the three CLI modules a name is DEFINED in is not a fact
# they should have to track.
from app.cli.run_job_operator import (
    EXTRA_FLAGS as _EXTRA_FLAGS,
)
from app.cli.run_job_operator import (
    add_operator_arguments,
    run_category_seed_job,
    run_part_rename_job,
    run_provider_refresh_job,
    run_spec_normalize_job,
    run_symbol_collapse_job,
)
from app.cli.run_job_options import (
    JobConfigError,
    JobHaltedError,
    JobOptions,
    UnknownJobError,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "JOBS",
    "JobConfigError",
    "JobHaltedError",
    "JobOptions",
    "JobSpec",
    "UnknownJobError",
    "heartbeat_is_fresh",
    "job_interval_seconds",
    "main",
    "run_job",
]

# Nothing from sqlalchemy or app.core is imported at module level on purpose.
# The compose healthchecks for backend-cron-sessions and backend-cron-datasheets
# run `python -m app.cli.run_job --check-all-heartbeats ...` on a 0.5-CPU
# sidecar with a short timeout; the probe only stats heartbeat files, and a
# cold `import sqlalchemy` there was measured at tens of seconds under host
# load. `tests/test_run_job_probe_imports.py` pins this.

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], "Session"]
# A scheduled job takes the session alone; an operator-run job takes the
# session and its flags. Spelling both out rather than `Callable[..., int]`
# keeps the two shapes checkable — `tests/test_run_job_options.py::
# test_every_job_signature_matches_its_takes_options_flag` reads the real
# signature off each registered job and fails if the flag lies about it.
ScheduledJob = Callable[["Session"], int]
OperatorJob = Callable[["Session", "JobOptions"], int]
JobCallable = ScheduledJob | OperatorJob
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
    #: Whether `run` takes a second `JobOptions` argument, i.e. whether
    #: this job is operator-run (`--apply` / `--workspace` / `--report`)
    #: rather than scheduled.
    takes_options: bool = False
    #: Whether `--apply` is refused without `--report`. Set by a job that
    #: rewrites values IN PLACE, where the CSV is the only record of what
    #: they were and the rollback procedure reads it. A job that only
    #: creates rows (`category-seed`) or clears a nullable column
    #: (`symbol-collapse`) can be undone from the schema alone and leaves
    #: this False.
    requires_report: bool = False
    #: Flag names beyond the common four that this job reads, as
    #: `JobOptions` attribute names. The parser is shared, so a flag only
    #: one job understands is accepted by argparse and then refused for
    #: every job that does not list it here — silently ignoring it is the
    #: failure mode this whole surface is built to avoid.
    extra_flags: tuple[str, ...] = ()


def _acquire_job_lock(db: Session, job_name: str) -> bool:
    from sqlalchemy import text

    from app.core.advisory_locks import RUN_JOB_LOCK_CLASSID

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


def _run_datasheet_backfill(db: Session) -> int:
    # Not gated on a host setting the way print-dispatch is: the job's own
    # DATASHEET_BACKFILL_INTERVAL_SECONDS=0 short-circuits it to a no-op
    # inside the service, and the sidecar's --print-interval loop never
    # starts it in the first place.
    from app.domain.parts.services.datasheets import backfill_missing_datasheets

    return backfill_missing_datasheets(db)


def _printing_is_configured() -> bool:
    """True when a print sink is configured (``PRINT_HOST`` non-empty)."""
    from app.core.config import settings

    return bool((settings().PRINT_HOST or "").strip())


def _run_print_dispatch(db: Session) -> int:
    # Gated on PRINT_HOST because this job is the one that actually talks to
    # the printer. With no sink configured (the default until the VPS-side
    # tunnel + ufw rule exist — see docs/deployment.md "Label printer
    # connectivity") send_jscript_batch would mark every queued job `failed`
    # against a printer that was never meant to be reachable yet. Returning 0
    # keeps the sidecar loop a harmless no-op and leaves the queue intact for
    # whenever printing is switched on.
    if not _printing_is_configured():
        return 0

    from app.domain.printing.print_service import dispatch_queued_batch

    return dispatch_queued_batch(db)


def _run_print_job_reconcile(db: Session) -> int:
    # Deliberately NOT gated on PRINT_HOST: this is pure DB bookkeeping and
    # never opens a socket. Turning printing off must still resolve jobs left
    # stuck in `sent`, which is exactly the state this sweep exists to clear.
    from app.domain.printing.print_service import reconcile_stale_print_jobs

    return reconcile_stale_print_jobs(db)


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
    "print-dispatch": JobSpec(
        name="print-dispatch",
        owner="backend/printing",
        cadence="every 60 seconds",
        idempotency=(
            "Ships only queued batch_blank jobs that still carry a payload; "
            "each job leaves the pass in a terminal status (printed or "
            "failed), so a re-run never re-sends the same label. A no-op "
            "returning 0 when PRINT_HOST is empty (printing disabled)."
        ),
        run=_run_print_dispatch,
    ),
    "datasheet-backfill": JobSpec(
        name="datasheet-backfill",
        owner="backend/parts",
        cadence="hourly (configurable)",
        idempotency=(
            "Processes at most DATASHEET_BACKFILL_BATCH_SIZE parts whose "
            "datasheet_url has no stored part_datasheets row. A stored row is "
            "never re-downloaded; a failure increments attempts and starts a "
            "retry cooldown, so re-running resumes rather than repeating. A "
            "no-op returning 0 when DATASHEET_BACKFILL_INTERVAL_SECONDS is 0."
        ),
        run=_run_datasheet_backfill,
        interval_setting="DATASHEET_BACKFILL_INTERVAL_SECONDS",
    ),
    "category-seed": JobSpec(
        name="category-seed",
        owner="backend/categories",
        cadence="manual (operator-run)",
        idempotency=(
            "Creates only categories the workspace is missing, matched by "
            "name under the expected parent, and fills a KiCad metadata "
            "field only where it is still unset. Never renames, re-parents "
            "or overwrites. A second run creates nothing. Writes nothing at "
            "all without --apply."
        ),
        run=run_category_seed_job,
        takes_options=True,
    ),
    "symbol-collapse": JobSpec(
        name="symbol-collapse",
        owner="backend/eda",
        cadence="manual (operator-run)",
        idempotency=(
            "Clears part_eda.symbol_id only where the symbol came from a "
            "vendor zip and the part's category has a default_symbol_ref to "
            "fall back to. A cleared row no longer matches, so a second run "
            "finds nothing. Writes nothing at all without --apply."
        ),
        run=run_symbol_collapse_job,
        takes_options=True,
    ),
    "spec-normalize": JobSpec(
        name="spec-normalize",
        owner="backend/parts",
        cadence="manual (operator-run, one-off backfill)",
        idempotency=(
            "Re-keys existing provider custom_fields onto the canonical spec "
            "schema (ADR-0034) and files uncategorized parts from the "
            "provider's own taxonomy. A row already carrying its canonical "
            "key, parsed value, provider and value_num is not a change, so a "
            "second run reports 0. Nothing is deleted: junk keys and "
            "placeholder values are archived. Writes nothing at all without "
            "--apply, which it refuses without --report."
        ),
        run=run_spec_normalize_job,
        takes_options=True,
        requires_report=True,
    ),
    "part-rename": JobSpec(
        name="part-rename",
        owner="backend/parts",
        cadence="manual (operator-run)",
        idempotency=(
            "Classifies every active part against the naming convention "
            "(domain/parts/naming.py) and renames only those that do not "
            "already match, so a second run proposes nothing. Text a rename "
            "would overwrite is parked in the part's `alias` custom field; a "
            "part that already has an `alias` is skipped rather than renamed, "
            "and free-text names are skipped unless --include-free. Writes "
            "nothing at all without --apply, which it refuses without "
            "--report."
        ),
        run=run_part_rename_job,
        takes_options=True,
        # It rewrites `parts.name` in place. The CSV is the operator's
        # record of what those names were — the `alias` field covers most
        # classes but not a part renamed off its own MPN.
        requires_report=True,
        extra_flags=("include_free",),
    ),
    "provider-refresh": JobSpec(
        name="provider-refresh",
        owner="backend/parts",
        cadence="manual (operator-run)",
        idempotency=(
            "Re-runs the provider MPN lookup for every active, linked part "
            "with an MPN and writes back what the providers answer now, "
            "through the same service the refresh route uses. A second run "
            "over unchanged upstream data rewrites no part column and no "
            "spec row; `last_refresh_at` and the link's own timestamp move "
            "every time, because they are the record that the run happened. "
            "With --include-unlinked it also visits parts no provider has "
            "ever been linked to, where the primary fills only the columns "
            "the part left empty. "
            "Junk archived by spec-normalize stays archived. Writes nothing "
            "at all without --apply, which it refuses without --report. "
            "Stops at exit 3 when a provider reports it is out of quota, "
            "keeping everything committed up to that point."
        ),
        run=run_provider_refresh_job,
        takes_options=True,
        # It rewrites `parts.manufacturer` / `description` / `footprint`
        # and spec values in place from a remote payload. The CSV is the
        # operator's only record of what they were.
        requires_report=True,
        extra_flags=(
            "limit",
            "only_uncategorized",
            "link_missing_providers",
            "include_unlinked",
            "sleep_ms",
        ),
    ),
    "print-job-reconcile": JobSpec(
        name="print-job-reconcile",
        owner="backend/printing",
        cadence="every 5 minutes",
        idempotency=(
            "Marks only jobs still in 'sent' whose updated_at is older than "
            "the staleness window as failed. Rows already terminal are not "
            "matched, so re-running changes nothing."
        ),
        run=_run_print_job_reconcile,
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

    return int(getattr(settings(), job.interval_setting))


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
    if job.takes_options:
        # Operator-run: nothing schedules it, so it has no cadence to be
        # late for and writes no heartbeat. Healthy by definition — and
        # answering rather than raising is what lets a monitor iterate
        # `list(JOBS)` without knowing which kind each one is.
        return True
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


def all_heartbeats_are_fresh(
    job_names: list[str],
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
    heartbeat_dir: Path = HEARTBEAT_DIR,
    max_age_seconds: int = HEARTBEAT_MAX_AGE_SECONDS,
) -> bool:
    """Return True when every scheduled job is disabled or has a fresh heartbeat."""
    return all(
        heartbeat_is_fresh(
            job_name,
            jobs=jobs,
            heartbeat_dir=heartbeat_dir,
            max_age_seconds=max_age_seconds,
        )
        for job_name in job_names
    )


def run_job(
    job_name: str,
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
    session_factory: SessionFactory = _default_session_factory,
    heartbeat_dir: Path = HEARTBEAT_DIR,
    options: JobOptions | None = None,
) -> int:
    """Run one registered job and return the job's affected-row count.

    `options` is accepted only by jobs that declare `takes_options`;
    passing it to a scheduled job is a `JobConfigError` rather than a
    silently ignored flag, so `--apply` can never look like it worked.

    A dry run ends in ROLLBACK, not COMMIT. The jobs plan without
    writing, so this is the second of two independent guards — the one
    that holds even if a job forgets to check the flag.
    """
    job = _get_job(job_name, jobs)
    _job_interval_seconds(job)
    if options is not None and not job.takes_options:
        raise JobConfigError(
            f"job {job.name!r} is scheduled and takes no "
            "--dry-run / --apply / --workspace / --report options"
        )
    # Defaulted here rather than narrowed at the call site: an
    # operator-run job always receives a `JobOptions`, and "no flags"
    # means the dry run.
    job_options = options if options is not None else JobOptions()

    db = session_factory()
    try:
        if not _acquire_job_lock(db, job.name):
            db.rollback()
            _write_heartbeat(job, heartbeat_dir=heartbeat_dir)
            logger.info("job=%s status=skipped reason=lock_denied", job.name)
            return 0
        if job.takes_options:
            affected = job.run(db, job_options)
            if job_options.apply:
                db.commit()
            else:
                db.rollback()
                logger.info("job=%s status=dry_run would_affect=%s", job.name, affected)
        else:
            affected = job.run(db)
            db.commit()
    except JobHaltedError:
        # The job stopped on purpose, after writing its report and (on
        # --apply) committing what it finished. The rollback is a no-op
        # on an already-committed session and the correct answer on a dry
        # run; either way the exception carries the reason to `main`.
        db.rollback()
        logger.warning("job=%s status=halted", job.name)
        raise
    except Exception:
        db.rollback()
        logger.exception("job=%s status=error", job.name)
        raise
    finally:
        db.close()

    _write_heartbeat(job, heartbeat_dir=heartbeat_dir)
    logger.info(
        "job=%s status=ok affected=%s cadence=%s owner=%s",
        job.name,
        affected,
        job.cadence,
        job.owner,
    )
    return affected


def _write_heartbeat(job: JobSpec, *, heartbeat_dir: Path = HEARTBEAT_DIR) -> None:
    """Record that a SCHEDULED job ran.

    Operator-run jobs write none. A heartbeat is the answer to "is the
    cadence still being met", and a job a human runs by hand has no
    cadence — a file saying it ran once last March would be read as
    healthy, and its absence read as broken. `heartbeat_is_fresh` has
    the matching rule.
    """
    if job.takes_options:
        return
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_path = heartbeat_dir / job.name
    tmp_path = heartbeat_dir / f".{job.name}.{uuid4().hex}.tmp"
    tmp_path.write_text("ok\n", encoding="utf-8")
    tmp_path.replace(heartbeat_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an allow-listed backend job.")
    parser.add_argument(
        "job_name",
        nargs="?",
        help="Registered job name, e.g. sourcing-cache-sweep",
    )
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
    mode.add_argument(
        "--check-all-heartbeats",
        nargs="+",
        metavar="JOB_NAME",
        help=(
            "Exit successfully when every listed scheduled job is disabled or "
            "healthy. Loads backend settings once for the full probe."
        ),
    )
    parser.add_argument(
        "--heartbeat-max-age-seconds",
        type=int,
        default=None,
        help="Maximum heartbeat age accepted by heartbeat checks.",
    )
    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Report what an operator-run job would change and write nothing. "
            "This is the default; the flag exists to be explicit in a runbook."
        ),
    )
    run_mode.add_argument(
        "--apply",
        action="store_true",
        help="Commit the changes an operator-run job reports. Default is a dry run.",
    )
    parser.add_argument(
        "--workspace",
        metavar="UUID",
        default=None,
        help="Limit an operator-run job to one workspace. Default is all of them.",
    )
    parser.add_argument(
        "--report",
        metavar="PATH",
        type=Path,
        default=None,
        help=(
            "Write an operator-run job's CSV report to this file instead of "
            "stdout. Written on a dry run too — the report is the point of one."
        ),
    )
    add_operator_arguments(parser)
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.heartbeat_max_age_seconds is not None and not (
        args.check_heartbeat or args.check_all_heartbeats
    ):
        parser.error(
            "--heartbeat-max-age-seconds requires --check-heartbeat "
            "or --check-all-heartbeats"
        )
    if args.heartbeat_max_age_seconds is None:
        args.heartbeat_max_age_seconds = HEARTBEAT_MAX_AGE_SECONDS
    for flag, value in (("--limit", args.limit), ("--sleep-ms", args.sleep_ms)):
        # A negative limit reaches Postgres as `LIMIT -1` and a negative
        # pause would silently clamp to zero. Both are typos, and an
        # operator running a job against a metered API deserves to hear
        # about a typo rather than discover it in the report.
        if value is not None and value < 0:
            parser.error(f"{flag} must not be negative")
    if args.workspace is not None:
        try:
            args.workspace = UUID(args.workspace)
        except ValueError:
            parser.error(f"--workspace is not a UUID: {args.workspace}")
    # The probes answer a question about a job's configuration and never
    # run it, so an --apply next to one would be silently discarded —
    # the exact failure mode `run_job` refuses for scheduled jobs.
    if _job_flags_given(args) and (
        args.print_interval or args.check_heartbeat or args.check_all_heartbeats
    ):
        parser.error(
            "--dry-run / --apply / --workspace / --report and the job-specific "
            "flags (--include-free, --limit, --only-uncategorized, "
            "--link-missing-providers, --sleep-ms) cannot be combined with "
            "--print-interval or a heartbeat check"
        )
    return args


def _job_flags_given(args: argparse.Namespace) -> bool:
    """Whether the operator passed any job flag at all.

    One definition, because `_parse_args` and `_options_for` ask the same
    question and a flag added to only one of them is a flag that is
    either silently dropped or wrongly refused next to a health probe.
    """
    return bool(
        args.apply
        or args.dry_run
        or args.workspace is not None
        or args.report is not None
        or any(getattr(args, flag) != unset for flag, unset in _EXTRA_FLAGS.items())
    )


def _options_for(
    job_name: str, args: argparse.Namespace, jobs: Mapping[str, JobSpec]
) -> JobOptions | None:
    """`JobOptions` for an operator-run job, or None for a scheduled one.

    A scheduled job that was handed `--apply`, `--workspace` or
    `--report` gets the options object anyway, so `run_job` refuses it by
    name instead of dropping the flag on the floor.
    """
    job = _get_job(job_name, jobs)
    undeclared = [
        flag
        for flag, unset in _EXTRA_FLAGS.items()
        if getattr(args, flag) != unset and flag not in job.extra_flags
    ]
    if undeclared:
        # By name, not ignored. The parser is shared, so argparse accepts
        # a flag for every job; this is the only place that can tell the
        # operator the job they named does not read it.
        spelled = ", ".join(f"--{flag.replace('_', '-')}" for flag in undeclared)
        raise JobConfigError(f"job {job.name!r} takes no {spelled}")
    if not _job_flags_given(args) and not job.takes_options:
        return None
    if job.requires_report and args.apply and args.report is None:
        raise JobConfigError(
            f"job {job.name!r} requires --report with --apply: it rewrites "
            "values in place, and the CSV is the only record of what they were"
        )
    # Built from `_EXTRA_FLAGS` rather than named one by one: the
    # mapping already has to list every job-specific flag for the refusal
    # check above, and a flag present there and missing here would be
    # accepted, validated, and then silently dropped.
    return JobOptions(
        apply=args.apply,
        workspace_id=args.workspace,
        report=args.report,
        **{flag: getattr(args, flag) for flag in _EXTRA_FLAGS},
    )


def main(
    argv: list[str] | None = None,
    *,
    jobs: Mapping[str, JobSpec] = JOBS,
    session_factory: SessionFactory = _default_session_factory,
    heartbeat_dir: Path = HEARTBEAT_DIR,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        if args.check_all_heartbeats:
            if all_heartbeats_are_fresh(
                args.check_all_heartbeats,
                jobs=jobs,
                heartbeat_dir=heartbeat_dir,
                max_age_seconds=args.heartbeat_max_age_seconds,
            ):
                return 0
            print(
                "jobs="
                f"{','.join(args.check_all_heartbeats)} "
                "status=unhealthy reason=heartbeat",
                file=sys.stderr,
            )
            return 1
        if args.job_name is None:
            print("job_name is required unless --check-all-heartbeats is used", file=sys.stderr)
            return 2
        if args.print_interval:
            print(job_interval_seconds(args.job_name, jobs=jobs))
            return 0
        if args.check_heartbeat:
            if heartbeat_is_fresh(
                args.job_name,
                jobs=jobs,
                heartbeat_dir=heartbeat_dir,
                max_age_seconds=args.heartbeat_max_age_seconds,
            ):
                return 0
            print(f"job={args.job_name} status=unhealthy reason=heartbeat", file=sys.stderr)
            return 1
        run_job(
            args.job_name,
            jobs=jobs,
            session_factory=session_factory,
            heartbeat_dir=heartbeat_dir,
            options=_options_for(args.job_name, args, jobs),
        )
    except JobHaltedError as exc:
        # Not 0 and not 2: the run did real work, kept it, and stopped for
        # a reason that will still be true if it is retried immediately. A
        # runbook step and a monitor both need to tell that from a clean
        # finish and from a usage error.
        print(str(exc), file=sys.stderr)
        return 3
    except (UnknownJobError, JobConfigError, LookupError) as exc:
        # LookupError is what an operator-run job raises for a
        # `--workspace` that names nothing. Reporting it as a usage error
        # rather than letting it surface as a traceback is the point: the
        # alternative, an empty report and exit 0, reads as "nothing to
        # do" for what is actually a typo.
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
