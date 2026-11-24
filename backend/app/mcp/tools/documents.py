"""Part document tools — list a part's local PDFs, queue one to fetch (SM-016).

`list_part_documents` is a read: it reports the PDFs a part already has
locally (datasheets, application notes, PCNs, user uploads) with an
absolute `download_url` that accepts the same Bearer token, plus the
documents still waiting for the hourly download.

`request_part_document` writes one queue row and NEVER fetches. The
download happens in the `datasheet-backfill` cron job, the only code that
may reach a host outside the Mouser/DigiKey allow-list (ADR-0033,
ADR-0036). The rules live in `domain/parts/services/documents.py`; this
module is the agent-facing contract.
"""
from __future__ import annotations

from typing import Any, Literal

from app.domain.parts.services import datasheets as ds
from app.domain.parts.services import documents as service
from app.mcp.principal import Caller
from app.mcp.tools._registry import tool
from app.mcp.tools._shared import audit, resolve_part

DocumentKind = Literal[
    "datasheet",
    "application_note",
    "technical_note",
    "design_note",
    "programming_manual",
    "pcn",
    "other",
]

# Queuing is a cheap DB write, but every row becomes an outbound fetch
# later; the per-workspace pending cap is the real bound, this caps bursts.
_REQUEST_RATE = "30/minute"


@tool()
def list_part_documents(
    caller: Caller,
    part_id_or_mpn: str,
    kinds: list[DocumentKind] | None = None,
) -> dict[str, Any]:
    """List the PDFs stored locally for a part, and the ones still queued.

    Args:
        part_id_or_mpn: Part id, or its exact manufacturer part number.
        kinds: Optional filter, e.g. ["application_note", "pcn"].

    Returns `documents`: one entry per local PDF with `id`, `kind`
    (datasheet, application_note, technical_note, design_note,
    programming_manual, pcn, other), `title`, `file_name`, `size_bytes`,
    `sha256`, `mime`, `source_url` (where it was downloaded from; null for
    a user upload), `retrieved_at` and `download_url`. GET `download_url`
    with the SAME `Authorization: Bearer <token>` header you use here to
    get the bytes — a read-only token is enough. User-uploaded PDFs are
    included, as kind `other` unless they were typed.

    `pending` lists documents recorded but not downloaded yet:
    `source_url`, `kind`, `title`, `status` ("queued" — never tried,
    "retrying" — failed and will be retried after a cooldown, "failed" —
    gave up) and `failure_code` when a download failed (e.g. http_404,
    unexpected_type for a page that is not a PDF). Downloads run in an
    hourly background job; nothing here fetches.
    """
    part = resolve_part(caller, part_id_or_mpn)
    out = service.list_part_documents(caller.db, ws=caller.ws, part=part, kinds=kinds)
    return {"part_id": str(part.id), **out}


@tool(writes=True, rate=_REQUEST_RATE)
def request_part_document(
    caller: Caller,
    part_id_or_mpn: str,
    url: str,
    kind: DocumentKind,
    title: str | None = None,
) -> dict[str, Any]:
    """Queue a PDF you found (e.g. on the manufacturer's site) for download.

    Nothing is fetched now. An hourly background job downloads the URL,
    keeps it only if it really is a PDF (at most 10 MB, HTTPS, no
    redirects), and attaches it to the part; then it shows up in
    `list_part_documents` with a `download_url`. Link the PDF itself, not
    the page that links to it.

    Args:
        part_id_or_mpn: Part id, or its exact manufacturer part number.
        url: https URL of the PDF. Refused: http, IP addresses, localhost
            or internal hostnames, credentials in the URL, a non-default
            port, longer than 2048 characters.
        kind: datasheet, application_note, technical_note, design_note,
            programming_manual, pcn or other.
        title: Optional human title, e.g. "AN2867 Oscillator design guide".

    Returns `queued: true` with the new `request` (`source_url`, `kind`,
    `title`, `status: "queued"`). A URL this part already has recorded —
    its datasheet, a provider document link, or an earlier request —
    returns `queued: false` and the existing entry unchanged, with
    `download_url` when it is already stored. Refusals:
    `document.invalid_url`, `document.invalid_kind`,
    `document.invalid_title`, `document.queue_full` (too many requests
    waiting in this workspace; wait for the hourly job to drain them).
    """
    part = resolve_part(caller, part_id_or_mpn)
    outcome = service.request_document(
        caller.db,
        ws=caller.ws,
        part=part,
        url=url,
        kind=kind,
        title=title,
        user_id=caller.user.id,
    )
    row = outcome.row
    if outcome.created:
        # Host and kind only — a document URL's query string can carry a
        # signed token, and `audit_log.comment` must never hold one.
        audit(
            caller,
            action="part.document.requested",
            target_type="part_datasheet",
            target_id=row.id,
            comment=(
                f"part={part.id} host={ds._audit_host(row.source_url)} kind={row.kind}"
            ),
        )
    stored = row.status == ds.STATUS_STORED and row.attachment_id is not None
    payload: dict[str, Any] = {
        "queued": outcome.created,
        "part_id": str(part.id),
        "request": {
            "source_url": row.source_url,
            "kind": row.kind,
            "title": row.title,
            "status": "stored" if stored else service._pending_status(row),
        },
    }
    if stored:
        payload["download_url"] = service.download_url(row.attachment_id)
    return payload
