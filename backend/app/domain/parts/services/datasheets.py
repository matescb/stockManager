"""Local datasheet store — fetch, attach, backfill.

A part's datasheet has always been a URL sitting in a `custom_fields` row.
This module turns that URL into a file we own:

1. **Fetch** through `services/assets.py::fetch_asset` with the datasheet
   policy (no host allow-list, pinned resolved IP, HTTPS only — ADR-0033).
2. **Store** content-addressed at `{UPLOAD_DIR}/parts/{ws}/{sha}.{ext}`,
   the existing invariant, and register the file as an `Attachment` with
   `file_type='datasheet'` so it is a first-class object: it shows up in
   the part's attachment list, downloads through the existing route, and
   inherits the polymorphic-cleanup listeners on part hard-delete.
3. **Record** the outcome in `part_datasheets`, one row per
   (workspace, part, source URL), which is what makes the backfill
   idempotent and resumable.

What this module deliberately does NOT do
-----------------------------------------
It never rewrites the part's `datasheet_url` custom field. That field is
provider-owned on linked parts (see `provider_fields.py`) and a provider
refresh reconciles it; if the backfill wrote a local path there the two
would fight on every refresh. The custom field stays the upstream
provenance record and `part_datasheets` is the local one.

Forward compatibility
---------------------
The planned Datalab conversion (PDF → markdown/JSON + extracted images)
writes its output as further `attachments` rows on the same part and
records the manifest in `part_datasheets.derived` (JSONB) with
`derived_status` tracking progress. Nothing here needs a schema change for
that step.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.advisory_locks import DATASHEET_BACKFILL_LOCK_CLASSID
from app.core.config import settings
from app.core.time import utcnow
from app.domain.attachments.models import Attachment
from app.domain.audit.service import log as audit_log
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part, PartDatasheet
from app.domain.parts.services import assets
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

DATASHEET_FIELD_KEY = "datasheet_url"
DATASHEET_FILE_TYPE = "datasheet"
LOCAL_ASSET_PREFIX = "/api/parts/assets/"

STATUS_STORED = "stored"
STATUS_FAILED = "failed"

_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class BackfillOutcome:
    """Per-run counters. Returned by the job so the log line is useful."""

    processed: int = 0
    stored: int = 0
    adopted: int = 0
    failed: int = 0

    @property
    def affected(self) -> int:
        return self.processed


def _url_sha256(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _is_local_asset_url(value: str) -> bool:
    return value.strip().startswith(LOCAL_ASSET_PREFIX)


def datasheet_file_name(part: Part, ext: str) -> str:
    """A human-meaningful download name for the stored PDF.

    The bytes are content-addressed on disk; this is only what the browser
    offers in the Save-As dialog and what the attachment list shows.
    """
    base = (part.mpn or part.name or "datasheet").strip()
    base = _FILENAME_UNSAFE.sub("_", base).strip("._-")[:80] or "datasheet"
    suffix = ext if ext else "bin"
    return f"{base}-datasheet.{suffix}"


def _existing_record(
    db: Session, *, workspace_id: UUID, part_id: UUID, url_sha: str
) -> PartDatasheet | None:
    return db.execute(
        select(PartDatasheet)
        .where(PartDatasheet.workspace_id == workspace_id)
        .where(PartDatasheet.part_id == part_id)
        .where(PartDatasheet.source_url_sha256 == url_sha)
    ).scalar_one_or_none()


def _find_or_create_attachment(
    db: Session,
    *,
    workspace_id: UUID,
    part: Part,
    storage_key: str,
    file_name: str,
    mime_type: str | None,
    size_bytes: int,
    user_id: UUID | None,
) -> Attachment:
    """Return the datasheet attachment for this (part, storage_key) pair.

    Re-uses an existing row rather than adding a duplicate: content is
    addressed by hash, so re-running the backfill after an attachment row
    already exists must not produce a second one.
    """
    existing = db.execute(
        select(Attachment)
        .where(Attachment.workspace_id == workspace_id)
        .where(Attachment.object_type == "part")
        .where(Attachment.object_id == part.id)
        .where(Attachment.storage_key == storage_key)
        .where(Attachment.file_type == DATASHEET_FILE_TYPE)
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = Attachment(
        workspace_id=workspace_id,
        object_type="part",
        object_id=part.id,
        file_name=file_name,
        file_type=DATASHEET_FILE_TYPE,
        mime_type=mime_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        uploaded_by=user_id,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(row)
    db.flush()
    return row


def _upsert_record(
    db: Session,
    *,
    workspace_id: UUID,
    part_id: UUID,
    source_url: str,
    url_sha: str,
    user_id: UUID | None,
) -> PartDatasheet:
    row = _existing_record(
        db, workspace_id=workspace_id, part_id=part_id, url_sha=url_sha
    )
    if row is None:
        row = PartDatasheet(
            workspace_id=workspace_id,
            part_id=part_id,
            source_url=source_url,
            source_url_sha256=url_sha,
            status=STATUS_FAILED,
            attempts=0,
            derived_status="none",
            derived={},
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(row)
        db.flush()
    return row


def _mark_stored(
    row: PartDatasheet,
    *,
    attachment: Attachment,
    storage_key: str,
    content_sha256: str,
    content_type: str | None,
    size_bytes: int,
    user_id: UUID | None,
) -> None:
    now = utcnow()
    row.status = STATUS_STORED
    row.attachment_id = attachment.id
    row.storage_key = storage_key
    row.content_sha256 = content_sha256
    row.content_type = content_type
    row.size_bytes = size_bytes
    row.fetched_at = now
    row.last_attempt_at = now
    row.failure_code = None
    row.updated_by = user_id


def _mark_failed(row: PartDatasheet, *, failure_code: str, user_id: UUID | None) -> None:
    row.status = STATUS_FAILED
    row.attempts = (row.attempts or 0) + 1
    row.last_attempt_at = utcnow()
    row.failure_code = failure_code
    row.updated_by = user_id


def _audit_host(url: str) -> str:
    """Hostname only — never the path or query string.

    A signed vendor URL can carry a token in its query string, and
    `audit_log.comment` must never hold credentials (CLAUDE.md).
    """
    try:
        return (urlparse(url).hostname or "unknown").lower()
    except ValueError:
        return "unknown"


def _log_audit(
    db: Session,
    *,
    ws: Workspace | None,
    action: str,
    part_id: UUID,
    datasheet_id: UUID,
    detail: str,
) -> None:
    if ws is None:
        return
    audit_log(
        db,
        ws=ws,
        # The backfill runs in the cron sidecar with no acting user; the
        # action name plus a NULL user_id is how a system mutation reads.
        user=None,
        action=action,
        target_type="part_datasheet",
        target_ids=[datasheet_id, part_id],
        comment=detail,
    )


def adopt_local_datasheet(
    db: Session,
    *,
    ws: Workspace,
    part: Part,
    source_url: str,
    user_id: UUID | None = None,
) -> PartDatasheet | None:
    """Register an ALREADY-local `/api/parts/assets/...` datasheet.

    Prod has a handful of parts whose `datasheet_url` was localised by the
    old provider-import path but that were never registered as attachments
    (the `attachments` table was empty before this feature). This adopts
    them without a network round trip.

    Returns None when the referenced file does not belong to this workspace
    or is not on disk — those are recorded as a failure row, not silently
    skipped.
    """
    url_sha = _url_sha256(source_url)
    row = _upsert_record(
        db,
        workspace_id=ws.id,
        part_id=part.id,
        source_url=source_url,
        url_sha=url_sha,
        user_id=user_id,
    )
    if row.status == STATUS_STORED and row.attachment_id is not None:
        return row

    remainder = source_url.strip()[len(LOCAL_ASSET_PREFIX):].split("?", 1)[0]
    parts = remainder.split("/")
    if len(parts) != 2:
        _mark_failed(row, failure_code="local_path_invalid", user_id=user_id)
        return None
    asset_ws_id, filename = parts
    # Workspace isolation: a local asset URL that names a DIFFERENT
    # workspace's folder must never be adopted into this one.
    if asset_ws_id != str(ws.id):
        _mark_failed(row, failure_code="local_wrong_workspace", user_id=user_id)
        return None
    if "/" in filename or "\\" in filename or filename.startswith("."):
        _mark_failed(row, failure_code="local_path_invalid", user_id=user_id)
        return None

    storage_key = os.path.join("parts", str(ws.id), filename)
    abs_path = os.path.join(settings().UPLOAD_DIR, storage_key)
    if not os.path.isfile(abs_path):
        _mark_failed(row, failure_code="local_file_missing", user_id=user_id)
        return None

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    size_bytes = os.path.getsize(abs_path)
    attachment = _find_or_create_attachment(
        db,
        workspace_id=ws.id,
        part=part,
        storage_key=storage_key,
        file_name=datasheet_file_name(part, ext),
        mime_type=assets.mime_for_ext(ext),
        size_bytes=size_bytes,
        user_id=user_id,
    )
    _mark_stored(
        row,
        attachment=attachment,
        storage_key=storage_key,
        # The content-addressed filename IS the sha of the bytes.
        content_sha256=filename.rsplit(".", 1)[0],
        content_type=assets.mime_for_ext(ext),
        size_bytes=size_bytes,
        user_id=user_id,
    )
    db.flush()
    _log_audit(
        db,
        ws=ws,
        action="part.datasheet.adopted",
        part_id=part.id,
        datasheet_id=row.id,
        detail="source=local",
    )
    return row


def fetch_datasheet_for_part(
    db: Session,
    *,
    ws: Workspace,
    part: Part,
    source_url: str,
    user_id: UUID | None = None,
) -> PartDatasheet | None:
    """Download `source_url` and attach it to `part`.

    Returns the `PartDatasheet` row on success, or None when the fetch was
    refused — in which case a `failed` row carrying the reason is still
    written, so the backfill can move on and never retries in a tight loop.

    Never raises for a network reason: `assets.fetch_asset` maps every
    refusal to a failure code.
    """
    url_sha = _url_sha256(source_url)
    row = _upsert_record(
        db,
        workspace_id=ws.id,
        part_id=part.id,
        source_url=source_url,
        url_sha=url_sha,
        user_id=user_id,
    )
    if row.status == STATUS_STORED and row.attachment_id is not None:
        # Idempotent: already downloaded under this exact URL.
        return row

    # `allow_any_host=True` is the ADR-0033 opt-in. This module is the ONLY
    # caller that passes it, and it is only reached from the cron backfill —
    # never from a request handler.
    result = assets.fetch_asset(
        source_url, str(ws.id), "datasheet", allow_any_host=True
    )
    if result.stored is None:
        _mark_failed(
            row, failure_code=result.failure_code or "unknown", user_id=user_id
        )
        db.flush()
        _log_audit(
            db,
            ws=ws,
            action="part.datasheet.fetch_failed",
            part_id=part.id,
            datasheet_id=row.id,
            detail=f"host={_audit_host(source_url)} reason={row.failure_code}",
        )
        return None

    stored = result.stored
    attachment = _find_or_create_attachment(
        db,
        workspace_id=ws.id,
        part=part,
        storage_key=stored.storage_key,
        file_name=datasheet_file_name(part, stored.ext),
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        user_id=user_id,
    )
    _mark_stored(
        row,
        attachment=attachment,
        storage_key=stored.storage_key,
        content_sha256=stored.sha256,
        content_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        user_id=user_id,
    )
    db.flush()
    _log_audit(
        db,
        ws=ws,
        action="part.datasheet.stored",
        part_id=part.id,
        datasheet_id=row.id,
        detail=f"host={_audit_host(source_url)} bytes={stored.size_bytes}",
    )
    return row


def _candidate_rows(db: Session, *, limit: int) -> list[tuple[UUID, UUID, str]]:
    """(workspace_id, part_id, datasheet_url) still needing local storage.

    A candidate is a live part in a live workspace whose `datasheet_url`
    custom field has no `part_datasheets` row that is either already stored,
    out of retry attempts, or still inside its retry cooldown.

    Every predicate is expressed against `custom_fields.workspace_id`, and
    the part join carries the same workspace, so a row can never pair a part
    with another workspace's custom field.
    """
    config = settings()
    max_attempts = config.DATASHEET_BACKFILL_MAX_ATTEMPTS
    retry_cutoff = utcnow() - timedelta(
        seconds=config.DATASHEET_BACKFILL_RETRY_AFTER_SECONDS
    )

    settled = (
        select(PartDatasheet.id)
        .where(PartDatasheet.workspace_id == CustomField.workspace_id)
        .where(PartDatasheet.part_id == CustomField.object_id)
        .where(PartDatasheet.source_url == CustomField.value)
        .where(PartDatasheet.archived_at.is_(None))
        .where(
            # `attachment_id IS NOT NULL` is load-bearing. Deleting the
            # attachment unlinks the file but leaves this row `stored`
            # (`ON DELETE SET NULL`), so without this clause a routine
            # attachment delete would permanently exclude the part from the
            # sweep and the datasheet would be gone with nothing to notice
            # it. A freshly-orphaned row is still held off by the
            # `last_attempt_at` cooldown below, so it re-fetches on the next
            # cycle rather than immediately.
            (
                (PartDatasheet.status == STATUS_STORED)
                & PartDatasheet.attachment_id.is_not(None)
            )
            | (PartDatasheet.attempts >= max_attempts)
            | (PartDatasheet.last_attempt_at > retry_cutoff)
        )
        .exists()
    )

    stmt = (
        select(CustomField.workspace_id, CustomField.object_id, CustomField.value)
        .join(
            Part,
            (Part.id == CustomField.object_id)
            & (Part.workspace_id == CustomField.workspace_id),
        )
        .where(CustomField.object_type == "part")
        .where(CustomField.key == DATASHEET_FIELD_KEY)
        .where(CustomField.archived_at.is_(None))
        .where(CustomField.value.is_not(None))
        .where(CustomField.value != "")
        .where(Part.archived_at.is_(None))
        .where(~settled)
        .order_by(CustomField.workspace_id, CustomField.created_at, CustomField.id)
        .limit(limit)
    )
    return [
        (workspace_id, part_id, value)
        for workspace_id, part_id, value in db.execute(stmt).all()
    ]


_BACKFILL_LOCK_KEY = "datasheet-backfill"


def _try_acquire_backfill_lock(db: Session) -> bool:
    """Take the SESSION-level advisory lock guarding the backfill.

    `run_job` wraps every job in `pg_try_advisory_xact_lock`, which Postgres
    drops at the first COMMIT. This job commits per candidate on purpose, so
    it needs a lock that outlives those commits — otherwise a manual
    `python -m app.cli.run_job datasheet-backfill` could interleave with the
    sidecar's run and two workers would race the same candidates.
    """
    result = db.execute(
        text(
            "SELECT pg_try_advisory_lock("
            "CAST(:classid AS int4), CAST(hashtext(:key) AS int4)"
            ")"
        ),
        {"classid": DATASHEET_BACKFILL_LOCK_CLASSID, "key": _BACKFILL_LOCK_KEY},
    )
    return bool(result.scalar())


def _release_backfill_lock(db: Session) -> None:
    db.execute(
        text(
            "SELECT pg_advisory_unlock("
            "CAST(:classid AS int4), CAST(hashtext(:key) AS int4)"
            ")"
        ),
        {"classid": DATASHEET_BACKFILL_LOCK_CLASSID, "key": _BACKFILL_LOCK_KEY},
    )


def backfill_missing_datasheets(db: Session, *, limit: int | None = None) -> int:
    """Fetch (or adopt) datasheets for parts that don't have one locally.

    Returns the number of candidates processed — the `run_job` "affected"
    count. Resumable and idempotent by construction:

    * A `stored` row is never re-downloaded (the candidate query excludes it,
      and `fetch_datasheet_for_part` short-circuits on it anyway).
    * A failure writes a `failed` row with an incremented attempt count and
      a cooldown, so the next run picks up where this one left off instead
      of re-attempting the same dead link.
    * One bad URL cannot stall the batch: every per-candidate failure is
      confined to that candidate.
    * **Each candidate is committed before the next one starts.** The
      sidecar caps a run with `timeout 600`; under one big transaction a
      kill would roll back every `attempts` increment made in that run, so
      the deterministic candidate ordering would hand back the exact same
      URL first on the next run — a backfill that logs `exit=124` hourly
      and never advances. Committing per candidate means a killed run keeps
      everything it finished.

    Workspace isolation: candidates carry their own `workspace_id` and every
    lookup and write below is filtered by it.
    """
    config = settings()
    if config.DATASHEET_BACKFILL_INTERVAL_SECONDS == 0:
        # Disabled. Same shape as print-dispatch with PRINT_HOST empty: a
        # harmless no-op so the sidecar loop and heartbeat stay healthy.
        return 0

    if not _try_acquire_backfill_lock(db):
        logger.info("datasheet-backfill skipped: another run holds the lock")
        return 0

    try:
        return _run_backfill(db, limit=limit)
    finally:
        _release_backfill_lock(db)


def _run_backfill(db: Session, *, limit: int | None) -> int:
    config = settings()
    batch = limit if limit is not None else config.DATASHEET_BACKFILL_BATCH_SIZE
    candidates = _candidate_rows(db, limit=batch)
    if not candidates:
        return 0

    workspace_cache: dict[UUID, Workspace | None] = {}
    part_cache: dict[tuple[UUID, UUID], Part | None] = {}
    outcome = BackfillOutcome()

    for workspace_id, part_id, source_url in candidates:
        if workspace_id not in workspace_cache:
            workspace_cache[workspace_id] = db.get(Workspace, workspace_id)
        ws = workspace_cache[workspace_id]
        if ws is None:
            continue

        cache_key = (workspace_id, part_id)
        if cache_key not in part_cache:
            part_cache[cache_key] = db.execute(
                select(Part)
                .where(Part.id == part_id)
                .where(Part.workspace_id == workspace_id)
            ).scalar_one_or_none()
        part = part_cache[cache_key]
        if part is None:
            continue

        # `source_url` is passed VERBATIM, never stripped. The candidate
        # query below excludes settled rows by comparing
        # `part_datasheets.source_url` to `custom_fields.value`, so a
        # normalised copy would never match the row it came from and the
        # candidate would be re-selected on every run forever, starving the
        # rest of the batch. Whitespace is handled where the URL is parsed
        # (`assets._build_target`, `adopt_local_datasheet`) instead.
        is_local = _is_local_asset_url(source_url)
        if is_local:
            row = adopt_local_datasheet(db, ws=ws, part=part, source_url=source_url)
        else:
            row = fetch_datasheet_for_part(db, ws=ws, part=part, source_url=source_url)

        # Commit this candidate before starting the next fetch. See the
        # public docstring: the sidecar's `timeout 600` must never be able to
        # roll back attempt counters, or the sweep re-selects the same URL
        # forever. `SessionLocal` sets expire_on_commit=False, so the cached
        # Workspace/Part objects stay usable across the commit.
        db.commit()

        succeeded = row is not None
        outcome = replace(
            outcome,
            processed=outcome.processed + 1,
            stored=outcome.stored + (1 if succeeded and not is_local else 0),
            adopted=outcome.adopted + (1 if succeeded and is_local else 0),
            failed=outcome.failed + (0 if succeeded else 1),
        )

    logger.info(
        "datasheet-backfill processed=%d stored=%d adopted=%d failed=%d",
        outcome.processed,
        outcome.stored,
        outcome.adopted,
        outcome.failed,
    )
    return outcome.processed
