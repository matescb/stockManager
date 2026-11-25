"""MCP tools for substitute links and their evaluation state (SM-012).

`list_substitutes` reads every link usable from a part — the same set
`bom_shortages` counts, including bidirectional links stored from the
other side. `set_substitute_evaluation` writes relation / evaluation /
evidence on one existing link; it never creates or removes a link.

Validation, the by/at stamp and the audit comment live in
`domain/parts/services/substitutes.py`, shared with the REST routes.
"""
from __future__ import annotations

from typing import Any, Literal

from app.domain.parts.services import substitutes as subs
from app.mcp.principal import Caller
from app.mcp.tools._registry import tool
from app.mcp.tools._shared import audit, part_url, resolve_part, sid

_RATE = "60/minute"  # same ceiling as the other per-part metadata writes


@tool()
def list_substitutes(caller: Caller, part_id_or_mpn: str) -> dict[str, Any]:
    """The registered substitutes for a part, with relation and evaluation.

    Args:
        part_id_or_mpn: The part's id or exact MPN (archived parts too).

    Returns `substitutes`, one per link usable in place of this part
    (links this part owns, plus bidirectional links registered from the
    other side): `part_id`, `mpn`, `name`, `part_url`, `direction`
    (`one_way` | `bidirectional`), `relation` (`pin-compatible` |
    `footprint-compatible` | `functional`, absent when not stated),
    `evaluation` (`not-evaluated` | `evaluated-ok` | `rejected`),
    `evidence`, `evaluated_by` and `evaluated_at`. Only evaluated-ok
    substitutes count in bom_shortages' `short_by_evaluated_only`.
    """
    part = resolve_part(caller, part_id_or_mpn, include_archived=True)
    links = subs.links_for_part(
        caller.db, workspace_id=caller.ws.id, part_id=part.id, include_reverse=True
    )
    out = []
    for row, other_id in links:
        other = resolve_part(caller, str(other_id), include_archived=True)
        payload = subs.link_payload(row, other_part_id=other_id)
        payload.update(
            mpn=other.mpn,
            name=other.name,
            part_url=part_url(other_id),
            archived=other.archived_at is not None or None,
        )
        out.append({k: v for k, v in payload.items() if v is not None})
    return {
        "part_id": sid(part.id),
        "part_url": part_url(part.id),
        "substitutes": out,
    }


@tool(writes=True, rate=_RATE)
def set_substitute_evaluation(
    caller: Caller,
    part_id_or_mpn: str,
    substitute_id_or_mpn: str,
    relation: Literal["pin-compatible", "footprint-compatible", "functional", ""]
    | None = None,
    evaluation: Literal["not-evaluated", "evaluated-ok", "rejected"] | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Record how a substitute relates to a part and whether it was evaluated.

    The link must already exist (list_substitutes shows them; links are
    created in the app). A substitute is not interchangeable until its
    evaluation is `evaluated-ok`, and only those count in bom_shortages'
    `short_by_evaluated_only`. Only the arguments you pass change; pass
    "" to clear relation or evidence. Any change stamps evaluated_by (the
    token's owner) and evaluated_at.

    Args:
        part_id_or_mpn: The part the substitute stands in for (id or exact MPN).
        substitute_id_or_mpn: The substitute part (id or exact MPN). A
            bidirectional link matches in either order; a one-way link
            only as part → substitute.
        relation: `pin-compatible`, `footprint-compatible` or `functional`.
        evaluation: `not-evaluated`, `evaluated-ok` or `rejected`.
        evidence: The reference that justified the verdict, e.g. a
            simulation or test id such as "SIM-012" (max 500 characters).

    Refusals (nothing is written): part.not_found,
    part.substitute_not_found (no such link in this workspace),
    part.substitute_invalid (bad value, nothing to set),
    auth.token_read_only.

    Returns `substitute` in list_substitutes' shape and `updated` (the
    fields that changed; empty when the values were already set).
    """
    part = resolve_part(caller, part_id_or_mpn, include_archived=True)
    other = resolve_part(caller, substitute_id_or_mpn, include_archived=True)
    given = {
        "relation": relation,
        "evaluation": evaluation,
        "evidence": evidence,
    }
    provided = {k: v for k, v in given.items() if v is not None}
    if not provided:
        subs.invalid("nothing to set: pass relation, evaluation or evidence")
    row = subs.find_link(
        caller.db,
        workspace_id=caller.ws.id,
        part_id=part.id,
        substitute_part_id=other.id,
        allow_reverse=True,
    )
    changed = subs.set_evaluation(row, user_id=caller.user.id, **provided)
    if changed:
        caller.db.flush()
        audit(
            caller,
            action="part.substitute_evaluated",
            target_type="part_substitute",
            target_id=row.part_id,
            target_ids=[row.part_id, row.substitute_part_id],
            comment=subs.audit_comment(row, changed),
        )
    payload = subs.link_payload(row, other_part_id=other.id)
    payload.update(mpn=other.mpn, name=other.name, part_url=part_url(other.id))
    return {
        "part_id": sid(part.id),
        "part_url": part_url(part.id),
        "substitute": {k: v for k, v in payload.items() if v is not None},
        "updated": changed,
    }
