"""Link a newly created part to the distributors that sell it.

Owner's rule (2026-11-28): a part he cannot buy has no place in the
library, so a part created WITH an MPN must come out of the create
linked to Mouser/DigiKey, or not be created at all. Before this, both
create doors (REST `POST /api/parts`, MCP `create_part`) wrote a bare
`local` row with no specs, prices or datasheet, and 48 such parts had
piled up on prod.

How, without a second copy of either half:

* The lookup goes through `catalog_lookup` — the agent path that reuses
  this workspace's cached answer (an earlier MCP `catalog_lookup` costs
  nothing again), honours the quota reserve and pacing (SM-005), and
  keeps a refusal apart from a miss.
* Each EXACT answer is then applied with
  `refresh_part(stored_answer_only=True)`: the one refresh
  implementation, reading the answer the lookup just stored, so the
  write itself makes no request. The primary claims the part
  (`claim_unowned`: it fills only what the creator left empty); a
  secondary adds its link and namespaced fields.

Outcomes:

* no MPN → nothing to look up; the part stays `local` (test pads);
* no provider configured in the workspace → unchanged behaviour (fresh
  installs, and workspaces that never set one up);
* no exact match anywhere, and every provider answered → 422
  `part.not_available_from_providers`, listing what each one said;
* a provider could not answer (quota, pacing, suspended, down) and none
  matched → 503 with that provider's code. "Could not ask" is never read
  as "does not exist".

The caller owns the transaction: a raise here rolls the new row back
with everything else.
"""
from __future__ import annotations

from typing import Any

from fastapi import status

from app.core.errors import ErrorCodes, raise_http
from app.domain.parts.models import Part
from app.domain.parts.services.catalog_lookup import CatalogLookupRefused, catalog_lookup
from app.domain.parts.services.provider_refresh import (
    RefreshError,
    primary_provider_name,
    refresh_part,
)

__all__ = ["link_new_part"]


def link_new_part(db, *, ws: Any, part: Part, user_id) -> list[str]:
    """Link `part` to every provider with an exact match; the names linked.

    `[]` when there is nothing to do (no MPN, no provider configured).
    Raises the 422 / 503 described in the module docstring otherwise.
    """
    mpn = (part.mpn or "").strip()
    if not mpn:
        return []
    try:
        answer = catalog_lookup(db, ws, mpn=mpn)
    except CatalogLookupRefused as exc:
        if exc.code == ErrorCodes.PART_PROVIDER_NOT_CONFIGURED:
            return []
        raise_http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            code=exc.code,
            message=f"could not check {mpn!r} with the distributors: {exc.message}",
        )

    primary = primary_provider_name(ws)
    exact = [r["source"] for r in answer.get("records", []) if r.get("match") == "exact"]
    # Primary first: it claims the part's columns and category, and the
    # category picks the spec schema the secondary's payload is read with.
    exact.sort(key=lambda name: name != primary)

    linked: list[str] = []
    for name in exact:
        try:
            outcome = refresh_part(
                db,
                ws=ws,
                part=part,
                provider_name=name,
                user_id=user_id,
                stored_answer_only=True,
                claim_unowned=name == primary,
            )
        except RefreshError:
            continue
        if outcome.found:
            linked.append(name)
    if linked:
        return linked

    errors = answer.get("errors") or []
    if errors:
        first = errors[0]
        raise_http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            code=first.get("code") or ErrorCodes.PROVIDER_UPSTREAM_ERROR,
            message=(
                f"could not check {mpn!r} with every distributor: "
                + "; ".join(f"{e['provider']}: {e['message']}" for e in errors)
            ),
            providers=errors,
        )
    said = [
        {"provider": m["provider"], "answer": m.get("message") or "no match"}
        for m in answer.get("misses") or []
    ] + [
        {"provider": r["source"], "answer": f"no exact match (closest: {r.get('mpn')})"}
        for r in answer.get("records", [])
        if r.get("match") != "exact"
    ]
    raise_http(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        code=ErrorCodes.PART_NOT_AVAILABLE_FROM_PROVIDERS,
        message=(
            f"no distributor sells {mpn!r} under that exact MPN; check the MPN "
            "or choose a part that can be bought"
        ),
        providers=said,
    )
