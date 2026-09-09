"""Local datasheet store — backfill, attachment registration, isolation.

Covers the C2/C3 half of ADR-0033:

- a fetched datasheet becomes a real `attachments` row (`file_type='datasheet'`)
  AND a `part_datasheets` bookkeeping row;
- re-running the backfill re-downloads nothing and duplicates nothing;
- a vendor 404 marks that one part and the batch continues;
- a failed row respects its retry cooldown and its attempt ceiling;
- an already-local `/api/parts/assets/...` value is adopted without a
  network call, and one naming another workspace's folder is refused;
- every write is workspace-scoped, and the migration-0079 trigger refuses a
  cross-workspace `part_id` at the database boundary;
- hard-deleting the part purges the attachment through the polymorphic
  cleanup listener — `attachments` had zero rows in prod before this
  feature, so this is the first real exercise of that path;
- a content-addressed file shared by two attachments survives deleting one.
"""
from __future__ import annotations

import os
import socket
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.time import utcnow
from app.domain.attachments.models import Attachment
from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part, PartDatasheet
from app.domain.parts.services import assets, datasheets
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import create_part, signup_user

_PDF_BODY = b"%PDF-1.7\nlocal-datasheet-bytes"
_OTHER_PDF_BODY = b"%PDF-1.7\nanother-datasheet"
_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _upload_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    assets.reset_host_throttle()
    yield
    assets.reset_host_throttle()


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Every hostname resolves to one public address. No real DNS in tests."""

    def _resolve(_host, port, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, port))]

    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolve)


def _pdf(body: bytes = _PDF_BODY, status_code: int = 200) -> assets._AssetResponse:
    return assets._AssetResponse(
        status_code=status_code,
        headers={"content-type": "application/pdf"},
        body=body,
    )


def _fake_http(responses: dict[str, assets._AssetResponse], calls: list[str]):
    """`_http_get` stand-in keyed by the redacted reference (scheme://host/path)."""

    def _get(target: assets._FetchTarget) -> assets._AssetResponse:
        calls.append(target.redacted_ref)
        return responses.get(target.redacted_ref, _pdf(status_code=404, body=b""))

    return _get


def _new_workspace(db, email: str) -> tuple[Workspace, TestClient]:
    client = TestClient(app)
    ws_id = uuid.UUID(signup_user(client, email=email).json()["data"]["workspace_id"])
    ws = db.get(Workspace, ws_id)
    assert ws is not None
    return ws, client


def _part_with_datasheet(db, client, ws: Workspace, *, name: str, url: str) -> Part:
    part_id = uuid.UUID(create_part(client, name=name, mpn=f"MPN-{name}"))
    db.add(
        CustomField(
            workspace_id=ws.id,
            object_type="part",
            object_id=part_id,
            key=datasheets.DATASHEET_FIELD_KEY,
            value=url,
            source="provider",
        )
    )
    db.flush()
    part = db.get(Part, part_id)
    assert part is not None
    return part


def _records(db, ws: Workspace) -> list[PartDatasheet]:
    return list(
        db.execute(
            select(PartDatasheet).where(PartDatasheet.workspace_id == ws.id)
        ).scalars()
    )


def _attachments(db, ws: Workspace) -> list[Attachment]:
    return list(
        db.execute(
            select(Attachment).where(Attachment.workspace_id == ws.id)
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Store + attach
# ---------------------------------------------------------------------------


def test_backfill_stores_datasheet_and_registers_attachment(db, monkeypatch, tmp_path):
    ws, client = _new_workspace(db, "ds-store@example.com")
    url = "https://www.vishay.com/docs/1.pdf"
    part = _part_with_datasheet(db, client, ws, name="R1", url=url)

    calls: list[str] = []
    monkeypatch.setattr(
        assets, "_http_get", _fake_http({"https://www.vishay.com/docs/1.pdf": _pdf()}, calls)
    )

    processed = datasheets.backfill_missing_datasheets(db)

    assert processed == 1
    assert calls == ["https://www.vishay.com/docs/1.pdf"]

    records = _records(db, ws)
    assert len(records) == 1
    record = records[0]
    assert record.part_id == part.id
    assert record.status == datasheets.STATUS_STORED
    assert record.source_url == url
    assert record.failure_code is None
    assert record.size_bytes == len(_PDF_BODY)
    assert record.content_type == "application/pdf"
    assert record.fetched_at is not None
    # The forward slot for the Datalab conversion starts empty.
    assert record.derived_status == "none"
    assert record.derived == {}

    attachments = _attachments(db, ws)
    assert len(attachments) == 1
    attachment = attachments[0]
    assert record.attachment_id == attachment.id
    assert attachment.object_type == "part"
    assert attachment.object_id == part.id
    assert attachment.file_type == datasheets.DATASHEET_FILE_TYPE
    assert attachment.mime_type == "application/pdf"
    assert attachment.size_bytes == len(_PDF_BODY)
    assert attachment.file_name.endswith("-datasheet.pdf")

    # Content-addressed on disk under the unchanged layout (ADR-0005).
    assert attachment.storage_key == record.storage_key
    on_disk = tmp_path / attachment.storage_key
    assert on_disk.is_file()
    assert on_disk.read_bytes() == _PDF_BODY
    assert on_disk.name == f"{record.content_sha256}.pdf"
    assert on_disk.parent.name == str(ws.id)


def test_backfill_writes_audit_row_without_leaking_the_url(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-audit@example.com")
    url = "https://www.vishay.com/docs/2.pdf?token=SUPERSECRET"
    _part_with_datasheet(db, client, ws, name="R2", url=url)
    monkeypatch.setattr(
        assets,
        "_http_get",
        _fake_http({"https://www.vishay.com/docs/2.pdf": _pdf()}, []),
    )

    datasheets.backfill_missing_datasheets(db)

    rows = list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.workspace_id == ws.id)
            .where(AuditLog.action == "part.datasheet.stored")
        ).scalars()
    )
    assert len(rows) == 1
    comment = rows[0].comment or ""
    assert "host=www.vishay.com" in comment
    assert "SUPERSECRET" not in comment
    assert "?" not in comment
    assert "/docs/" not in comment


def test_datasheet_custom_field_is_left_untouched(db, monkeypatch):
    """The upstream URL stays the provenance record — see ADR-0033."""
    ws, client = _new_workspace(db, "ds-cf@example.com")
    url = "https://www.vishay.com/docs/3.pdf"
    part = _part_with_datasheet(db, client, ws, name="R3", url=url)
    monkeypatch.setattr(assets, "_http_get", _fake_http({url: _pdf()}, []))

    datasheets.backfill_missing_datasheets(db)

    field = db.execute(
        select(CustomField)
        .where(CustomField.workspace_id == ws.id)
        .where(CustomField.object_id == part.id)
        .where(CustomField.key == datasheets.DATASHEET_FIELD_KEY)
    ).scalar_one()
    assert field.value == url


# ---------------------------------------------------------------------------
# Idempotency / resumability
# ---------------------------------------------------------------------------


def test_backfill_is_idempotent(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-idem@example.com")
    url = "https://www.ti.com/lit/ds/a.pdf"
    _part_with_datasheet(db, client, ws, name="U1", url=url)

    calls: list[str] = []
    monkeypatch.setattr(assets, "_http_get", _fake_http({url: _pdf()}, calls))

    first = datasheets.backfill_missing_datasheets(db)
    second = datasheets.backfill_missing_datasheets(db)
    third = datasheets.backfill_missing_datasheets(db)

    assert first == 1
    assert (second, third) == (0, 0), "a stored row must not be re-processed"
    assert calls == [url], "the PDF must be downloaded exactly once"
    assert len(_records(db, ws)) == 1
    assert len(_attachments(db, ws)) == 1


def test_direct_refetch_short_circuits_on_stored_row(db, monkeypatch):
    """Even called directly, a stored (part, url) pair never re-downloads."""
    ws, client = _new_workspace(db, "ds-direct@example.com")
    url = "https://www.ti.com/lit/ds/b.pdf"
    part = _part_with_datasheet(db, client, ws, name="U2", url=url)

    calls: list[str] = []
    monkeypatch.setattr(assets, "_http_get", _fake_http({url: _pdf()}, calls))

    first = datasheets.fetch_datasheet_for_part(db, ws=ws, part=part, source_url=url)
    second = datasheets.fetch_datasheet_for_part(db, ws=ws, part=part, source_url=url)

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Degradation — one bad URL must not stall the batch
# ---------------------------------------------------------------------------


def test_one_bad_url_does_not_stall_the_batch(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-bad@example.com")
    good_a = "https://www.vishay.com/docs/ok-a.pdf"
    bad = "https://www.panasonic.com/docs/gone.pdf"
    good_b = "https://www.yageo.com/docs/ok-b.pdf"
    part_a = _part_with_datasheet(db, client, ws, name="A", url=good_a)
    part_bad = _part_with_datasheet(db, client, ws, name="B", url=bad)
    part_b = _part_with_datasheet(db, client, ws, name="C", url=good_b)

    calls: list[str] = []
    monkeypatch.setattr(
        assets,
        "_http_get",
        _fake_http(
            {
                good_a: _pdf(),
                bad: _pdf(status_code=404, body=b""),
                good_b: _pdf(body=_OTHER_PDF_BODY),
            },
            calls,
        ),
    )

    processed = datasheets.backfill_missing_datasheets(db)

    assert processed == 3, "the 404 must not abort the remaining candidates"
    assert set(calls) == {good_a, bad, good_b}

    by_part = {record.part_id: record for record in _records(db, ws)}
    assert by_part[part_a.id].status == datasheets.STATUS_STORED
    assert by_part[part_b.id].status == datasheets.STATUS_STORED
    failed = by_part[part_bad.id]
    assert failed.status == datasheets.STATUS_FAILED
    assert failed.failure_code == "http_404"
    assert failed.attempts == 1
    assert failed.attachment_id is None
    assert failed.last_attempt_at is not None

    # Only the two successes produced attachments.
    assert len(_attachments(db, ws)) == 2

    rows = list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.workspace_id == ws.id)
            .where(AuditLog.action == "part.datasheet.fetch_failed")
        ).scalars()
    )
    assert len(rows) == 1
    assert "reason=http_404" in (rows[0].comment or "")


def test_failed_row_is_not_retried_inside_the_cooldown(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-cooldown@example.com")
    url = "https://www.murata.com/docs/gone.pdf"
    _part_with_datasheet(db, client, ws, name="M1", url=url)

    calls: list[str] = []
    monkeypatch.setattr(
        assets, "_http_get", _fake_http({url: _pdf(status_code=404, body=b"")}, calls)
    )

    assert datasheets.backfill_missing_datasheets(db) == 1
    assert datasheets.backfill_missing_datasheets(db) == 0, "cooldown must hold"
    assert len(calls) == 1


def test_failed_row_is_retried_after_the_cooldown(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-retry@example.com")
    url = "https://www.murata.com/docs/flaky.pdf"
    _part_with_datasheet(db, client, ws, name="M2", url=url)

    calls: list[str] = []
    monkeypatch.setattr(
        assets, "_http_get", _fake_http({url: _pdf(status_code=503, body=b"")}, calls)
    )
    assert datasheets.backfill_missing_datasheets(db) == 1

    # Age the attempt past the cooldown window.
    record = _records(db, ws)[0]
    record.last_attempt_at = utcnow() - datasheets.timedelta(
        seconds=settings().DATASHEET_BACKFILL_RETRY_AFTER_SECONDS + 60
    )
    db.flush()

    monkeypatch.setattr(assets, "_http_get", _fake_http({url: _pdf()}, calls))
    assert datasheets.backfill_missing_datasheets(db) == 1

    db.refresh(record)
    assert record.status == datasheets.STATUS_STORED
    assert record.failure_code is None
    assert record.attachment_id is not None
    assert len(calls) == 2


def test_exhausted_attempts_stop_the_retry_loop(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-exhausted@example.com")
    url = "https://www.molex.com/docs/dead.pdf"
    _part_with_datasheet(db, client, ws, name="X1", url=url)

    calls: list[str] = []
    monkeypatch.setattr(
        assets, "_http_get", _fake_http({url: _pdf(status_code=404, body=b"")}, calls)
    )
    assert datasheets.backfill_missing_datasheets(db) == 1

    record = _records(db, ws)[0]
    record.attempts = settings().DATASHEET_BACKFILL_MAX_ATTEMPTS
    record.last_attempt_at = utcnow() - datasheets.timedelta(days=365)
    db.flush()

    assert datasheets.backfill_missing_datasheets(db) == 0
    assert len(calls) == 1


def test_batch_size_bounds_one_run(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-batch@example.com")
    urls = [f"https://www.tdk.com/docs/{i}.pdf" for i in range(5)]
    for index, url in enumerate(urls):
        _part_with_datasheet(db, client, ws, name=f"T{index}", url=url)

    calls: list[str] = []
    monkeypatch.setattr(
        assets, "_http_get", _fake_http({url: _pdf() for url in urls}, calls)
    )
    monkeypatch.setattr(settings(), "DATASHEET_BACKFILL_BATCH_SIZE", 2, raising=False)

    assert datasheets.backfill_missing_datasheets(db) == 2
    assert len(calls) == 2
    assert datasheets.backfill_missing_datasheets(db) == 2
    assert datasheets.backfill_missing_datasheets(db) == 1
    assert datasheets.backfill_missing_datasheets(db) == 0
    assert len(calls) == 5


def test_backfill_disabled_when_interval_is_zero(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-off@example.com")
    url = "https://www.nexperia.com/docs/a.pdf"
    _part_with_datasheet(db, client, ws, name="N1", url=url)

    calls: list[str] = []
    monkeypatch.setattr(assets, "_http_get", _fake_http({url: _pdf()}, calls))
    monkeypatch.setattr(
        settings(), "DATASHEET_BACKFILL_INTERVAL_SECONDS", 0, raising=False
    )

    assert datasheets.backfill_missing_datasheets(db) == 0
    assert calls == []
    assert _records(db, ws) == []


def test_archived_part_is_not_a_candidate(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-archived@example.com")
    url = "https://www.onsemi.com/docs/a.pdf"
    part = _part_with_datasheet(db, client, ws, name="O1", url=url)
    part.archived_at = utcnow()
    db.flush()

    calls: list[str] = []
    monkeypatch.setattr(assets, "_http_get", _fake_http({url: _pdf()}, calls))

    assert datasheets.backfill_missing_datasheets(db) == 0
    assert calls == []


# ---------------------------------------------------------------------------
# Adoption of already-local assets
# ---------------------------------------------------------------------------


def test_already_local_asset_is_adopted_without_a_network_call(db, monkeypatch, tmp_path):
    ws, client = _new_workspace(db, "ds-adopt@example.com")
    sha = "a" * 64
    asset_dir = tmp_path / "parts" / str(ws.id)
    asset_dir.mkdir(parents=True)
    (asset_dir / f"{sha}.pdf").write_bytes(_PDF_BODY)

    local_url = f"/api/parts/assets/{ws.id}/{sha}.pdf"
    part = _part_with_datasheet(db, client, ws, name="L1", url=local_url)

    def _must_not_fetch(_target):  # pragma: no cover - must never run
        raise AssertionError("adoption must not hit the network")

    monkeypatch.setattr(assets, "_http_get", _must_not_fetch)

    assert datasheets.backfill_missing_datasheets(db) == 1

    record = _records(db, ws)[0]
    assert record.status == datasheets.STATUS_STORED
    assert record.content_sha256 == sha
    assert record.storage_key == os.path.join("parts", str(ws.id), f"{sha}.pdf")
    assert record.size_bytes == len(_PDF_BODY)

    attachment = _attachments(db, ws)[0]
    assert attachment.object_id == part.id
    assert attachment.file_type == datasheets.DATASHEET_FILE_TYPE
    assert attachment.storage_key == record.storage_key


def test_local_asset_path_naming_another_workspace_is_refused(db, monkeypatch, tmp_path):
    """Workspace isolation on the adoption path.

    A `datasheet_url` that points into ANOTHER workspace's asset folder must
    never be adopted into this one — even though the file really exists.
    """
    ws, client = _new_workspace(db, "ds-cross-a@example.com")
    other_ws, _other_client = _new_workspace(db, "ds-cross-b@example.com")

    sha = "b" * 64
    other_dir = tmp_path / "parts" / str(other_ws.id)
    other_dir.mkdir(parents=True)
    (other_dir / f"{sha}.pdf").write_bytes(_PDF_BODY)

    local_url = f"/api/parts/assets/{other_ws.id}/{sha}.pdf"
    _part_with_datasheet(db, client, ws, name="L2", url=local_url)
    monkeypatch.setattr(assets, "_http_get", _fake_http({}, []))

    assert datasheets.backfill_missing_datasheets(db) == 1

    record = _records(db, ws)[0]
    assert record.status == datasheets.STATUS_FAILED
    assert record.failure_code == "local_wrong_workspace"
    assert record.attachment_id is None
    assert _attachments(db, ws) == []
    assert _attachments(db, other_ws) == []


def test_local_asset_missing_on_disk_is_recorded_as_failed(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-missing@example.com")
    local_url = f"/api/parts/assets/{ws.id}/{'c' * 64}.pdf"
    _part_with_datasheet(db, client, ws, name="L3", url=local_url)
    monkeypatch.setattr(assets, "_http_get", _fake_http({}, []))

    assert datasheets.backfill_missing_datasheets(db) == 1
    record = _records(db, ws)[0]
    assert record.status == datasheets.STATUS_FAILED
    assert record.failure_code == "local_file_missing"


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def test_backfill_keeps_workspaces_separate(db, monkeypatch):
    ws_a, client_a = _new_workspace(db, "ds-iso-a@example.com")
    ws_b, client_b = _new_workspace(db, "ds-iso-b@example.com")
    url_a = "https://www.analog.com/docs/a.pdf"
    url_b = "https://www.st.com/docs/b.pdf"
    part_a = _part_with_datasheet(db, client_a, ws_a, name="IA", url=url_a)
    part_b = _part_with_datasheet(db, client_b, ws_b, name="IB", url=url_b)

    monkeypatch.setattr(
        assets,
        "_http_get",
        _fake_http({url_a: _pdf(), url_b: _pdf(body=_OTHER_PDF_BODY)}, []),
    )

    assert datasheets.backfill_missing_datasheets(db) == 2

    records_a = _records(db, ws_a)
    records_b = _records(db, ws_b)
    assert [r.part_id for r in records_a] == [part_a.id]
    assert [r.part_id for r in records_b] == [part_b.id]
    assert all(r.workspace_id == ws_a.id for r in records_a)
    assert all(r.workspace_id == ws_b.id for r in records_b)

    attachments_a = _attachments(db, ws_a)
    attachments_b = _attachments(db, ws_b)
    assert [a.object_id for a in attachments_a] == [part_a.id]
    assert [a.object_id for a in attachments_b] == [part_b.id]
    # Files land in each workspace's own folder.
    assert records_a[0].storage_key.split(os.sep)[1] == str(ws_a.id)
    assert records_b[0].storage_key.split(os.sep)[1] == str(ws_b.id)


def test_cross_workspace_part_id_is_refused_by_the_database(db):
    """Migration 0079's trigger, not just the service, blocks the mix."""
    ws_a, client_a = _new_workspace(db, "ds-trig-a@example.com")
    ws_b, client_b = _new_workspace(db, "ds-trig-b@example.com")
    part_b = _part_with_datasheet(
        db, client_b, ws_b, name="TB", url="https://www.st.com/docs/x.pdf"
    )

    db.add(
        PartDatasheet(
            workspace_id=ws_a.id,
            part_id=part_b.id,  # another workspace's part
            source_url="https://www.st.com/docs/x.pdf",
            source_url_sha256="d" * 64,
            status=datasheets.STATUS_FAILED,
            attempts=0,
        )
    )
    # `WS001` is a custom SQLSTATE with no entry in psycopg's integrity-error
    # table, so it surfaces as DBAPIError — same as the 0060/0064 triggers.
    with pytest.raises(DBAPIError) as excinfo:
        db.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "WS001"
    db.rollback()


def test_cross_workspace_attachment_id_is_refused_by_the_database(db, monkeypatch):
    ws_a, client_a = _new_workspace(db, "ds-att-a@example.com")
    ws_b, client_b = _new_workspace(db, "ds-att-b@example.com")
    url_b = "https://www.st.com/docs/y.pdf"
    _part_with_datasheet(db, client_b, ws_b, name="AB", url=url_b)
    monkeypatch.setattr(assets, "_http_get", _fake_http({url_b: _pdf()}, []))
    assert datasheets.backfill_missing_datasheets(db) == 1
    foreign_attachment = _attachments(db, ws_b)[0]

    part_a = _part_with_datasheet(
        db, client_a, ws_a, name="AA", url="https://www.st.com/docs/z.pdf"
    )
    db.add(
        PartDatasheet(
            workspace_id=ws_a.id,
            part_id=part_a.id,
            attachment_id=foreign_attachment.id,  # another workspace's attachment
            source_url="https://www.st.com/docs/z.pdf",
            source_url_sha256="e" * 64,
            status=datasheets.STATUS_STORED,
            attempts=0,
        )
    )
    # `WS001` is a custom SQLSTATE with no entry in psycopg's integrity-error
    # table, so it surfaces as DBAPIError — same as the 0060/0064 triggers.
    with pytest.raises(DBAPIError) as excinfo:
        db.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "WS001"
    db.rollback()


# ---------------------------------------------------------------------------
# Hard-delete cleanup — first real user of the polymorphic listeners
# ---------------------------------------------------------------------------


def test_hard_deleting_the_part_purges_the_datasheet_attachment(db, monkeypatch):
    ws, client = _new_workspace(db, "ds-delete@example.com")
    url = "https://www.microchip.com/docs/a.pdf"
    part = _part_with_datasheet(db, client, ws, name="D1", url=url)
    monkeypatch.setattr(assets, "_http_get", _fake_http({url: _pdf()}, []))

    assert datasheets.backfill_missing_datasheets(db) == 1
    assert len(_attachments(db, ws)) == 1
    assert len(_records(db, ws)) == 1

    db.delete(part)
    db.flush()

    # attachments has no FK on object_id — the before_delete listener in
    # domain/_polymorphic_cleanup.py is the only thing that removes it.
    assert _attachments(db, ws) == [], "polymorphic cleanup did not fire"
    # part_datasheets.part_id CASCADEs at the DB level.
    assert _records(db, ws) == []
    # The custom field goes too (same listener).
    assert (
        db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_id == part.id)
        ).first()
        is None
    )


def test_deleting_one_attachment_keeps_a_shared_content_addressed_file(
    db, monkeypatch, tmp_path
):
    """Two parts, one family datasheet, one file on disk.

    Content addressing means both attachments point at the same
    `storage_key`. Deleting one must not unlink the bytes the other still
    needs.
    """
    ws, client = _new_workspace(db, "ds-shared@example.com")
    url_a = "https://www.diodes.com/docs/family.pdf"
    url_b = "https://www.diodes.com/docs/family-copy.pdf"
    _part_with_datasheet(db, client, ws, name="S1", url=url_a)
    _part_with_datasheet(db, client, ws, name="S2", url=url_b)
    monkeypatch.setattr(
        assets, "_http_get", _fake_http({url_a: _pdf(), url_b: _pdf()}, [])
    )

    assert datasheets.backfill_missing_datasheets(db) == 2
    attachments = _attachments(db, ws)
    assert len(attachments) == 2
    storage_keys = {a.storage_key for a in attachments}
    assert len(storage_keys) == 1, "identical bodies must share one stored file"
    shared_path = tmp_path / attachments[0].storage_key
    assert shared_path.is_file()

    response = client.delete(f"/api/attachments/{attachments[0].id}")
    assert response.status_code == 200, response.text

    assert shared_path.is_file(), "shared content-addressed file was unlinked"
    remaining = _attachments(db, ws)
    assert len(remaining) == 1

    response = client.delete(f"/api/attachments/{remaining[0].id}")
    assert response.status_code == 200, response.text
    assert not shared_path.exists(), "last reference should unlink the file"


def test_padded_custom_field_value_settles_and_stops_being_a_candidate(db, monkeypatch):
    """A `datasheet_url` with surrounding whitespace must not loop forever.

    The candidate query excludes settled rows by comparing
    `part_datasheets.source_url` to `custom_fields.value`. If the backfill
    stored a stripped copy, the comparison would never match, the same part
    would be re-selected on every run, and with a bounded batch it would
    starve every other candidate. The record key is therefore the verbatim
    field value; stripping happens where the URL is parsed.
    """
    ws, client = _new_workspace(db, "ds-padded@example.com")
    padded = "  https://www.tdk.com/docs/padded.pdf\n"
    other = "https://www.te.com/docs/other.pdf"
    _part_with_datasheet(db, client, ws, name="W1", url=padded)
    _part_with_datasheet(db, client, ws, name="W2", url=other)

    calls: list[str] = []
    monkeypatch.setattr(
        assets,
        "_http_get",
        _fake_http(
            {
                "https://www.tdk.com/docs/padded.pdf": _pdf(),
                other: _pdf(body=_OTHER_PDF_BODY),
            },
            calls,
        ),
    )

    assert datasheets.backfill_missing_datasheets(db) == 2
    assert len(calls) == 2

    records = {r.source_url: r for r in _records(db, ws)}
    assert padded in records, "the record key must be the verbatim field value"
    assert records[padded].status == datasheets.STATUS_STORED

    # The whole point: a second run finds nothing left to do.
    assert datasheets.backfill_missing_datasheets(db) == 0
    assert len(calls) == 2


def test_padded_local_asset_value_is_adopted(db, monkeypatch, tmp_path):
    ws, client = _new_workspace(db, "ds-padded-local@example.com")
    sha = "f" * 64
    asset_dir = tmp_path / "parts" / str(ws.id)
    asset_dir.mkdir(parents=True)
    (asset_dir / f"{sha}.pdf").write_bytes(_PDF_BODY)

    padded_local = f"  /api/parts/assets/{ws.id}/{sha}.pdf  "
    _part_with_datasheet(db, client, ws, name="W3", url=padded_local)

    def _must_not_fetch(_target):  # pragma: no cover - must never run
        raise AssertionError("a padded local path must still be recognised as local")

    monkeypatch.setattr(assets, "_http_get", _must_not_fetch)

    assert datasheets.backfill_missing_datasheets(db) == 1
    record = _records(db, ws)[0]
    assert record.status == datasheets.STATUS_STORED
    assert record.content_sha256 == sha
    assert datasheets.backfill_missing_datasheets(db) == 0
