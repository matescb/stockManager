"""Workspace-isolation pins for the ``print_jobs`` ledger.

skladVA is single-tenant; this project is multi-tenant, so the print-job ledger
MUST be scoped by ``workspace_id`` end to end. These tests pin that contract:

  * idempotency dedupe is *per workspace* — the same key in two workspaces
    yields two distinct jobs and workspace B never dedupes against (or observes)
    workspace A's job,
  * the composite UNIQUE index enforces per-workspace uniqueness at the DB level
    while allowing cross-workspace key reuse,
  * the maintenance sweeps (``dispatch_queued_batch`` / ``reconcile_stale_print
    _jobs``) only touch rows of the workspace they are scoped to.

Mirrors the style of ``tests/test_workspace_isolation.py`` (create resource in
workspace A, prove workspace B is unaffected).
"""

from __future__ import annotations

import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.printing import print_service
from app.domain.printing.models import PrintJob
from tests.test_print_service import _make_workspace, _queued_job


def test_same_key_yields_distinct_jobs_across_workspaces(db: Session):
    ws_a = _make_workspace(db)
    ws_b = _make_workspace(db)

    job_a, created_a = print_service.get_or_create_job(
        db, workspace_id=ws_a, idempotency_key="shared", kind=print_service.KIND_ON_DEMAND
    )
    # Workspace B uses the SAME key: it must create its OWN job, not dedupe
    # against — or leak — workspace A's.
    job_b, created_b = print_service.get_or_create_job(
        db, workspace_id=ws_b, idempotency_key="shared", kind=print_service.KIND_ON_DEMAND
    )

    assert created_a is True
    assert created_b is True
    assert job_a.id != job_b.id
    assert job_a.workspace_id == ws_a
    assert job_b.workspace_id == ws_b


def test_dedupe_is_scoped_and_does_not_cross_read(db: Session):
    ws_a = _make_workspace(db)
    ws_b = _make_workspace(db)
    _queued_job(db, ws_a, "probe")

    # A repeat within A dedupes; the same key in B does not see A's row.
    _job_a2, created_a2 = print_service.get_or_create_job(
        db, workspace_id=ws_a, idempotency_key="probe", kind=print_service.KIND_ON_DEMAND
    )
    _job_b, created_b = print_service.get_or_create_job(
        db, workspace_id=ws_b, idempotency_key="probe", kind=print_service.KIND_ON_DEMAND
    )

    assert created_a2 is False  # deduped within A
    assert created_b is True    # B created a fresh, independent job


def test_composite_unique_index_enforces_per_workspace_uniqueness(db: Session):
    ws_a = _make_workspace(db)
    ws_b = _make_workspace(db)

    # Same key across two workspaces is allowed by the (workspace_id, key) index.
    db.add(PrintJob(workspace_id=ws_a, idempotency_key="k", kind=print_service.KIND_ON_DEMAND))
    db.add(PrintJob(workspace_id=ws_b, idempotency_key="k", kind=print_service.KIND_ON_DEMAND))
    db.flush()

    # A second row with the same (workspace_id, key) must violate the index.
    db.add(PrintJob(workspace_id=ws_a, idempotency_key="k", kind=print_service.KIND_ON_DEMAND))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_dispatch_sweep_scoped_to_workspace(db: Session):
    # PRINT_HOST is empty in tests (no sink) -> dispatch fail-closes A's jobs to
    # 'failed', but the workspace filter must leave B's queued rows untouched.
    ws_a = _make_workspace(db)
    ws_b = _make_workspace(db)
    job_a = _queued_job(
        db, ws_a, "swa", kind=print_service.KIND_BATCH_BLANK, payload="m m\r\nJ\r\nA 1\r\n"
    )
    job_b = _queued_job(
        db, ws_b, "swb", kind=print_service.KIND_BATCH_BLANK, payload="m m\r\nJ\r\nA 1\r\n"
    )

    print_service.dispatch_queued_batch(db, workspace_id=ws_a)

    db.expire_all()
    assert db.get(PrintJob, job_a.id).status != print_service.STATUS_QUEUED
    assert db.get(PrintJob, job_b.id).status == print_service.STATUS_QUEUED


def test_reconcile_sweep_scoped_to_workspace(db: Session):
    ws_a = _make_workspace(db)
    ws_b = _make_workspace(db)
    job_a = _queued_job(db, ws_a, "ra")
    job_b = _queued_job(db, ws_b, "rb")
    old = print_service.utcnow() - datetime.timedelta(minutes=10)
    db.execute(
        sa.update(PrintJob)
        .where(PrintJob.id.in_([job_a.id, job_b.id]))
        .values(status=print_service.STATUS_SENT, updated_at=old)
    )
    db.expire_all()

    affected = print_service.reconcile_stale_print_jobs(db, workspace_id=ws_a)

    assert affected == 1
    db.expire_all()
    assert db.get(PrintJob, job_a.id).status == print_service.STATUS_FAILED
    assert db.get(PrintJob, job_b.id).status == print_service.STATUS_SENT
