"""Substitute links: relation and evaluation state (SM-012).

A `PartSubstitute` row says "this other part may stand in for this one".
SM-012 adds what the link is (`relation`: pin-compatible,
footprint-compatible or functional) and whether anyone has checked it
(`evaluation`: not-evaluated, evaluated-ok or rejected), with the
`evidence` reference that justified the verdict and who/when last
changed any of the three (`evaluated_by` / `evaluated_at`).

The one writer for those fields, shared by the REST routes
(`api/routes/parts_relations.py`) and the MCP tools
(`mcp/tools/substitutes.py`), so the two surfaces cannot disagree about
validation, stamping or the audit comment.

Availability arithmetic that READS the evaluation state lives with the
shortage analysis in `domain/builds/service.py`.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.parts.models import (
    SUBSTITUTE_EVALUATIONS,
    SUBSTITUTE_RELATIONS,
    PartSubstitute,
)

#: Upper bound on `evidence`. It is a reference ("SIM-012",
#: "TEST-4 report rev B"), not a report body.
EVIDENCE_MAX_LENGTH = 500

#: Fields the evaluation writer owns, in audit-comment order.
EVALUATION_FIELDS = ("relation", "evaluation", "evidence")


class _Unset:
    """Marker for "argument not given" — distinct from `None` / ""."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "UNSET"


UNSET: Any = _Unset()


def find_link(
    db: Session,
    *,
    workspace_id: UUID,
    part_id: UUID,
    substitute_part_id: UUID,
    allow_reverse: bool = False,
) -> PartSubstitute:
    """The link `part_id → substitute_part_id` in this workspace.

    With `allow_reverse`, a BIDIRECTIONAL link stored the other way round
    (`substitute_part_id → part_id`) also matches — it is the same link,
    and the shortage analysis already treats it as one. A one-way link is
    never matched backwards. A foreign or absent link is
    `part.substitute_not_found` (404), never forbidden.
    """
    forward = db.execute(
        select(PartSubstitute)
        .where(PartSubstitute.workspace_id == workspace_id)
        .where(PartSubstitute.part_id == part_id)
        .where(PartSubstitute.substitute_part_id == substitute_part_id)
    ).scalars().first()
    if forward is not None:
        return forward
    if allow_reverse:
        reverse = db.execute(
            select(PartSubstitute)
            .where(PartSubstitute.workspace_id == workspace_id)
            .where(PartSubstitute.part_id == substitute_part_id)
            .where(PartSubstitute.substitute_part_id == part_id)
            .where(PartSubstitute.direction == "bidirectional")
        ).scalars().first()
        if reverse is not None:
            return reverse
    raise_http(
        status.HTTP_404_NOT_FOUND,
        ErrorCodes.PART_SUBSTITUTE_NOT_FOUND,
        "no substitute link between these parts in this workspace",
    )


def links_for_part(
    db: Session, *, workspace_id: UUID, part_id: UUID, include_reverse: bool
) -> list[tuple[PartSubstitute, UUID]]:
    """`(link, other_part_id)` for every link usable from `part_id`.

    `include_reverse` adds bidirectional links stored with `part_id` on
    the substitute side — the same set `builds.service` counts.
    """
    cond = PartSubstitute.part_id == part_id
    if include_reverse:
        cond = cond | (
            (PartSubstitute.substitute_part_id == part_id)
            & (PartSubstitute.direction == "bidirectional")
        )
    rows = db.execute(
        select(PartSubstitute)
        .where(PartSubstitute.workspace_id == workspace_id)
        .where(cond)
    ).scalars().all()
    out: list[tuple[PartSubstitute, UUID]] = []
    for row in rows:
        other = row.substitute_part_id if row.part_id == part_id else row.part_id
        if other != part_id:
            out.append((row, other))
    return out


def link_payload(row: PartSubstitute, *, other_part_id: UUID) -> dict[str, Any]:
    """Wire shape of one link, seen from the part whose list it is in."""
    return {
        "part_id": str(other_part_id),
        "direction": row.direction,
        "relation": row.relation,
        "evaluation": row.evaluation or "not-evaluated",
        "evidence": row.evidence,
        "evaluated_by": str(row.evaluated_by) if row.evaluated_by else None,
        # Normalised to UTC: a freshly stamped value is UTC, one read back
        # from Postgres carries the session time zone.
        "evaluated_at": (
            row.evaluated_at.astimezone(timezone.utc).isoformat() if row.evaluated_at else None
        ),
    }


def invalid(message: str) -> None:
    """Refuse with `part.substitute_invalid` (422)."""
    raise_http(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        ErrorCodes.PART_SUBSTITUTE_INVALID,
        message,
    )


def _normalise(field: str, value: Any) -> str | None:
    """Validate one incoming value; "" / None clears (except evaluation)."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if field == "evaluation":
            invalid("evaluation cannot be cleared; set it to 'not-evaluated'")
        return None
    if not isinstance(value, str):
        invalid(f"{field} must be a string")
    text = value.strip()
    if field == "relation" and text not in SUBSTITUTE_RELATIONS:
        invalid(f"relation must be one of {', '.join(SUBSTITUTE_RELATIONS)}")
    if field == "evaluation" and text not in SUBSTITUTE_EVALUATIONS:
        invalid(f"evaluation must be one of {', '.join(SUBSTITUTE_EVALUATIONS)}")
    if field == "evidence" and len(text) > EVIDENCE_MAX_LENGTH:
        invalid(f"evidence is limited to {EVIDENCE_MAX_LENGTH} characters")
    return text


def set_evaluation(
    row: PartSubstitute,
    *,
    user_id: UUID | None,
    relation: Any = UNSET,
    evaluation: Any = UNSET,
    evidence: Any = UNSET,
) -> list[str]:
    """Apply the given fields to `row`; return the names that CHANGED.

    Merge semantics: an argument left `UNSET` is not touched. `None` or
    "" clears `relation` / `evidence`; `evaluation` is never cleared.
    Every value is validated before anything is written, so a bad call
    writes nothing. When something changed, `evaluated_by` /
    `evaluated_at` are stamped; a no-op call stamps nothing.
    """
    given = {
        "relation": relation,
        "evaluation": evaluation,
        "evidence": evidence,
    }
    incoming = {
        field: _normalise(field, value)
        for field, value in given.items()
        if value is not UNSET
    }
    changed = [
        field
        for field in EVALUATION_FIELDS
        if field in incoming and getattr(row, field) != incoming[field]
    ]
    for field in changed:
        setattr(row, field, incoming[field])
    if changed:
        row.evaluated_by = user_id
        row.evaluated_at = utcnow()
    return changed


def audit_comment(row: PartSubstitute, changed: list[str]) -> str:
    """Field names plus the new enumerated state — never the evidence text.

    `relation` and `evaluation` are closed vocabularies and safe to log;
    `evidence` is free text, so only its name appears.
    """
    parts = [f"fields={','.join(changed)}"]
    if "relation" in changed:
        parts.append(f"relation={row.relation or 'unset'}")
    parts.append(f"evaluation={row.evaluation}")
    return ";".join(parts)
