"""The distributor price/stock lookup — a read that is declared a write.

`sourcing_offers` answers a read-shaped question ("what does this part
cost?") and every instinct says `writes=False`. It shipped that way, and
that was the bug: the call reaches out to TrustedParts over the network,
spends a slice of the workspace's metered provider budget
(`domain/sourcing/service.py` raises `SourcingBudgetBlocked` when it is
gone), and writes the answer into `sourcing_cache`. None of that is
something a `read_only` token — the credential pasted into a KiCad
config file on a workstation — should be able to make happen, and the
REST twin agrees: `POST /api/sourcing/search` sits behind
`require_role("member")`.

So the flag follows the COST, not the shape of the answer. Anything that
spends money, burns a quota, or leaves a row behind is a write here.

It lives in its own module because `read.py` promises read-only tools
and this one is not; leaving it there with a `writes=True` would make
that promise a lie a reader has to check.
"""
from __future__ import annotations

from typing import Any

from app.domain.sourcing import service as sourcing_service
from app.domain.sourcing.client import (
    SourcingAuthError,
    SourcingClientError,
    SourcingRateLimitError,
    SourcingTimeoutError,
)
from app.domain.sourcing.service import SourcingBudgetBlocked, SourcingNotConfigured
from app.mcp.principal import Caller
from app.mcp.tools._registry import tool
from app.mcp.tools._shared import compact, resolve_part, sid

# Matches `POST /api/sourcing/search`'s own ceiling. The budget gate
# inside the service is the real protection for the paid quota; this
# caps the request rate in front of it.
_SOURCING_RATE = "60/minute"


@tool(writes=True, rate=_SOURCING_RATE)
def sourcing_offers(caller: Caller, part_id: str, qty: int = 1) -> dict[str, Any]:
    """Distributor offers (price and availability) for one part.

    Args:
        part_id: The part's id or exact MPN. The part must have an MPN
            — offers are looked up by manufacturer part number.
        qty: The quantity you intend to buy, used to pick the price
            break to report.

    Returns `offers` with distributor, stock, currency and unit price,
    served from the workspace's cached sourcing data where it is fresh.
    When sourcing is not set up for this workspace, or the provider is
    unreachable, this returns `status` explaining why and an empty
    offer list rather than failing — treat that as "unknown", not as
    "unavailable".

    Needs a full-access token and `member` role even though it only
    reads: a lookup that misses the cache costs a call against the
    workspace's paid distributor quota.
    """
    part = resolve_part(caller, part_id)
    if not part.mpn:
        return {
            "part_id": sid(part.id),
            "status": "no_mpn",
            "message": "this part has no MPN, so distributor offers cannot be looked up",
            "offers": [],
        }

    # Every failure below is DEGRADATION, not an error: an agent asking
    # "what does this cost" when sourcing was never configured should be
    # told that, not handed a tool error it will try to route around.
    # Same taxonomy as `domain/reports/service.py::bom_buyability_report`.
    try:
        result = sourcing_service.search(
            caller.db,
            workspace=caller.ws,
            mpns=[part.mpn],
            requested_by=caller.user.id,
        )
    except SourcingNotConfigured:
        return _degraded(part, "not_configured", "sourcing is not configured for this workspace")
    except SourcingBudgetBlocked as exc:
        return _degraded(part, "budget_blocked", str(exc))
    except (
        SourcingAuthError,
        SourcingRateLimitError,
        SourcingTimeoutError,
        SourcingClientError,
    ) as exc:
        return _degraded(part, "provider_error", type(exc).__name__)

    # `results` is per-MPN and we asked for one; each result carries
    # `offers` per manufacturer part, and the buyable rows hang off
    # `offer.distributors`. Flattened to one row per distributor,
    # because "who has it and what does it cost" is a single question.
    offers: list[dict[str, Any]] = []
    for row in result.results:
        for offer in row.offers:
            for dist in offer.distributors:
                offers.append(
                    compact(
                        {
                            "distributor": dist.name,
                            "sku": dist.sku,
                            "stock": dist.stock,
                            "moq": dist.moq,
                            "lead_time_days": dist.lead_time_days,
                            "currency": dist.currency,
                            "unit_price": _price_for_qty(dist, qty),
                            "url": dist.product_url,
                        }
                    )
                )
    return {
        "part_id": sid(part.id),
        "mpn": part.mpn,
        "qty": qty,
        "status": "ok",
        "cache_hit": result.cache_hit,
        "offers": offers,
    }


def _degraded(part, status_: str, message: str) -> dict[str, Any]:
    return {
        "part_id": sid(part.id),
        "mpn": part.mpn,
        "status": status_,
        "message": message,
        "offers": [],
    }


def _price_for_qty(dist: Any, qty: int) -> float | None:
    """The unit price at the break that applies to `qty`.

    Price breaks ascend by quantity, so the applicable one is the last
    whose minimum is still within reach. Falls back to the distributor's
    headline unit price when it publishes no breaks. Returned as a float
    rather than the wire `Decimal`, because the result is serialised to
    JSON for a model to read and a quoted decimal string reads as a
    different type to it.
    """
    best: float | None = None
    for row in dist.price_breaks or []:
        if row.quantity is not None and row.unit_price is not None and row.quantity <= qty:
            best = float(row.unit_price)
    if best is not None:
        return best
    return None if dist.unit_price is None else float(dist.unit_price)
