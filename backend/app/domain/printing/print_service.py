"""cab SQUIX TCP print service — transport + print-job lifecycle.

Ported from the sibling skladVA project (/mnt/data/WORK/sklad,
``backend/app/printing/print_service.py``), adapted to this codebase's
settings/logging/time helpers and, crucially, made **workspace-aware**: skladVA
is single-tenant, this project is multi-tenant, so the job ledger is scoped by
``workspace_id`` and idempotency dedupes *within* a workspace.

Opens a raw TCP socket to ``PRINT_HOST:PRINT_PORT`` (cab SQUIX raw port 9100)
and writes rendered JScript through the vendored :class:`CabPrinter` driver. The
``print_jobs`` row is the *reconciliation point*: a physical print and a DB
write cannot be one atomic transaction, so the job tracks the attempt through
``queued -> sent -> printed | failed`` and on failure the caller registers
nothing.

Scope of THIS foundation module
-------------------------------
Transport + lifecycle over *pre-rendered* JScript only. The label renderer (the
object-code / template layer) is a later PR, so skladVA's render-coupled
helpers (``PrintJobSpec`` / ``_render`` / ``send`` / ``enqueue_and_print``) are
intentionally NOT ported yet — callers that already hold JScript use
:func:`send_jscript` / :func:`send_jscript_batch`, and the deferred-dispatch and
reconciliation sweeps operate on the stored ``payload``.

Boundaries
----------
- This module owns the transport and the job-status transitions, not the HTTP
  shape (there is no route in this PR).
- It does NOT commit. Status mutations flush within the caller's transaction so
  the print attempt and any business change commit or roll back together. The
  ``get_db`` dependency owns the commit boundary.
- ``PRINT_HOST`` empty (the dev/CI/test default) means *no print sink*:
  :func:`send_jscript` raises :class:`PrinterUnreachable` so callers exercise
  the failure path deterministically. Tests point ``PRINT_HOST`` at a stub TCP
  listener to drive the success path.

Idempotency
-----------
Dedupe is keyed on ``(workspace_id, idempotency_key)`` (composite UNIQUE).
:func:`get_or_create_job` returns the existing job for a repeated key rather
than creating a second one, so a retried print never prints twice.
"""

from __future__ import annotations

import datetime
import uuid
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.domain.printing.cab_squix import CabPrinter, PrinterError
from app.domain.printing.models import PrintJob

_log = get_logger(__name__)

# Job lifecycle states. Kept as constants so callers never typo a status the DB
# CHECK constraint would later reject.
STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_PRINTED = "printed"
STATUS_FAILED = "failed"

# Job kinds (mirror the print_jobs CHECK constraint).
KIND_ON_DEMAND = "on_demand"
KIND_BATCH_BLANK = "batch_blank"

_VALID_KINDS = (KIND_ON_DEMAND, KIND_BATCH_BLANK)
_TERMINAL_STATUSES = (STATUS_PRINTED, STATUS_FAILED)

# TCP connect + write timeout (seconds). The printer is on the same LAN/VPN, so
# a few seconds is generous; beyond it we treat the printer as unreachable
# rather than hanging the request.
_SOCKET_TIMEOUT_SECONDS = 5.0

# Batch delivery sizing. Concatenating labels into one connection avoids the
# connect + poll-to-idle cost per label (the slow "one by one"); chunking bounds
# the printer's receive buffer and the blast radius of a mid-run media error.
_BATCH_CHUNK_SIZE = 25
# Completion-poll budget for a chunk scales with its label count (each label
# takes the printer a second or two to feed + burn).
_BATCH_BASE_WAIT_SECONDS = 30.0
_BATCH_PER_LABEL_WAIT_SECONDS = 4.0

# Reconciliation staleness window. A job stuck in 'sent' beyond this is assumed
# to have lost its outcome (process crash / network drop after the bytes left
# but before the status was advanced) and is marked 'failed' so it stops being
# treated as in-flight.
_STALE_SENT_THRESHOLD = datetime.timedelta(minutes=5)

# Cap on the persisted error string so a verbose driver message can't bloat the
# row.
_MAX_ERROR_CHARS = 1000


class PrinterUnreachable(RuntimeError):
    """Raised when the JScript could not be delivered to the printer.

    Covers an unconfigured sink (no ``PRINT_HOST``), a refused/timed-out TCP
    connection, a printer error state, and a write error. The caller marks the
    ``print_jobs`` row ``failed`` and registers nothing.
    """


def _write_to_printer(payload: str, *, max_wait: float = 60.0) -> None:
    """Deliver ``payload`` (JScript) to the configured cab SQUIX printer.

    Routes through the vendored :class:`CabPrinter` driver: a status preflight
    (ESCs) confirms the printer is idle and not in an error state, the JScript
    is written, then the driver polls until the interpreter goes idle (job
    complete). Any failure — unconfigured host, refused/timed-out connection, a
    printer error state, or a job that never finishes — is normalised to
    :class:`PrinterUnreachable` so the caller marks the job ``failed``. The
    driver opens and closes the socket per call.
    """
    cfg = settings()
    host = (cfg.PRINT_HOST or "").strip()
    if not host:
        # No print sink configured (dev/CI/test default). Fail closed so the
        # reconciliation path is exercised rather than silently "succeeding".
        raise PrinterUnreachable("no print host configured (PRINT_HOST is empty)")

    port = cfg.PRINT_PORT
    printer = CabPrinter(host, port, timeout=_SOCKET_TIMEOUT_SECONDS)
    try:
        # preflight (status query) + write + completion polling, all inside the
        # driver. PrinterError covers offline/error-state/job-failed/timeout;
        # OSError covers the transport (connection refused, timeout, DNS).
        printer.send_job(payload, max_wait=max_wait)
    except PrinterError as exc:
        raise PrinterUnreachable(
            f"printer {host}:{port} rejected or failed the job: {exc}"
        ) from exc
    except OSError as exc:  # connection refused, timeout, DNS, write error
        raise PrinterUnreachable(
            f"could not deliver label to printer {host}:{port}: {exc}"
        ) from exc


def get_or_create_job(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    idempotency_key: str,
    kind: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: str | None = None,
) -> tuple[PrintJob, bool]:
    """Return ``(job, created)`` for ``(workspace_id, idempotency_key)``.

    If a job already exists for the key **within this workspace** it is returned
    unchanged with ``created=False`` (idempotent replay). Otherwise a fresh
    ``queued`` job is inserted and flushed within the caller's transaction. A
    concurrent insert racing on the composite UNIQUE key is resolved by
    re-reading the winner.

    The caller owns the commit; this only flushes so the job gets its id.
    """
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown print-job kind: {kind!r}")

    key = idempotency_key.strip()
    existing = _find_job_by_key(db, workspace_id, key)
    if existing is not None:
        return existing, False

    job = PrintJob(
        workspace_id=workspace_id,
        idempotency_key=key,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        status=STATUS_QUEUED,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        # Lost the UNIQUE(workspace_id, idempotency_key) race — roll back this
        # insert and return the winning row so the retry dedupes to one job.
        db.rollback()
        winner = _find_job_by_key(db, workspace_id, key)
        if winner is None:
            raise
        return winner, False
    return job, True


def _find_job_by_key(
    db: Session, workspace_id: uuid.UUID, key: str
) -> PrintJob | None:
    """Look up a job by idempotency key, SCOPED to the workspace.

    The workspace filter is the isolation boundary: workspace B must never
    dedupe against — or even observe — workspace A's job for the same key.
    """
    return (
        db.execute(
            select(PrintJob).where(
                PrintJob.workspace_id == workspace_id,
                PrintJob.idempotency_key == key,
            )
        )
        .scalars()
        .first()
    )


def send_jscript(db: Session, job: PrintJob, jscript_text: str) -> PrintJob:
    """Transmit pre-rendered ``jscript_text`` for ``job``, walking its lifecycle.

    Marks the job ``sent`` before the hand-off, ``printed`` on success, and
    ``failed`` (recording the error) on a transport failure. It is a no-op on a
    job already in a terminal state so a reconciliation re-send never double
    prints. Status changes flush within the caller's transaction.

    Raises :class:`PrinterUnreachable` on a transport failure.
    """
    if job.status in _TERMINAL_STATUSES:
        return job

    job.status = STATUS_SENT
    db.flush()
    try:
        _write_to_printer(jscript_text)
    except PrinterUnreachable as exc:
        job.status = STATUS_FAILED
        job.error = str(exc)[:_MAX_ERROR_CHARS]
        db.flush()
        _log.warning(
            "print job failed",
            extra={"print_job_id": str(job.id), "workspace_id": str(job.workspace_id)},
        )
        raise
    job.status = STATUS_PRINTED
    job.error = None
    db.flush()
    _log.info(
        "print job printed",
        extra={"print_job_id": str(job.id), "workspace_id": str(job.workspace_id)},
    )
    return job


def send_jscript_batch(
    db: Session,
    jobs_texts: list[tuple[PrintJob, str]],
    *,
    chunk_size: int = _BATCH_CHUNK_SIZE,
) -> int:
    """Transmit many pre-rendered labels in as few connections as possible.

    Each ``(job, jscript_text)`` pair is a self-contained cab program (``J`` …
    ``A``). Sending one per connection means a preflight + write + poll-to-idle
    per label — the slow "one by one". Instead we concatenate labels into chunks
    of ``chunk_size`` and deliver each chunk over a SINGLE connection: one
    preflight, one write, one poll until the printer finishes the whole chunk.
    The printer streams the labels back-to-back (TCP flow-control is the
    backpressure).

    Lifecycle mirrors :func:`send_jscript`: each job goes ``sent`` before its
    chunk is written, then ``printed`` on success. On a chunk transport failure
    every not-yet-printed job (this chunk and all remaining) is marked ``failed``
    with the error and delivery stops — a mid-run failure can leave the printed
    count for an in-flight chunk uncertain, so we do not over-claim. Jobs already
    terminal are skipped. Returns the number of jobs printed. Does not raise on a
    transport failure (the outcome is recorded on the job rows).
    """
    pending = [
        (job, text)
        for job, text in jobs_texts
        if job.status not in _TERMINAL_STATUSES
    ]
    printed = 0
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start : start + chunk_size]
        payload = "\r\n".join(text for _job, text in chunk)
        for job, _text in chunk:
            job.status = STATUS_SENT
        db.flush()
        max_wait = _BATCH_BASE_WAIT_SECONDS + _BATCH_PER_LABEL_WAIT_SECONDS * len(chunk)
        try:
            _write_to_printer(payload, max_wait=max_wait)
        except PrinterUnreachable as exc:
            # Fail this chunk AND every remaining pending job so each row ends in
            # a terminal state and the reported count never exceeds reality.
            for job, _text in pending[start:]:
                job.status = STATUS_FAILED
                job.error = str(exc)[:_MAX_ERROR_CHARS]
            db.flush()
            _log.warning(
                "print batch chunk failed",
                extra={"chunk_labels": len(chunk), "remaining": len(pending) - start},
            )
            break
        for job, _text in chunk:
            job.status = STATUS_PRINTED
            job.error = None
        printed += len(chunk)
        db.flush()
        _log.info("print batch chunk printed", extra={"chunk_labels": len(chunk)})
    return printed


def dispatch_queued_batch(
    db: Session,
    *,
    workspace_id: uuid.UUID | None = None,
    max_jobs: int = 200,
) -> int:
    """Ship queued batch labels to the printer — the cron dispatcher.

    Batch printing enqueues each label as a ``queued`` ``batch_blank`` job with
    its rendered JScript stored in ``payload`` and returns immediately, so a big
    batch never blocks/times out the HTTP client. This runs off the request path
    (in the cron loop) and ships up to ``max_jobs`` queued jobs via
    :func:`send_jscript_batch` (chunked, one connection per chunk). Returns the
    number printed this pass; leftover jobs are picked up on the next tick.

    ``workspace_id`` optionally scopes the sweep to one workspace; ``None`` (the
    default) is the cross-workspace maintenance sweep the cron sidecar runs,
    analogous to the session-purge job.
    """
    stmt = (
        select(PrintJob)
        .where(
            PrintJob.status == STATUS_QUEUED,
            PrintJob.kind == KIND_BATCH_BLANK,
            PrintJob.payload.is_not(None),
        )
        .order_by(PrintJob.created_at, PrintJob.id)
        .limit(max_jobs)
    )
    if workspace_id is not None:
        stmt = stmt.where(PrintJob.workspace_id == workspace_id)
    rows = db.execute(stmt).scalars().all()
    if not rows:
        return 0
    return send_jscript_batch(db, [(job, job.payload or "") for job in rows])


def reconcile_stale_print_jobs(
    db: Session,
    *,
    workspace_id: uuid.UUID | None = None,
    threshold: datetime.timedelta = _STALE_SENT_THRESHOLD,
) -> int:
    """Mark print jobs stuck in ``sent`` past ``threshold`` as ``failed``.

    Maintenance job. A job whose bytes were handed to the printer (``sent``) but
    whose terminal outcome was never recorded — the worker crashed, the
    connection dropped — would otherwise linger forever as in-flight. This
    sweeps such rows to ``failed`` with a reconciliation note so they are
    visibly resolved and never falsely treated as still printing.

    ``workspace_id`` optionally scopes the sweep; ``None`` (the default) is the
    cross-workspace maintenance sweep. Returns the number reconciled. The caller
    owns the commit; this only issues the bulk UPDATE within that transaction.
    """
    cutoff = utcnow() - threshold
    stmt = (
        update(PrintJob)
        .where(PrintJob.status == STATUS_SENT, PrintJob.updated_at < cutoff)
        .values(status=STATUS_FAILED, error="reconciled: stuck in 'sent'")
        .execution_options(synchronize_session=False)
    )
    if workspace_id is not None:
        stmt = stmt.where(PrintJob.workspace_id == workspace_id)
    result = db.execute(stmt)
    affected = cast(CursorResult, result).rowcount or 0
    if affected:
        _log.warning("reconciled stale print jobs", extra={"count": affected})
    return affected


__all__ = [
    "PrinterUnreachable",
    "STATUS_QUEUED",
    "STATUS_SENT",
    "STATUS_PRINTED",
    "STATUS_FAILED",
    "KIND_ON_DEMAND",
    "KIND_BATCH_BLANK",
    "get_or_create_job",
    "send_jscript",
    "send_jscript_batch",
    "dispatch_queued_batch",
    "reconcile_stale_print_jobs",
]
