"""Lifecycle tests for the cab SQUIX print service (transport + print_jobs).

Ported from skladVA's ``tests/integration/test_printing.py`` test *approach*: a
**stub TCP listener** stands in for the physical cab SQUIX printer so both the
success and failure paths run without a real device, and failure is injected by
pointing the service at a closed port. Adapted to this project's foundation
surface — pre-rendered JScript through ``send_jscript`` /
``send_jscript_batch`` (no label renderer in this PR) — and to the multi-tenant
``print_jobs`` ledger (every job carries a ``workspace_id``).

Covers:
  * queued -> sent -> printed on a reachable printer,
  * failed + PrinterUnreachable on a refused connection,
  * fail-closed when ``PRINT_HOST`` is empty (the dev/CI/test default),
  * terminal-job re-send is a no-op (no double print),
  * idempotency-key dedupe within a workspace,
  * batch chunking (all printed / all failed),
  * the deferred-dispatch and stale-reconciliation maintenance sweeps.
"""

from __future__ import annotations

import datetime
import socket
import threading
import time
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.printing import print_service
from app.domain.printing.models import PrintJob
from app.domain.printing.print_service import PrinterUnreachable
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace

_ESC = b"\x1b"

# A minimal, self-contained cab program used wherever a test just needs *some*
# valid JScript to ship.
_SAMPLE_JSCRIPT = "m m\r\nJ\r\nS l1;0,0,68,71,104\r\nA 1\r\n"


# ---------------------------------------------------------------------------
# Stub TCP listener (mock cab SQUIX) + failure injection
# ---------------------------------------------------------------------------


class StubPrinter:
    """A TCP listener that speaks just enough cab SQUIX JScript to drive the
    vendored ``cab_squix.CabPrinter`` happy path.

    The driver opens a fresh socket per operation, so the stub serves
    connections in a loop and routes by the first bytes it reads:

      * ``ESC s`` (status query) -> replies with a 9-char ``XYNNNNNNZ`` status.
        Before any job it reports online/no-error/idle (passes preflight). After
        a job arrives, the FIRST status poll reports the interpreter active and
        every poll after reports idle — so the driver's completion poll observes
        active-then-idle and returns cleanly.
      * anything else -> a JScript job; the bytes are recorded in ``received``.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.25)
        self.host, self.port = self._sock.getsockname()
        self.received = bytearray()
        self.job_connections = 0  # count of connections carrying a JScript job
        self._job_seen = False
        self._active_polls_left = 0
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _status_reply(self) -> bytes:
        # Format XYNNNNNNZ: X=online, Y=error code, NNNNNN=pending, Z=active.
        if self._job_seen and self._active_polls_left > 0:
            self._active_polls_left -= 1
            return b"Y-000001Y"  # online, no error, 1 pending, job active
        return b"Y-000000N"  # online, no error, none pending, idle

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _addr = self._sock.accept()
            except (OSError, socket.timeout):
                continue
            with conn:
                conn.settimeout(0.5)
                try:
                    first = conn.recv(4096)
                except OSError:
                    continue
                if not first:
                    continue
                if first.startswith(_ESC + b"s"):
                    try:
                        conn.sendall(self._status_reply())
                    except OSError:
                        pass
                    continue
                # JScript job: record it and arm one "active" status poll so the
                # driver's completion loop sees active-then-idle.
                self.received.extend(first)
                self.job_connections += 1
                self._job_seen = True
                self._active_polls_left = 1
                while True:
                    try:
                        chunk = conn.recv(4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    self.received.extend(chunk)

    def close(self) -> None:
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


@pytest.fixture()
def stub_printer(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubPrinter]:
    """A running stub printer with the cached settings pointed at it.

    Patches the lru_cached ``settings()`` instance in place so the service's
    ``_write_to_printer`` connects to the stub rather than a real device.
    """
    printer = StubPrinter()
    cfg = settings()
    monkeypatch.setattr(cfg, "PRINT_HOST", printer.host, raising=False)
    monkeypatch.setattr(cfg, "PRINT_PORT", printer.port, raising=False)
    try:
        yield printer
    finally:
        printer.close()


@pytest.fixture()
def dead_printer(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Point settings at a closed port so every connect is refused.

    Failure-injection fixture: binds then immediately closes a socket so the
    port is almost certainly unbound, guaranteeing a connection refusal.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _host, port = sock.getsockname()
    sock.close()  # port now free -> connect refused
    cfg = settings()
    monkeypatch.setattr(cfg, "PRINT_HOST", "127.0.0.1", raising=False)
    monkeypatch.setattr(cfg, "PRINT_PORT", port, raising=False)
    yield port


def _wait_received(printer: StubPrinter, timeout: float = 2.0) -> bytes:
    """Block until the stub has received bytes (the daemon thread races the
    test assertion)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if printer.received:
            return bytes(printer.received)
        time.sleep(0.01)
    return bytes(printer.received)


# ---------------------------------------------------------------------------
# fixtures: workspaces + queued jobs
# ---------------------------------------------------------------------------


def _make_workspace(db: Session) -> uuid.UUID:
    user = User(
        email=f"print-{uuid.uuid4().hex[:8]}@x.com",
        name="print tester",
        password_hash="test",
    )
    db.add(user)
    db.flush()
    workspace = Workspace(
        name=f"print-ws-{uuid.uuid4().hex[:8]}",
        kind="organization",
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    return workspace.id


def _queued_job(
    db: Session,
    workspace_id: uuid.UUID,
    key: str,
    *,
    kind: str = print_service.KIND_ON_DEMAND,
    payload: str | None = None,
) -> PrintJob:
    job = PrintJob(
        workspace_id=workspace_id,
        idempotency_key=key,
        kind=kind,
        status=print_service.STATUS_QUEUED,
        payload=payload,
    )
    db.add(job)
    db.flush()
    return job


# ---------------------------------------------------------------------------
# send_jscript — queued -> sent -> printed / failed
# ---------------------------------------------------------------------------


def test_send_jscript_transmits_and_marks_printed(db: Session, stub_printer):
    ws = _make_workspace(db)
    job = _queued_job(db, ws, "ok-1")

    result = print_service.send_jscript(db, job, _SAMPLE_JSCRIPT)

    assert result.status == print_service.STATUS_PRINTED
    assert result.error is None
    payload = _wait_received(stub_printer)
    assert b"m m" in payload
    assert b"A 1" in payload


def test_send_jscript_marks_failed_and_raises_when_unreachable(db: Session, dead_printer):
    ws = _make_workspace(db)
    job = _queued_job(db, ws, "fail-1")

    with pytest.raises(PrinterUnreachable):
        print_service.send_jscript(db, job, _SAMPLE_JSCRIPT)

    assert job.status == print_service.STATUS_FAILED
    assert job.error


def test_send_jscript_raises_when_no_print_host_configured(db: Session, monkeypatch):
    # Default (empty PRINT_HOST) is "no sink" -> fail closed, no silent success.
    cfg = settings()
    monkeypatch.setattr(cfg, "PRINT_HOST", "", raising=False)
    ws = _make_workspace(db)
    job = _queued_job(db, ws, "no-sink")

    with pytest.raises(PrinterUnreachable):
        print_service.send_jscript(db, job, _SAMPLE_JSCRIPT)

    assert job.status == print_service.STATUS_FAILED
    assert "PRINT_HOST" in (job.error or "")


def test_send_jscript_on_terminal_job_is_noop(db: Session, stub_printer):
    # A reconciliation re-send of an already-printed job must not print twice.
    ws = _make_workspace(db)
    job = _queued_job(db, ws, "terminal")
    job.status = print_service.STATUS_PRINTED
    db.flush()

    result = print_service.send_jscript(db, job, _SAMPLE_JSCRIPT)

    assert result.status == print_service.STATUS_PRINTED
    assert not stub_printer.received  # nothing transmitted


# ---------------------------------------------------------------------------
# get_or_create_job — idempotency within a workspace
# ---------------------------------------------------------------------------


def test_get_or_create_job_dedupes_on_key_within_workspace(db: Session):
    ws = _make_workspace(db)
    job1, created1 = print_service.get_or_create_job(
        db, workspace_id=ws, idempotency_key="dup", kind=print_service.KIND_ON_DEMAND
    )
    job2, created2 = print_service.get_or_create_job(
        db, workspace_id=ws, idempotency_key="dup", kind=print_service.KIND_ON_DEMAND
    )

    assert created1 is True
    assert created2 is False
    assert job1.id == job2.id
    count = db.execute(
        sa.select(sa.func.count())
        .select_from(PrintJob)
        .where(PrintJob.workspace_id == ws, PrintJob.idempotency_key == "dup")
    ).scalar_one()
    assert count == 1


def test_get_or_create_job_requires_idempotency_key(db: Session):
    ws = _make_workspace(db)
    with pytest.raises(ValueError, match="idempotency_key is required"):
        print_service.get_or_create_job(
            db, workspace_id=ws, idempotency_key="  ", kind=print_service.KIND_ON_DEMAND
        )


def test_get_or_create_job_rejects_unknown_kind(db: Session):
    ws = _make_workspace(db)
    with pytest.raises(ValueError, match="unknown print-job kind"):
        print_service.get_or_create_job(
            db, workspace_id=ws, idempotency_key="k", kind="not_a_kind"
        )


# ---------------------------------------------------------------------------
# send_jscript_batch — chunked delivery
# ---------------------------------------------------------------------------


def test_send_jscript_batch_prints_all(db: Session, stub_printer):
    ws = _make_workspace(db)
    jobs = [_queued_job(db, ws, f"batch-{i}") for i in range(3)]
    pairs = [(job, _SAMPLE_JSCRIPT) for job in jobs]

    printed = print_service.send_jscript_batch(db, pairs)

    assert printed == 3
    assert all(job.status == print_service.STATUS_PRINTED for job in jobs)
    # One chunk -> one connection carrying all three concatenated programs.
    payload = _wait_received(stub_printer)
    assert payload.count(b"m m") == 3


def test_send_jscript_batch_marks_all_failed_on_unreachable(db: Session, dead_printer):
    ws = _make_workspace(db)
    jobs = [_queued_job(db, ws, f"bfail-{i}") for i in range(3)]
    pairs = [(job, _SAMPLE_JSCRIPT) for job in jobs]

    printed = print_service.send_jscript_batch(db, pairs)

    assert printed == 0
    assert all(job.status == print_service.STATUS_FAILED for job in jobs)
    assert all(job.error for job in jobs)


def test_send_jscript_batch_skips_terminal_jobs(db: Session, stub_printer):
    ws = _make_workspace(db)
    already = _queued_job(db, ws, "already")
    already.status = print_service.STATUS_PRINTED
    fresh = _queued_job(db, ws, "fresh")
    db.flush()

    printed = print_service.send_jscript_batch(
        db, [(already, _SAMPLE_JSCRIPT), (fresh, _SAMPLE_JSCRIPT)]
    )

    assert printed == 1
    assert fresh.status == print_service.STATUS_PRINTED


# ---------------------------------------------------------------------------
# dispatch_queued_batch — the deferred-dispatch sweep
# ---------------------------------------------------------------------------


def test_dispatch_queued_batch_ships_only_queued_batch_with_payload(
    db: Session, stub_printer
):
    ws = _make_workspace(db)
    # Shipped: queued, batch_blank, has payload.
    _queued_job(db, ws, "d-1", kind=print_service.KIND_BATCH_BLANK, payload=_SAMPLE_JSCRIPT)
    _queued_job(db, ws, "d-2", kind=print_service.KIND_BATCH_BLANK, payload=_SAMPLE_JSCRIPT)
    # Ignored: no payload.
    _queued_job(db, ws, "d-3", kind=print_service.KIND_BATCH_BLANK, payload=None)
    # Ignored: wrong kind.
    _queued_job(db, ws, "d-4", kind=print_service.KIND_ON_DEMAND, payload=_SAMPLE_JSCRIPT)

    printed = print_service.dispatch_queued_batch(db, workspace_id=ws)

    assert printed == 2
    payload = _wait_received(stub_printer)
    assert payload.count(b"m m") == 2


def test_dispatch_queued_batch_returns_zero_when_nothing_queued(db: Session):
    ws = _make_workspace(db)
    # Only an on_demand queued job with a payload — not a dispatch candidate.
    _queued_job(db, ws, "nd", kind=print_service.KIND_ON_DEMAND, payload=_SAMPLE_JSCRIPT)

    assert print_service.dispatch_queued_batch(db, workspace_id=ws) == 0


# ---------------------------------------------------------------------------
# reconcile_stale_print_jobs — sweep jobs stuck in 'sent'
# ---------------------------------------------------------------------------


def _backdate(db: Session, job: PrintJob, minutes: int) -> None:
    """Force updated_at into the past. An explicit value in .values() bypasses
    the model's onupdate hook."""
    old = print_service.utcnow() - datetime.timedelta(minutes=minutes)
    db.execute(
        sa.update(PrintJob)
        .where(PrintJob.id == job.id)
        .values(status=print_service.STATUS_SENT, updated_at=old)
    )
    db.expire_all()


def test_reconcile_marks_stuck_sent_as_failed(db: Session):
    ws = _make_workspace(db)
    job = _queued_job(db, ws, "stuck")
    _backdate(db, job, minutes=10)  # older than the 5-minute threshold

    affected = print_service.reconcile_stale_print_jobs(db)

    assert affected == 1
    db.expire_all()
    refreshed = db.get(PrintJob, job.id)
    assert refreshed.status == print_service.STATUS_FAILED
    assert "reconciled" in (refreshed.error or "")


def test_reconcile_leaves_recent_sent_untouched(db: Session):
    ws = _make_workspace(db)
    job = _queued_job(db, ws, "recent")
    _backdate(db, job, minutes=1)  # within the 5-minute threshold

    affected = print_service.reconcile_stale_print_jobs(db)

    assert affected == 0
    db.expire_all()
    refreshed = db.get(PrintJob, job.id)
    assert refreshed.status == print_service.STATUS_SENT
