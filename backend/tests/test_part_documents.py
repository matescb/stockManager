"""Part documents beyond the datasheet (SM-016, ADR-0036).

- DigiKey `MediaLinks` map to document links, filtered to documents;
- a refresh queues them as PENDING rows (DB only, no fetch);
- the cron backfill downloads a queued document as a PDF attachment and
  refuses a non-PDF;
- `request_part_document` validates, dedups, caps, audits host + kind
  only, refuses a read-only token, and never sees another workspace;
- `list_part_documents` works with a read-only token, and its
  `download_url` serves the bytes to a Bearer token;
- hard-deleting the part still purges the document attachments.

No real network: DNS and `_http_get` are stubbed exactly as in
`test_datasheet_backfill.py`.
"""
from __future__ import annotations

import socket
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.domain.attachments.models import Attachment
from app.domain.audit.models import AuditLog
from app.domain.parts.models import Part, PartDatasheet
from app.domain.parts.providers.digikey import _record_from_product
from app.domain.parts.services import assets, datasheets, documents
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_mcp import _mint, call, call_error, mcp_session

_PDF_BODY = b"%PDF-1.7\napplication-note-bytes"
_PUBLIC_IP = "93.184.216.34"
_APP_NOTE = "https://www.st.com/resource/en/application_note/an2867.pdf"

# A DigiKey Product Information v4 `ProductDetails.Product`, trimmed to the
# fields the mapper reads. `MediaLinks` uses the v4 field names (`MediaType`,
# `Title`, `SmallPhoto`, `Thumbnail`, `Url`); DigiKey serves many of them
# scheme-relative or over plain http.
DIGIKEY_PRODUCT_WITH_MEDIA = {
    "ManufacturerProductNumber": "STM32G031K8T6",
    "Manufacturer": {"Name": "STMicroelectronics"},
    "Description": {"ProductDescription": "IC MCU 32BIT 64KB FLASH 32LQFP"},
    "ProductUrl": "https://www.digikey.cz/en/products/detail/st/STM32G031K8T6/10326709",
    "DatasheetUrl": "https://www.st.com/resource/en/datasheet/stm32g031k8.pdf",
    "PhotoUrl": "https://mm.digikey.com/Volume0/stm32.jpg",
    "Parameters": [],
    "MediaLinks": [
        {
            "MediaType": "Datasheets",
            "Title": "STM32G031x4/x6/x8 Datasheet",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.st.com/resource/en/datasheet/stm32g031k8.pdf",
        },
        {
            "MediaType": "Design Resources",
            "Title": "AN2867 Oscillator design guide for STM8 and STM32",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "//www.st.com/resource/en/application_note/an2867.pdf",
        },
        {
            "MediaType": "Design Resources",
            "Title": "Getting started with STM32G0 hardware development",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.st.com/resource/en/application_note/an5096.pdf",
        },
        {
            "MediaType": "Other Related Documents",
            "Title": "STM32G0x1 Reference manual",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "http://www.st.com/resource/en/reference_manual/rm0444.pdf",
        },
        {
            "MediaType": "Other Related Documents",
            "Title": "STM32 programming manual (PM0223)",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.st.com/resource/en/programming_manual/pm0223.pdf",
        },
        {
            "MediaType": "Other Related Documents",
            "Title": "PCB layout guidelines for STM32",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.st.com/resource/en/pcb_layout.pdf",
        },
        {
            "MediaType": "Other Related Documents",
            "Title": "STM32 errata sheet technical note",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.st.com/resource/en/errata.pdf",
        },
        {
            "MediaType": "PCN Design/Specification",
            "Title": "Mult Dev Datasheet Update 12/Mar/2024",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://media.digikey.com/pdf/PCNs/ST/pcn123.pdf",
        },
        {
            "MediaType": "Other Related Documents",
            "Title": "STM32 product line brochure",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.st.com/brochure.pdf",
        },
        {
            "MediaType": "Product Photos",
            "Title": "32-LQFP",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://mm.digikey.com/Volume0/stm32.jpg",
        },
        {
            "MediaType": "Video File",
            "Title": "STM32G0 overview",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.youtube.com/watch?v=abc",
        },
        {
            "MediaType": "EDA Models",
            "Title": "STM32G031K8T6 by Ultra Librarian",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://app.ultralibrarian.com/details/x",
        },
        {
            "MediaType": "HTML Datasheet",
            "Title": "STM32G031x4/x6/x8 Datasheet",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.st.com/html/stm32g031.html",
        },
        {
            "MediaType": "Product Training Modules",
            "Title": "STM32G0 training",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.digikey.com/ptm/stm32g0",
        },
        {
            "MediaType": "Featured Product",
            "Title": "STM32G0 Series",
            "SmallPhoto": "",
            "Thumbnail": "",
            "Url": "https://www.digikey.com/featured/stm32g0",
        },
    ],
}


@pytest.fixture(autouse=True)
def _upload_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    assets.reset_host_throttle()
    yield
    assets.reset_host_throttle()


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    def _resolve(_host, port, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, port))]

    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolve)


def _response(body: bytes = _PDF_BODY, content_type: str = "application/pdf"):
    return assets._AssetResponse(
        status_code=200, headers={"content-type": content_type}, body=body
    )


def _fake_http(monkeypatch, responses: dict[str, assets._AssetResponse]) -> list[str]:
    calls: list[str] = []

    def _get(target: assets._FetchTarget) -> assets._AssetResponse:
        calls.append(target.redacted_ref)
        return responses.get(
            target.redacted_ref,
            assets._AssetResponse(status_code=404, headers={}, body=b""),
        )

    monkeypatch.setattr(assets, "_http_get", _get)
    return calls


def _workspace_of(db, part_id: str) -> Workspace:
    part = db.get(Part, uuid.UUID(part_id))
    assert part is not None
    ws = db.get(Workspace, part.workspace_id)
    assert ws is not None
    return ws


def _rows(db, part_id) -> list[PartDatasheet]:
    db.expire_all()
    return list(
        db.execute(
            select(PartDatasheet)
            .where(PartDatasheet.part_id == uuid.UUID(str(part_id)))
            .order_by(PartDatasheet.created_at, PartDatasheet.id)
        ).scalars()
    )


def _queue(db, part_id: str, url: str, kind: str = "application_note", title=None):
    part = db.get(Part, uuid.UUID(part_id))
    ws = db.get(Workspace, part.workspace_id)
    outcome = documents.request_document(
        db, ws=ws, part=part, url=url, kind=kind, title=title, user_id=None
    )
    db.flush()
    return outcome


# ---------------------------------------------------------------------------
# DigiKey MediaLinks mapping
# ---------------------------------------------------------------------------


def test_media_links_keep_documents_and_drop_everything_else():
    record = _record_from_product(DIGIKEY_PRODUCT_WITH_MEDIA)
    links = {link["url"]: link["kind"] for link in record["media_links"]}

    assert links == {
        # scheme-relative and plain-http links are upgraded to https
        "https://www.st.com/resource/en/application_note/an2867.pdf": "application_note",
        "https://www.st.com/resource/en/application_note/an5096.pdf": "design_note",
        "https://www.st.com/resource/en/reference_manual/rm0444.pdf": "programming_manual",
        "https://www.st.com/resource/en/programming_manual/pm0223.pdf": "programming_manual",
        "https://www.st.com/resource/en/pcb_layout.pdf": "design_note",
        "https://www.st.com/resource/en/errata.pdf": "technical_note",
        "https://media.digikey.com/pdf/PCNs/ST/pcn123.pdf": "pcn",
    }
    # The datasheet stays the `datasheet_url` field's business.
    assert record["datasheet_url"] == DIGIKEY_PRODUCT_WITH_MEDIA["DatasheetUrl"]
    titles = {link["title"] for link in record["media_links"]}
    assert "AN2867 Oscillator design guide for STM8 and STM32" in titles


def test_a_product_without_media_links_maps_to_an_empty_list():
    record = _record_from_product({"ManufacturerProductNumber": "X1"})
    assert record["media_links"] == []


def test_media_links_are_capped_per_part():
    product = {
        "ManufacturerProductNumber": "X2",
        "MediaLinks": [
            {"MediaType": "PCN Packaging", "Title": f"PCN {i}", "Url": f"https://a.com/{i}.pdf"}
            for i in range(60)
        ],
    }
    assert len(_record_from_product(product)["media_links"]) == 25


# ---------------------------------------------------------------------------
# Refresh queues media links (no fetch)
# ---------------------------------------------------------------------------


class _StubDigiKey:
    name = "digikey"

    def __init__(self, record: dict) -> None:
        self.record = record

    def lookup_mpn(self, mpn: str) -> dict:
        return {"found": True, "result": self.record, "match": "exact", "message": None}


def test_refresh_queues_media_links_as_pending_rows_without_fetching(
    authed_client, db, monkeypatch
):
    from app.core.secrets import encrypt
    from app.domain.parts.services import provider_cache

    provider_cache._breakers.clear()
    provider_cache._cache._store.clear()
    record = _record_from_product(DIGIKEY_PRODUCT_WITH_MEDIA)
    record["datasheet_url"] = None  # keep the request path off the asset fetch
    record["image_url"] = None
    monkeypatch.setattr(
        "app.domain.parts.services.provider_refresh.make_provider",
        lambda name, key, secret=None: _StubDigiKey(record),
    )
    calls = _fake_http(monkeypatch, {})

    part_id = create_part(authed_client, "MCU", mpn="STM32G031K8T6")
    ws = _workspace_of(db, part_id)
    ws.parts_provider = "digikey"
    ws.parts_provider_api_key = encrypt("id")
    ws.parts_provider_api_secret = encrypt("secret")
    db.flush()

    for _ in range(2):  # the second refresh must add nothing
        r = authed_client.post(f"/api/parts/{part_id}/refresh-from-provider")
        assert r.status_code == 200, r.text

    rows = _rows(db, part_id)
    assert len(rows) == len(record["media_links"]) == 7
    assert {r.origin for r in rows} == {datasheets.ORIGIN_MEDIA_LINK}
    assert {r.status for r in rows} == {datasheets.STATUS_PENDING}
    assert calls == [], "a refresh must never fetch a document"
    provider_cache._breakers.clear()
    provider_cache._cache._store.clear()


# ---------------------------------------------------------------------------
# Cron backfill
# ---------------------------------------------------------------------------


def test_backfill_stores_a_queued_document_as_a_pdf_attachment(authed_client, db, monkeypatch):
    part_id = create_part(authed_client, "Doc part", mpn="DOC-1")
    _queue(db, part_id, _APP_NOTE, title="AN2867 Oscillator design")
    calls = _fake_http(monkeypatch, {_APP_NOTE: _response()})

    assert datasheets.backfill_missing_datasheets(db) == 1

    assert calls == [_APP_NOTE]
    (row,) = _rows(db, part_id)
    assert row.status == datasheets.STATUS_STORED
    assert row.kind == "application_note"
    attachment = db.get(Attachment, row.attachment_id)
    assert attachment.file_type == "application_note"
    assert attachment.mime_type == "application/pdf"
    assert attachment.file_name == "DOC-1-AN2867_Oscillator_design.pdf"
    assert attachment.storage_key == f"parts/{row.workspace_id}/{row.content_sha256}.pdf"

    # Idempotent: a second run fetches nothing.
    assert datasheets.backfill_missing_datasheets(db) == 0
    assert calls == [_APP_NOTE]


def test_backfill_refuses_a_document_that_is_not_a_pdf(authed_client, db, monkeypatch):
    part_id = create_part(authed_client, "Doc part 2", mpn="DOC-2")
    url = "https://www.ti.com/lit/an/slva000/landing"
    _queue(db, part_id, url)
    _fake_http(monkeypatch, {url: _response(b"<html>hi</html>", "text/html")})

    assert datasheets.backfill_missing_datasheets(db) == 1

    (row,) = _rows(db, part_id)
    assert row.status == datasheets.STATUS_FAILED
    assert row.failure_code == "unexpected_type"
    assert row.attachment_id is None
    assert db.execute(select(Attachment)).scalars().all() == []


def test_backfill_document_batch_size_zero_turns_the_half_off(authed_client, db, monkeypatch):
    part_id = create_part(authed_client, "Doc part 3", mpn="DOC-3")
    _queue(db, part_id, _APP_NOTE)
    calls = _fake_http(monkeypatch, {_APP_NOTE: _response()})
    monkeypatch.setattr(settings(), "DOCUMENT_BACKFILL_BATCH_SIZE", 0)

    assert datasheets.backfill_missing_datasheets(db) == 0
    assert calls == []


def test_backfill_stops_starting_fetches_once_the_run_budget_is_spent(
    authed_client, db, monkeypatch
):
    part_id = create_part(authed_client, "Doc part 4", mpn="DOC-4")
    _queue(db, part_id, _APP_NOTE)
    calls = _fake_http(monkeypatch, {_APP_NOTE: _response()})
    monkeypatch.setattr(datasheets, "RUN_BUDGET_SECONDS", -1.0)

    assert datasheets.backfill_missing_datasheets(db) == 0
    assert calls == []
    (row,) = _rows(db, part_id)
    assert row.status == datasheets.STATUS_PENDING


def test_hard_deleting_the_part_purges_document_attachments(authed_client, db, monkeypatch):
    part_id = create_part(authed_client, "Doc part 5", mpn="DOC-5")
    _queue(db, part_id, _APP_NOTE, kind="pcn")
    _fake_http(monkeypatch, {_APP_NOTE: _response()})
    assert datasheets.backfill_missing_datasheets(db) == 1
    assert len(db.execute(select(Attachment)).scalars().all()) == 1

    db.delete(db.get(Part, uuid.UUID(part_id)))
    db.flush()

    assert db.execute(select(Attachment)).scalars().all() == []
    assert _rows(db, part_id) == []


# ---------------------------------------------------------------------------
# request_part_document — validation (service level)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://www.st.com/a.pdf",
        "ftp://www.st.com/a.pdf",
        "https://93.184.216.34/a.pdf",
        "https://[::1]/a.pdf",
        "https://2130706433/a.pdf",
        "https://0x7f.1/a.pdf",
        "https://localhost/a.pdf",
        "https://printer.local/a.pdf",
        "https://db.internal/a.pdf",
        "https://intranet/a.pdf",
        "https://user:pass@www.st.com/a.pdf",
        "https://www.st.com:8443/a.pdf",
        "https://www.st.com/a b.pdf",
        "https://www.st.com/" + "a" * 2100 + ".pdf",
        "",
    ],
)
def test_request_validation_refuses_unsafe_urls(url):
    with pytest.raises(documents.DocumentUrlRefused):
        documents.validate_document_url(url)


def test_request_validation_normalises_host_and_drops_the_fragment():
    assert (
        documents.validate_document_url("HTTPS://WWW.ST.com:443/a.pdf?x=1#page=3")
        == "https://www.st.com/a.pdf?x=1"
    )


def test_request_refuses_an_unknown_kind(authed_client, db):
    from fastapi import HTTPException

    part_id = create_part(authed_client, "K part", mpn="K-1")
    with pytest.raises(HTTPException) as exc:
        _queue(db, part_id, _APP_NOTE, kind="brochure")
    assert exc.value.detail["code"] == "document.invalid_kind"


def test_request_dedups_and_never_relabels_the_datasheet(authed_client, db):
    part_id = create_part(authed_client, "Dedup", mpn="DD-1")
    first = _queue(db, part_id, _APP_NOTE)
    again = _queue(db, part_id, _APP_NOTE + "#frag", kind="pcn")
    assert first.created is True
    assert again.created is False
    assert again.row.id == first.row.id
    assert again.row.kind == "application_note"
    assert len(_rows(db, part_id)) == 1


def test_request_cap_counts_waiting_agent_requests(authed_client, db, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings(), "DOCUMENT_REQUEST_PENDING_CAP", 2)
    part_id = create_part(authed_client, "Cap", mpn="CAP-1")
    _queue(db, part_id, "https://www.st.com/1.pdf")
    _queue(db, part_id, "https://www.st.com/2.pdf")
    with pytest.raises(HTTPException) as exc:
        _queue(db, part_id, "https://www.st.com/3.pdf")
    assert exc.value.detail["code"] == "document.queue_full"
    # A duplicate is still answered — it adds nothing.
    assert _queue(db, part_id, "https://www.st.com/1.pdf").created is False


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


async def test_request_part_document_queues_and_audits_host_and_kind_only(authed_client, db):
    part_id = create_part(authed_client, "MCP doc", mpn="MCP-DOC-1")
    token = _mint(authed_client)["token"]
    url = "https://www.st.com/resource/an.pdf?token=SUPERSECRET"
    async with mcp_session(token) as s:
        out = await call(
            s, "request_part_document", part_id_or_mpn="MCP-DOC-1", url=url,
            kind="application_note", title="AN1 Something",
        )
        assert out["queued"] is True
        assert out["request"]["status"] == "queued"
        again = await call(
            s, "request_part_document", part_id_or_mpn=part_id, url=url, kind="pcn"
        )
        assert again["queued"] is False
        assert again["request"]["kind"] == "application_note"
        refused = await call_error(
            s, "request_part_document", part_id_or_mpn=part_id,
            url="https://10.0.0.1/a.pdf", kind="other",
        )
        assert "document.invalid_url" in refused

    (row,) = _rows(db, part_id)
    assert row.origin == datasheets.ORIGIN_AGENT
    assert row.title == "AN1 Something"
    audits = list(
        db.execute(
            select(AuditLog).where(AuditLog.action == "part.document.requested")
        ).scalars()
    )
    assert len(audits) == 1
    comment = audits[0].comment or ""
    assert "host=www.st.com" in comment and "kind=application_note" in comment
    assert "SUPERSECRET" not in comment and "/resource/" not in comment


async def test_request_part_document_refuses_a_read_only_token(authed_client, db):
    part_id = create_part(authed_client, "RO doc", mpn="RO-DOC-1")
    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        err = await call_error(
            s, "request_part_document", part_id_or_mpn=part_id, url=_APP_NOTE,
            kind="application_note",
        )
    assert "auth.token_read_only" in err
    assert _rows(db, part_id) == []


async def test_document_tools_never_see_another_workspace(authed_client, db):
    part_id = create_part(authed_client, "Mine", mpn="ISO-DOC-1")
    _queue(db, part_id, _APP_NOTE)

    other = TestClient(app)
    signup_user(other, email=f"other-{uuid.uuid4().hex[:8]}@x.com")
    token = _mint(other)["token"]
    async with mcp_session(token) as s:
        for tool, kwargs in (
            ("list_part_documents", {}),
            ("request_part_document", {"url": "https://www.st.com/x.pdf", "kind": "other"}),
        ):
            for ref in (part_id, "ISO-DOC-1"):
                err = await call_error(s, tool, part_id_or_mpn=ref, **kwargs)
                assert "part.not_found" in err, err
    assert len(_rows(db, part_id)) == 1


async def test_list_part_documents_with_a_read_only_token_and_bearer_download(
    authed_client, db, monkeypatch
):
    part_id = create_part(authed_client, "List doc", mpn="LIST-DOC-1")
    # A user-uploaded PDF (kind `other`), a downloaded app note, a queued PCN.
    upload = authed_client.post(
        "/api/attachments",
        data={"object_type": "part", "object_id": part_id},
        files={"file": ("notes.pdf", b"%PDF-1.4\nuser-notes", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    _queue(db, part_id, _APP_NOTE, title="AN2867")
    _fake_http(monkeypatch, {_APP_NOTE: _response()})
    assert datasheets.backfill_missing_datasheets(db) == 1
    _queue(db, part_id, "https://media.digikey.com/pdf/PCNs/x.pdf", kind="pcn")

    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        out = await call(s, "list_part_documents", part_id_or_mpn="LIST-DOC-1")
        only_notes = await call(
            s, "list_part_documents", part_id_or_mpn=part_id, kinds=["application_note"]
        )

    docs = {d["kind"]: d for d in out["documents"]}
    assert set(docs) == {"other", "application_note"}
    note = docs["application_note"]
    assert note["source_url"] == _APP_NOTE
    assert note["title"] == "AN2867"
    assert note["mime"] == "application/pdf"
    assert note["size_bytes"] == len(_PDF_BODY)
    assert len(note["sha256"]) == 64
    assert docs["other"]["source_url"] is None
    import hashlib

    assert docs["other"]["sha256"] == hashlib.sha256(b"%PDF-1.4\nuser-notes").hexdigest()
    assert out["pending"] == [
        {
            "source_url": "https://media.digikey.com/pdf/PCNs/x.pdf",
            "kind": "pcn",
            "title": None,
            "status": "queued",
        }
    ]
    assert [d["kind"] for d in only_notes["documents"]] == ["application_note"]
    assert only_notes["pending"] == []

    # The download URL is absolute and serves the bytes to the SAME
    # read-only token as a Bearer header — no cookie involved.
    base = settings().APP_BASE_URL.rstrip("/")
    assert note["download_url"].startswith(f"{base}/api/attachments/")
    bearer = TestClient(app)
    r = bearer.get(
        note["download_url"][len(base):], headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    assert r.content == _PDF_BODY
    assert r.headers["content-disposition"].startswith("attachment")

    # ...and a token from another workspace gets nothing.
    other = TestClient(app)
    signup_user(other, email=f"dl-{uuid.uuid4().hex[:8]}@x.com")
    foreign = _mint(other, read_only=True)["token"]
    r = TestClient(app).get(
        note["download_url"][len(base):], headers={"Authorization": f"Bearer {foreign}"}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Security-review follow-ups: attempt committed before the fetch; fairness.
# ---------------------------------------------------------------------------


def test_the_attempt_is_committed_before_the_fetch_starts(authed_client, db, monkeypatch):
    """A run killed mid-download must leave the row in cooldown.

    The fetch below raises BaseException (what a SIGTERM-ish kill looks like
    to the loop): nothing after it runs, so whatever the row says afterwards
    is what was committed before the network call.
    """
    part_id = create_part(authed_client, "Hang", mpn="HANG-1")
    _queue(db, part_id, _APP_NOTE)

    class _Killed(BaseException):
        pass

    def _hang(*_a, **_k):
        raise _Killed()

    monkeypatch.setattr(assets, "fetch_asset", _hang)
    with pytest.raises(_Killed):
        datasheets.backfill_missing_datasheets(db)
    db.rollback()  # the kill: drop everything not yet committed

    (row,) = _rows(db, part_id)
    assert row.attempts == 1
    assert row.last_attempt_at is not None
    # ...so the next run leaves it alone for the cooldown.
    assert documents._document_candidates(db, limit=10) == []


def test_document_candidates_are_fair_across_workspaces(authed_client, db, monkeypatch):
    noisy = create_part(authed_client, "Noisy", mpn="NOISY-1")
    for i in range(8):
        _queue(db, noisy, f"https://www.st.com/noisy/{i}.pdf")

    other = TestClient(app)
    signup_user(other, email=f"quiet-{uuid.uuid4().hex[:8]}@x.com")
    quiet = create_part(other, "Quiet", mpn="QUIET-1")
    _queue(db, quiet, "https://www.ti.com/quiet.pdf")

    picked = documents._document_candidates(db, limit=10)
    by_ws: dict = {}
    for ws_id, _part, _row in picked:
        by_ws[ws_id] = by_ws.get(ws_id, 0) + 1
    noisy_ws = db.get(Part, uuid.UUID(noisy)).workspace_id
    quiet_ws = db.get(Part, uuid.UUID(quiet)).workspace_id
    assert by_ws == {noisy_ws: documents.DOCUMENT_BACKFILL_PER_WORKSPACE, quiet_ws: 1}
    # The quiet workspace's only row is served in the first round, even
    # though the noisy one queued earlier.
    assert quiet_ws in {ws for ws, _p, _r in picked[:2]}


# ---------------------------------------------------------------------------
# edaflow SM-020 — the same documents on the REST part record
# ---------------------------------------------------------------------------


async def test_rest_part_detail_carries_the_documents_mcp_lists(
    authed_client, db, monkeypatch
):
    part_id = create_part(authed_client, "REST doc", mpn="REST-DOC-1")
    upload = authed_client.post(
        "/api/attachments",
        data={"object_type": "part", "object_id": part_id},
        files={"file": ("ds.pdf", b"%PDF-1.4\nowner-datasheet", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    _queue(db, part_id, "https://media.digikey.com/pdf/PCNs/y.pdf", kind="pcn")
    bare = create_part(authed_client, "No docs", mpn="REST-DOC-2")

    import httpx

    def refuse(*a, **k):
        raise AssertionError("reading documents must not fetch")
    monkeypatch.setattr(httpx.Client, "send", refuse)

    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        mcp = await call(s, "list_part_documents", part_id_or_mpn="REST-DOC-1")

    rest = authed_client.get(f"/api/parts/{part_id}").json()["data"]
    assert rest["documents"] == mcp["documents"]
    assert rest["documents_pending"] == mcp["pending"]
    [doc] = rest["documents"]
    assert doc["kind"] == "other" and len(doc["sha256"]) == 64
    assert doc["download_url"].endswith(f"/api/attachments/{doc['id']}/download")

    empty = authed_client.get(f"/api/parts/{bare}").json()["data"]
    assert empty["documents"] == [] and empty["documents_pending"] == []
    # Detail only: list rows stay one query per extra.
    row = authed_client.get("/api/parts", params={"mpn": "REST-DOC-1"}).json()["data"][0]
    assert "documents" not in row
