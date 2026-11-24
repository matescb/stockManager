"""Part documents beyond the datasheet — queue, fetch, list (SM-016, ADR-0036).

The datasheet store (`services/datasheets.py`, ADR-0033) turned a part's
`datasheet_url` into a PDF we own. This module does the same for the rest
of a part's paperwork: application notes, technical notes, PCB design
notes, programming manuals and PCNs. They share the datasheet's record
(`part_datasheets`, with `kind` / `title` / `origin` from migration 0086),
its attachment registration, its failure bookkeeping and its cron job.

Where a document comes from
---------------------------
* **`media_link`** — a provider record's document links (DigiKey
  `MediaLinks`, filtered to documents in `providers/digikey.py`). The
  create and refresh paths call `queue_media_links`, which writes PENDING
  rows and nothing else: no network, so it is safe inside a request.
* **`agent`** — `request_part_document` (the MCP tool of the same name),
  for a PDF an agent found on a manufacturer's site. Validated, deduped,
  capped per workspace, audited — and NOT fetched.

Who fetches
-----------
Only `backfill_documents`, the second half of the hourly
`datasheet-backfill` job, under the same advisory lock, host breaker and
run budget as the datasheet half. It is the only place in this module
that passes `allow_any_host=True`, with the cron-only `document` kind
(`assets._UNRESTRICTED_KINDS`). Every ADR-0033 control applies unchanged:
pinned resolved IP, HTTPS only, no redirects, 10 MB cap, wall-clock
budget, magic bytes, PDF only, User-Agent, per-host throttle. Nothing in a
request handler or an MCP tool reaches an arbitrary host.

Text extraction is deliberately not here: `part_datasheets.derived` stays
the slot for the planned Datalab conversion.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import time
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.attachments.models import Attachment
from app.domain.parts.models import Part, PartDatasheet
from app.domain.parts.services import assets
from app.domain.parts.services import datasheets as ds
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

DOCUMENT_KINDS: tuple[str, ...] = (
    "datasheet",
    "application_note",
    "technical_note",
    "design_note",
    "programming_manual",
    "pcn",
    "other",
)
_DOCUMENT_KIND_SET = frozenset(DOCUMENT_KINDS)

# `assets.fetch_asset` kind for everything that is not a datasheet. Both are
# in `_UNRESTRICTED_KINDS`; the split only shows up in logs.
FETCH_KIND_DOCUMENT = "document"

MAX_URL_LENGTH = 2048
MAX_TITLE_LENGTH = 300
# A DigiKey MCU carries dozens of PCNs. More than this per part is noise the
# hourly sweep would spend days on.
MAX_MEDIA_LINKS_PER_PART = 25
# Documents fetched per WORKSPACE per run. Without it the oldest-first order
# lets one workspace's backlog (200 agent requests, each retried after a day)
# take every slot of every run and starve all the others.
DOCUMENT_BACKFILL_PER_WORKSPACE = 3

PDF_MIME = "application/pdf"

# Hostnames that never name a public manufacturer site. The cron's pinned
# resolution refuses non-global addresses anyway; this keeps obviously
# internal names out of the queue in the first place.
_INTERNAL_HOSTS = frozenset({"localhost", "localhost.localdomain"})
_INTERNAL_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".arpa",
)
# The last label of a public hostname: letters (or an IDNA `xn--` label).
# Rejects every numeric spelling of an address (`2130706433`, `0x7f.1`,
# `127.1`) that `ipaddress` does not parse but a resolver would.
_TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
_CONTROL = re.compile(r"[\x00-\x20\x7f\\]")
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_CONTENT_ADDRESSED = re.compile(r"^parts/[^/]+/([0-9a-f]{64})\.[a-z0-9]+$")


class DocumentUrlRefused(ValueError):
    """`validate_document_url` refused; `reason` is a short sentence."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_document_url(raw: str) -> str:
    """The URL a document may be queued under, or `DocumentUrlRefused`.

    Shape only — this never resolves or connects. HTTPS, a public-looking
    hostname (no IP literal, no localhost / internal suffix, a letter TLD),
    no userinfo, the default port, at most `MAX_URL_LENGTH` characters.
    The fragment is dropped (it never reaches the server and would make
    one document dedup as two); host and scheme are lower-cased.
    """
    url = (raw or "").strip()
    if not url:
        raise DocumentUrlRefused("url is empty")
    if len(url) > MAX_URL_LENGTH:
        raise DocumentUrlRefused(f"url is longer than {MAX_URL_LENGTH} characters")
    if _CONTROL.search(url):
        raise DocumentUrlRefused("url contains whitespace, control characters or '\\'")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise DocumentUrlRefused("url is not a valid URL") from None
    if parts.scheme.lower() != "https":
        raise DocumentUrlRefused("url must use https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise DocumentUrlRefused("url must not carry credentials")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise DocumentUrlRefused("url has no host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise DocumentUrlRefused("url must name a host, not an IP address")
    if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        raise DocumentUrlRefused("url names an internal host")
    labels = host.split(".")
    if len(labels) < 2 or not _TLD.match(labels[-1]):
        raise DocumentUrlRefused("url host is not a public domain name")
    if port not in (None, 443):
        raise DocumentUrlRefused("url must use the default https port")
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def clean_title(raw: str | None) -> str | None:
    """Printable, single-line, at most `MAX_TITLE_LENGTH`; None when empty."""
    if raw is None:
        return None
    text = "".join(
        " " if unicodedata.category(ch).startswith(("C", "Z")) else ch
        for ch in str(raw)
    )
    text = " ".join(text.split())
    return text[:MAX_TITLE_LENGTH] or None


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


# --------------------------------------------------------------------------
# Queuing (no network)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueOutcome:
    row: PartDatasheet
    created: bool


def _pending_agent_requests(db: Session, *, workspace_id: UUID) -> int:
    """Agent-queued rows still waiting: queued, or failed with attempts left."""
    max_attempts = settings().DATASHEET_BACKFILL_MAX_ATTEMPTS
    return int(
        db.execute(
            select(func.count(PartDatasheet.id))
            .where(PartDatasheet.workspace_id == workspace_id)
            .where(PartDatasheet.origin == ds.ORIGIN_AGENT)
            .where(PartDatasheet.archived_at.is_(None))
            .where(PartDatasheet.status != ds.STATUS_STORED)
            .where(PartDatasheet.attempts < max_attempts)
        ).scalar_one()
    )


def request_document(
    db: Session,
    *,
    ws: Workspace,
    part: Part,
    url: str,
    kind: str,
    title: str | None,
    user_id: UUID | None,
) -> QueueOutcome:
    """Queue `url` for the cron to download onto `part`. Never fetches.

    Raises the app's structured HTTP errors (`document.invalid_url`,
    `document.invalid_kind`, `document.queue_full`) so the MCP layer
    translates them like any other refusal. A URL already recorded for
    this part — as its datasheet, a media link or an earlier request —
    returns that row with `created=False` and changes nothing.

    The caller has already resolved `part` inside `ws`.
    """
    if kind not in _DOCUMENT_KIND_SET:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCodes.DOCUMENT_INVALID_KIND,
            f"kind must be one of {', '.join(DOCUMENT_KINDS)}",
        )
    try:
        normalised = validate_document_url(url)
    except DocumentUrlRefused as exc:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCodes.DOCUMENT_INVALID_URL,
            exc.reason,
        )
    if title is not None and len(title) > MAX_TITLE_LENGTH:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCodes.DOCUMENT_INVALID_TITLE,
            f"title is longer than {MAX_TITLE_LENGTH} characters",
        )

    url_sha = ds._url_sha256(normalised)
    existing = ds._existing_record(
        db, workspace_id=ws.id, part_id=part.id, url_sha=url_sha
    )
    if existing is not None:
        return QueueOutcome(row=existing, created=False)

    cap = settings().DOCUMENT_REQUEST_PENDING_CAP
    if _pending_agent_requests(db, workspace_id=ws.id) >= cap:
        raise_http(
            status.HTTP_429_TOO_MANY_REQUESTS,
            ErrorCodes.DOCUMENT_QUEUE_FULL,
            f"this workspace already has {cap} requested documents waiting "
            "for the hourly download; try again after it has drained",
        )

    row = ds._upsert_record(
        db,
        workspace_id=ws.id,
        part_id=part.id,
        source_url=normalised,
        url_sha=url_sha,
        user_id=user_id,
        kind=kind,
        origin=ds.ORIGIN_AGENT,
        title=clean_title(title),
        status=ds.STATUS_PENDING,
    )
    return QueueOutcome(row=row, created=True)


def queue_media_links(
    db: Session,
    *,
    workspace_id: UUID,
    part: Part,
    links: Sequence[dict[str, Any]] | None,
    user_id: UUID | None,
) -> int:
    """Queue a provider record's document links as PENDING rows.

    Called by the create and refresh paths with `record["media_links"]`.
    DB-only — the cron downloads — so it is safe inside a request handler.
    Idempotent: a URL already recorded for this part (by any origin) is
    left alone, and a link that fails `validate_document_url` or carries
    an unknown kind is skipped. Returns how many rows were added.
    """
    added = 0
    seen: set[str] = set()
    for link in list(links or [])[:MAX_MEDIA_LINKS_PER_PART]:
        if not isinstance(link, dict):
            continue
        kind = link.get("kind")
        if kind not in _DOCUMENT_KIND_SET:
            continue
        try:
            url = validate_document_url(str(link.get("url") or ""))
        except DocumentUrlRefused:
            continue
        url_sha = ds._url_sha256(url)
        if url_sha in seen:
            continue
        seen.add(url_sha)
        if ds._existing_record(
            db, workspace_id=workspace_id, part_id=part.id, url_sha=url_sha
        ) is not None:
            continue
        ds._upsert_record(
            db,
            workspace_id=workspace_id,
            part_id=part.id,
            source_url=url,
            url_sha=url_sha,
            user_id=user_id,
            kind=kind,
            origin=ds.ORIGIN_MEDIA_LINK,
            title=clean_title(link.get("title")),
            status=ds.STATUS_PENDING,
        )
        added += 1
    return added


# --------------------------------------------------------------------------
# Fetching (cron only)
# --------------------------------------------------------------------------


def document_file_name(part: Part, row: PartDatasheet, ext: str) -> str:
    """Save-As name: `{mpn}-{title or kind}.{ext}`, filesystem-safe."""
    base = (part.mpn or part.name or "part").strip()
    base = _FILENAME_UNSAFE.sub("_", base).strip("._-")[:80] or "part"
    label = _FILENAME_UNSAFE.sub("_", (row.title or row.kind or "document")).strip("._-")
    label = label[:100] or "document"
    return f"{base}-{label}.{ext or 'bin'}"


def fetch_document(db: Session, *, ws: Workspace, part: Part, row: PartDatasheet) -> bool:
    """Download one queued document and attach it. True when stored.

    Cron-only: this is the second of the two places that pass
    `allow_any_host=True` (ADR-0033, ADR-0036). A refusal is written to the
    row with the same terminal / retryable split the datasheet half uses.
    """
    if row.status == ds.STATUS_STORED and row.attachment_id is not None:
        return True
    fetch_kind = "datasheet" if row.kind == "datasheet" else FETCH_KIND_DOCUMENT
    # Committed before the fetch so a run killed mid-download leaves this
    # row in cooldown rather than first in line forever (`begin_attempt`).
    ds.begin_attempt(db, row, user_id=None)
    result = assets.fetch_asset(
        row.source_url, str(ws.id), fetch_kind, allow_any_host=True
    )
    if result.stored is None:
        ds._mark_failed(
            row,
            failure_code=result.failure_code or "unknown",
            user_id=None,
            attempt_recorded=True,
        )
        db.flush()
        ds._log_audit(
            db,
            ws=ws,
            action="part.document.fetch_failed",
            part_id=part.id,
            datasheet_id=row.id,
            detail=(
                f"host={ds._audit_host(row.source_url)} kind={row.kind} "
                f"reason={row.failure_code}"
            ),
        )
        return False

    stored = result.stored
    attachment = ds._find_or_create_attachment(
        db,
        workspace_id=ws.id,
        part=part,
        storage_key=stored.storage_key,
        file_name=document_file_name(part, row, stored.ext),
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        user_id=None,
        file_type=row.kind,
    )
    ds._mark_stored(
        row,
        attachment=attachment,
        storage_key=stored.storage_key,
        content_sha256=stored.sha256,
        content_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        user_id=None,
    )
    db.flush()
    ds._log_audit(
        db,
        ws=ws,
        action="part.document.stored",
        part_id=part.id,
        datasheet_id=row.id,
        detail=(
            f"host={ds._audit_host(row.source_url)} kind={row.kind} "
            f"bytes={stored.size_bytes}"
        ),
    )
    return True


def _document_candidates(db: Session, *, limit: int) -> list[tuple[UUID, UUID, UUID]]:
    """(workspace_id, part_id, row_id) of queued documents due for a fetch.

    Mirrors the datasheet half's "settled" test — stored with a live
    attachment, out of attempts, or inside the retry cooldown — written
    without a NULL in any branch, because here it is negated directly
    rather than inside an EXISTS (a NULL `last_attempt_at` would otherwise
    turn NOT(...) into NULL and hide every never-attempted row).
    """
    config = settings()
    max_attempts = config.DATASHEET_BACKFILL_MAX_ATTEMPTS
    retry_cutoff = utcnow() - timedelta(
        seconds=config.DATASHEET_BACKFILL_RETRY_AFTER_SECONDS
    )
    settled = (
        ((PartDatasheet.status == ds.STATUS_STORED) & PartDatasheet.attachment_id.is_not(None))
        | (PartDatasheet.attempts >= max_attempts)
        | (
            PartDatasheet.last_attempt_at.is_not(None)
            & (PartDatasheet.last_attempt_at > retry_cutoff)
        )
    )
    # Fair across workspaces: rank each workspace's due rows oldest-first,
    # keep at most DOCUMENT_BACKFILL_PER_WORKSPACE of them, and interleave by
    # rank — every workspace's oldest row before anyone's second.
    ranked = (
        select(
            PartDatasheet.workspace_id,
            PartDatasheet.part_id,
            PartDatasheet.id,
            PartDatasheet.created_at,
            func.row_number()
            .over(
                partition_by=PartDatasheet.workspace_id,
                order_by=(PartDatasheet.created_at, PartDatasheet.id),
            )
            .label("ws_rank"),
        )
        .join(
            Part,
            (Part.id == PartDatasheet.part_id)
            & (Part.workspace_id == PartDatasheet.workspace_id),
        )
        .where(PartDatasheet.origin != ds.ORIGIN_DATASHEET_FIELD)
        .where(PartDatasheet.archived_at.is_(None))
        .where(Part.archived_at.is_(None))
        .where(~settled)
        .subquery()
    )
    stmt = (
        select(ranked.c.workspace_id, ranked.c.part_id, ranked.c.id)
        .where(ranked.c.ws_rank <= DOCUMENT_BACKFILL_PER_WORKSPACE)
        .order_by(ranked.c.ws_rank, ranked.c.created_at, ranked.c.id)
        .limit(limit)
    )
    return [tuple(r) for r in db.execute(stmt).all()]  # type: ignore[misc]


def backfill_documents(
    db: Session,
    *,
    breaker: ds.HostBreaker | None = None,
    deadline: float | None = None,
    limit: int | None = None,
) -> int:
    """The document half of `datasheet-backfill`. Returns rows processed.

    Called by `datasheets.backfill_missing_datasheets` while it holds the
    backfill's session advisory lock, with the run's shared host breaker
    and deadline. Commits per candidate for the same reason the datasheet
    half does: a `timeout 600` kill must not roll back attempt counters.
    """
    config = settings()
    batch = limit if limit is not None else config.DOCUMENT_BACKFILL_BATCH_SIZE
    if batch <= 0:
        return 0
    candidates = _document_candidates(db, limit=batch)
    if not candidates:
        return 0

    breaker = breaker if breaker is not None else ds.HostBreaker()
    workspaces: dict[UUID, Workspace | None] = {}
    processed = stored = failed = skipped = 0
    for workspace_id, part_id, row_id in candidates:
        if deadline is not None and time.monotonic() >= deadline:
            logger.info("document-backfill run budget spent; resuming next run")
            break
        if workspace_id not in workspaces:
            workspaces[workspace_id] = db.get(Workspace, workspace_id)
        ws = workspaces[workspace_id]
        part = db.execute(
            select(Part).where(Part.id == part_id).where(Part.workspace_id == workspace_id)
        ).scalar_one_or_none()
        row = db.execute(
            select(PartDatasheet)
            .where(PartDatasheet.id == row_id)
            .where(PartDatasheet.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if ws is None or part is None or row is None:
            continue
        host = _host(row.source_url)
        if breaker.is_blocked(host):
            skipped += 1
            continue

        ok = fetch_document(db, ws=ws, part=part, row=row)
        breaker.record(
            db, ws=ws, host=host, failure_code=None if ok else row.failure_code
        )
        db.commit()
        processed += 1
        stored += 1 if ok else 0
        failed += 0 if ok else 1

    logger.info(
        "document-backfill processed=%d stored=%d failed=%d skipped=%d",
        processed,
        stored,
        failed,
        skipped,
    )
    return processed


# --------------------------------------------------------------------------
# Listing (read-only)
# --------------------------------------------------------------------------


def _sha256_of(storage_key: str, record: PartDatasheet | None) -> str | None:
    """The content hash: recorded, implied by the path, or computed."""
    if record is not None and record.content_sha256:
        return record.content_sha256
    match = _CONTENT_ADDRESSED.match(storage_key or "")
    if match:
        return match.group(1)
    path = os.path.join(settings().UPLOAD_DIR, storage_key or "")
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _pending_status(row: PartDatasheet) -> str:
    if row.status == ds.STATUS_FAILED or (row.attempts or 0) > 0:
        if row.attempts >= settings().DATASHEET_BACKFILL_MAX_ATTEMPTS:
            return "failed"
        return "retrying"
    return "queued"


def download_url(attachment_id: UUID) -> str:
    """Absolute URL of the attachment download route (Bearer-token friendly)."""
    base = settings().APP_BASE_URL.rstrip("/")
    return f"{base}/api/attachments/{attachment_id}/download"


def list_part_documents(
    db: Session,
    *,
    ws: Workspace,
    part: Part,
    kinds: Iterable[str] | None = None,
) -> dict[str, Any]:
    """A part's local PDFs plus what is still waiting to be downloaded.

    `documents` are the part's PDF attachments — downloaded datasheets and
    documents, and user uploads (kind `other` unless the upload was typed
    with one of `DOCUMENT_KINDS`). `pending` are recorded URLs with no
    stored file yet. Workspace-scoped on every query; the caller resolved
    `part` inside `ws`.
    """
    wanted: set[str] | None = None
    if kinds:
        wanted = set(kinds)
        unknown = sorted(wanted - _DOCUMENT_KIND_SET)
        if unknown:
            raise_http(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                ErrorCodes.DOCUMENT_INVALID_KIND,
                f"unknown kind(s) {', '.join(unknown)}; "
                f"expected one of {', '.join(DOCUMENT_KINDS)}",
            )

    records = list(
        db.execute(
            select(PartDatasheet)
            .where(PartDatasheet.workspace_id == ws.id)
            .where(PartDatasheet.part_id == part.id)
            .where(PartDatasheet.archived_at.is_(None))
            .order_by(PartDatasheet.created_at, PartDatasheet.id)
        ).scalars()
    )
    attachments = list(
        db.execute(
            select(Attachment)
            .where(Attachment.workspace_id == ws.id)
            .where(Attachment.object_type == "part")
            .where(Attachment.object_id == part.id)
            .where(Attachment.archived_at.is_(None))
            .where(Attachment.mime_type == PDF_MIME)
            .order_by(Attachment.created_at, Attachment.id)
        ).scalars()
    )
    by_attachment = {r.attachment_id: r for r in records if r.attachment_id is not None}
    live_attachment_ids = {a.id for a in attachments}

    documents: list[dict[str, Any]] = []
    for a in attachments:
        record = by_attachment.get(a.id)
        if record is not None:
            kind = record.kind
        else:
            kind = a.file_type if a.file_type in _DOCUMENT_KIND_SET else "other"
        if wanted is not None and kind not in wanted:
            continue
        retrieved = (record.fetched_at if record is not None else None) or a.created_at
        documents.append(
            {
                "id": str(a.id),
                "kind": kind,
                "title": record.title if record is not None else None,
                "file_name": a.file_name,
                "size_bytes": a.size_bytes,
                "sha256": _sha256_of(a.storage_key, record),
                "mime": a.mime_type,
                "source_url": record.source_url if record is not None else None,
                "retrieved_at": retrieved.isoformat() if retrieved else None,
                "download_url": download_url(a.id),
            }
        )

    pending: list[dict[str, Any]] = []
    for r in records:
        if r.status == ds.STATUS_STORED and r.attachment_id in live_attachment_ids:
            continue
        if r.status == ds.STATUS_STORED and r.attachment_id is not None:
            # Stored against an attachment that is not a live PDF of this
            # part (e.g. a non-PDF legacy adoption) — nothing to wait for.
            continue
        if wanted is not None and r.kind not in wanted:
            continue
        entry: dict[str, Any] = {
            "source_url": r.source_url,
            "kind": r.kind,
            "title": r.title,
            "status": _pending_status(r),
        }
        if r.failure_code:
            entry["failure_code"] = r.failure_code
        pending.append(entry)

    return {"documents": documents, "pending": pending}
