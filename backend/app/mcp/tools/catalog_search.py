"""`catalog_search` — distributor keyword / parametric search (SM-002).

Declared a write for the same reason as `catalog_lookup` (quota follows
cost): every search that misses the short in-process cache spends a call
from the owner's daily DigiKey / Mouser allowance. It writes nothing.
Rules live in `domain/parts/services/catalog_search.py`.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from app.domain.parts.services import catalog_search as service
from app.domain.parts.services.catalog_lookup import CatalogLookupRefused
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool

# Same ceiling as catalog_lookup; pacing and the reserve are the real guard.
_SEARCH_RATE = "60/minute"

Sort = Literal[
    "price", "-price", "stock", "-stock", "mpn", "-mpn",
    "manufacturer", "-manufacturer", "status", "-status", "min_qty", "-min_qty",
]


class ParameterFilter(TypedDict):
    """One DigiKey parametric filter: ids from `facets.parametric_filters`."""

    parameter_id: int
    value_ids: list[str]


@tool(writes=True, rate=_SEARCH_RATE)
def catalog_search(
    caller: Caller,
    keywords: str = "",
    category_id: str | None = None,
    manufacturer_ids: list[str] | None = None,
    parameter_filters: list[ParameterFilter] | None = None,
    status: str | None = None,
    in_stock: bool = False,
    rohs: bool = False,
    has_datasheet: bool = False,
    sort: Sort | None = None,
    limit: int = 20,
    offset: int = 0,
    provider: Literal["digikey", "mouser", "all"] = "all",
    list_categories: bool = False,
) -> dict[str, Any]:
    """Search the distributor catalogues (DigiKey, Mouser) for candidate parts.

    For part research ("0603 C0G 100nF 50V, active, in stock"). Nothing is
    written. Price and stock here are indicative: DigiKey's keyword-search
    data may be up to 24 h old, so call catalog_lookup for real-time price
    and availability before ordering. Narrow in steps: search by keywords, then re-query with ids
    taken from the returned `facets` — never invent ids.

    DigiKey fills facets sparsely: a broad search returns manufacturers
    and categories but usually NO status or parametric_filters. Those
    come back once the search is narrow — re-query with a leaf
    category_id from `facets.categories`, then take parameter ids and
    value ids from THAT answer. Put the part type and package in
    keywords ("ceramic capacitor 0603 C0G"), not the value: DigiKey
    writes capacitance as 0.1µF / 100pF, so "100nF" matches nothing;
    select the value with parameter_filters instead. A package or size in
    keywords ("0603", "SOT-23") does NOT restrict the package at DigiKey —
    narrow it with the "Package / Case" parametric filter once a leaf
    category is chosen. `hints` says when any of this applies.

    Args:
        keywords: Free text, e.g. "100nF 0603 C0G".
        category_id: A DigiKey leaf category id from `facets.categories`
            (or from `categories` when list_categories is true).
        manufacturer_ids: DigiKey manufacturer ids from `facets.manufacturers`.
        parameter_filters: [{parameter_id, value_ids}] from
            `facets.parametric_filters`; needs category_id. DigiKey only.
        status: A DigiKey status id from `facets.status` (e.g. Active).
        in_stock / rohs: Both providers. has_datasheet: DigiKey only.
        sort: "price", "stock", "mpn", "manufacturer", "status", "min_qty";
            prefix "-" for descending. DigiKey only. Omitted, DigiKey is
            asked for "-stock" (most in stock first): its own default order
            is not relevance. `applied_sort` says what each provider used.
        limit: Candidates per provider, default 20, at most 50.
        offset: Skip this many results — page with offset += limit.
        provider: "digikey", "mouser", or "all" (default).
        list_categories: Also return DigiKey's category tree (two levels)
            as `categories`; keywords may then be empty.

    Mouser does keyword search only: category, manufacturer, parametric,
    status, sort, has_datasheet and list_categories are refused for
    provider "mouser" with `provider.unsupported_filter` (with "all",
    Mouser is listed under `errors` and DigiKey still answers).

    Returns `candidates` (each with `source`, the provider's own
    `product_url`, `retrieved_at`, `mpn`, `manufacturer`, `description`,
    `status`, `key_parameters`, `stock`, `unit_price_qty1`,
    `datasheet_url`; DigiKey candidates also carry
    `price_stock_freshness: "indicative_up_to_24h"`) — per provider, never merged — plus `total` per
    provider, DigiKey's `facets` (categories, manufacturers, status,
    parametric_filters with ids, names and counts; `categories_from` is
    "candidates" when DigiKey sent no category facet and the list was
    built from this page's candidates), `hints`, `applied_filters`,
    `applied_sort` per provider ({sort, field, order, source:
    "caller"|"default"}; Mouser {sort: null, source: "provider"}),
    `cache`, and `quota` as in catalog_lookup. Same refusals as
    catalog_lookup (provider.quota_reserve_reached, provider.suspended,
    provider.pacing with retry_after_ms), plus provider.invalid_filter.
    A DigiKey 4xx carries DigiKey's own text as `provider_message` (and a
    `hint`); "Parametric Filtering Not Support at Parent Category Level"
    means use a leaf category_id.
    """
    try:
        return service.catalog_search(
            caller.db, caller.ws,
            keywords=keywords, category_id=category_id,
            manufacturer_ids=manufacturer_ids,
            parameter_filters=[dict(f) for f in parameter_filters or []],
            status=status, in_stock=in_stock, rohs=rohs, has_datasheet=has_datasheet,
            sort=sort, limit=limit, offset=offset, provider=provider,
            list_categories=list_categories,
        )
    except CatalogLookupRefused as exc:
        raise ToolError(f"{exc.code}: {exc.message}") from None
