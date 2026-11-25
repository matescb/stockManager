"""Accept or revoke a pin/pad finding for one part (SM-008b).

An acceptance is a person's statement that they looked at a finding on
these two files and it is fine — an exposed pad the symbol leaves out on
purpose, a connector shell the board grounds anyway. It is recorded
against the finding's kind and number AND both files' sha256s
(`PinPadAcceptance`), so replacing either file retires it: the check
then lists it as stale and the finding counts again.

Rules:

* The finding must exist in the check computed NOW, on the part's
  current files — you cannot accept something you were not shown. A
  `not-checkable` part has no findings to accept.
* `electrical` findings may be accepted too, but only with a note: that
  is a human overriding a likely open or short, and the reason belongs
  on the record.
* Accepting the same finding on the same files again updates the note,
  who and when.
* Revoking removes every acceptance of that kind and number for the
  part, stale ones included, so a user can also clear stale rows.

Who may call these is the route's business (owner/admin session only);
this module trusts its caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.eda import pin_pad
from app.domain.eda.models import PinPadAcceptance
from app.domain.eda.pin_pad_service import check_parts
from app.domain.parts.models import Part

NOTE_MAX = 2000


@dataclass(frozen=True)
class Accepted:
    row: PinPadAcceptance
    finding: dict[str, Any]


def _finding(check: dict[str, Any], kind: str, number: str) -> dict[str, Any]:
    if check["verdict"] == pin_pad.NOT_CHECKABLE:
        raise_http(
            status.HTTP_409_CONFLICT,
            ErrorCodes.EDA_PIN_PAD_NOT_CHECKABLE,
            "the pin/pad check cannot run on this part, so it has no findings "
            "to accept",
            reason=check.get("reason"),
        )
    for finding in check["findings"]["classified"]:
        if finding["kind"] == kind and finding["number"] == number:
            return finding
    raise_http(
        status.HTTP_404_NOT_FOUND,
        ErrorCodes.EDA_PIN_PAD_FINDING_NOT_FOUND,
        "the part's current files have no such finding",
        finding_kind=kind,
        number=number,
    )


def accept(
    db: Session,
    *,
    ws_id: UUID,
    part: Part,
    user_id: UUID,
    kind: str,
    number: str,
    note: str | None,
) -> Accepted:
    """Record (or refresh) the acceptance of one current finding."""
    check = check_parts(db, ws_id, [part])[part.id]
    finding = _finding(check, kind, number)
    note = (note or "").strip() or None
    if finding["class"] == pin_pad.ELECTRICAL and note is None:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.EDA_PIN_PAD_NOTE_REQUIRED,
            "accepting an electrical finding needs a note saying why it is fine",
        )
    row = db.execute(
        select(PinPadAcceptance)
        .where(PinPadAcceptance.workspace_id == ws_id)
        .where(PinPadAcceptance.part_id == part.id)
        .where(PinPadAcceptance.symbol_sha256 == check["symbol_sha256"])
        .where(PinPadAcceptance.footprint_sha256 == check["footprint_sha256"])
        .where(PinPadAcceptance.finding_kind == kind)
        .where(PinPadAcceptance.number == number)
    ).scalar_one_or_none()
    if row is None:
        row = PinPadAcceptance(
            workspace_id=ws_id,
            part_id=part.id,
            symbol_sha256=check["symbol_sha256"],
            footprint_sha256=check["footprint_sha256"],
            finding_kind=kind,
            number=number,
        )
        db.add(row)
    row.finding_class = finding["class"]
    row.accepted_by = user_id
    row.accepted_at = utcnow()
    row.note = note
    db.flush()
    return Accepted(row=row, finding=finding)


def revoke(db: Session, *, ws_id: UUID, part: Part, kind: str, number: str) -> int:
    """Delete every acceptance of (kind, number) on the part; how many."""
    removed = db.execute(
        delete(PinPadAcceptance)
        .where(PinPadAcceptance.workspace_id == ws_id)
        .where(PinPadAcceptance.part_id == part.id)
        .where(PinPadAcceptance.finding_kind == kind)
        .where(PinPadAcceptance.number == number)
    ).rowcount
    if not removed:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.EDA_PIN_PAD_ACCEPTANCE_NOT_FOUND,
            "no acceptance of that finding on this part",
            finding_kind=kind,
            number=number,
        )
    db.flush()
    return removed
