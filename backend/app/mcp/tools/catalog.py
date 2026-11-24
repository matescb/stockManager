"""Distributor catalogue tools — reads that are declared writes (SM-001).

`catalog_lookup` asks DigiKey and/or Mouser about a manufacturer part
number. Like `sourcing_offers` it answers a read-shaped question and is
declared `writes=True` anyway: a lookup that misses the cache spends a
call from the workspace's metered daily allowance, which the owner's UI,
scan import and `provider-refresh` job share. The flag follows the COST
(`docs/api/mcp.md`, "Why a price lookup counts as a write"), so a
read-only token — the one pasted into a KiCad config — cannot spend it.

The rules live in `domain/parts/services/catalog_lookup.py` (record
shape, provenance, exact vs fuzzy) and `provider_quota.py` (reserve,
pacing, the `quota` object); this module is the agent-facing contract.
"""
from __future__ import annotations

from typing import Any, Literal

from app.domain.parts.services import catalog_lookup as service
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool

# The REST twin, `POST /api/parts/lookup-mpn`, allows 60/minute per
# workspace. Pacing (one live call per provider per second) and the
# daily reserve are the real protection; this caps the burst in front.
_CATALOG_RATE = "60/minute"


@tool(writes=True, rate=_CATALOG_RATE)
def catalog_lookup(
    caller: Caller,
    mpn: str,
    manufacturer: str | None = None,
    provider: Literal["digikey", "mouser", "all"] = "all",
) -> dict[str, Any]:
    """Look up a manufacturer part number at the distributors (DigiKey, Mouser).

    Use it to confirm that an MPN read in a datasheet exists, who makes
    it, whether it is active, and what it costs — BEFORE create_part.
    Nothing is written: no part, no specification rows.

    Args:
        mpn: The manufacturer part number, as printed.
        manufacturer: Optional. When given, each record says whether its
            manufacturer matches (`manufacturer_matches`), and among
            several Mouser candidates the matching one is chosen.
        provider: "digikey", "mouser", or "all" (default) for every
            provider this workspace has configured.

    Returns `records`, ONE PER PROVIDER and never merged, each with
    `source`, the provider's own `product_url`, `retrieved_at`, `mpn`,
    `manufacturer`, `description`, `category`, `lifecycle`,
    `compliance`, `datasheet_url`, `stock`, `price_breaks` and `match`.
    `match: "exact"` means the provider's MPN is the one you asked for;
    `"fuzzy"` means it found a DIFFERENT part number (shown in `mpn`) —
    check it before using it. `candidates` lists every manufacturer when
    one MPN is made by several. `misses` lists providers that do not
    know the part; `errors` lists providers that refused, with a code.

    `cache: "hit"` means the answer came from the cache and spent no
    quota. `quota` has one entry per provider asked: `spent` (calls this
    answer cost), `remaining_day`, `reserve` and `source` ("header" from
    the provider, or "local-count"). When the remaining count is below
    the reserve the provider refuses with `provider.quota_reserve_reached`
    — the reserve is kept for the owner's own work, so do not retry
    today. `provider.suspended` means the provider has blocked calls for
    a few minutes; it is NOT "no match". `provider.pacing` means you
    called the same provider less than a second ago: wait
    `retry_after_ms` and call again. The call as a whole fails only
    when no provider could answer.
    """
    try:
        return service.catalog_lookup(
            caller.db,
            caller.ws,
            mpn=mpn,
            manufacturer=manufacturer,
            provider=provider,
        )
    except service.CatalogLookupRefused as exc:
        raise ToolError(f"{exc.code}: {exc.message}") from None
