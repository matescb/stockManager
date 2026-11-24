"""DigiKey Product Information v4 KeywordSearch, parametric (SM-002).

`digikey.py` uses the keyword endpoint only as the one-hit fallback of an
MPN lookup. This module sends the full `KeywordRequest` — category,
manufacturer, status and parametric filters, search options, sort, limit
and offset — and turns the answer into compact candidates plus the
response's `FilterOptions` as facets an agent can re-query with.

Field names follow DigiKey's v4 schema as already mapped in `digikey.py`
(`ManufacturerProductNumber`, `Manufacturer.Name`, `ProductStatus.Status`,
`Parameters[].ParameterText/ValueText`, `ProductVariations[].StandardPricing`)
plus the request/response members of KeywordRequest / KeywordResponse:
`FilterOptionsRequest.{CategoryFilter, ManufacturerFilter, StatusFilter,
ParameterFilterRequest, SearchOptions}`, `SortOptions.{Field, SortOrder}`,
`ProductsCount` and `FilterOptions.{TopCategories, Manufacturers, Status,
ParametricFilters}`. Nothing here is persisted; `catalog_search` decides
what may be cached.

Facets are sparse, and that is DigiKey, not this parser (SM-002 follow-up).
The field names above match the published v4 schema AND every live
KeywordResponse we could find; in those, `Manufacturers` is always filled
but `TopCategories`, `Status`, `Packaging`, `Series` and `ParametricFilters`
are `null` unless DigiKey judges the result set narrow (in practice: one
leaf category). So when `TopCategories` is missing the categories facet is
built from the candidates' own `Category` (real ids, page-local counts,
`categories_from: "candidates"`), and `hints` tells the agent to narrow by
category to unlock the parametric filters. Sources: the fixture README.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.domain.parts.providers.base import ProviderUpstreamError
from app.domain.parts.providers.digikey import (
    _API_BASE,
    _LOCALE_CURR,
    _TIMEOUT_SEC,
    DigiKeyProvider,
    _deepest_category_name,
    _digikey_headers,
    _mark_retry_after,
    _refusal,
)
from app.domain.parts.services.provider_quota import QUOTA, account_key
from app.domain.sourcing.providers import make_retrying_client

__all__ = ["SearchQuery", "categories", "search"]

_KEYWORD_SEARCH_PATH = "/products/v4/search/keyword"
_CATEGORIES_PATH = "/products/v4/search/categories"

#: Caps on what is returned, not on what is asked — the MCP rule is that a
#: wall of results makes an assistant worse at the task.
MAX_KEY_PARAMETERS = 8
MAX_FACET_MANUFACTURERS = 20
MAX_FACET_CATEGORIES = 10
MAX_PARAMETRIC_FILTERS = 20
MAX_FILTER_VALUES = 12

#: A capacitance / inductance written with an SI prefix ("100nF", "4.7 uH").
#: DigiKey lists values as "0.1µF" / "100pF", so its keyword matcher finds
#: nothing for "100nF"; we hint rather than rewrite the query.
_PREFIXED_VALUE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*[pnuµμ][FH]\b")

HINT_NARROW_FOR_PARAMETRICS = (
    "DigiKey returns parametric_filters and status only once a search is narrow "
    "(in practice one leaf category): re-query with category_id from "
    "facets.categories (or list_categories), then take parameter ids from that answer."
)
HINT_LEAF_CATEGORY = (
    "No parametric_filters came back for this category_id; if it is a parent "
    "category, use one of its leaf children (list_categories)."
)
HINT_VALUE_KEYWORDS = (
    "No match. DigiKey's keyword search matches values as its listings write them "
    "(0.1µF, 100pF, 10µH), so a value like 100nF in keywords finds nothing: drop the "
    "value, search by type/package, then narrow with category_id and parameter_filters."
)

#: Our sort names -> DigiKey `SortOptions.Field`.
SORT_FIELDS: dict[str, str] = {
    "price": "Price",
    "stock": "QuantityAvailable",
    "mpn": "ManufacturerProductNumber",
    "manufacturer": "Manufacturer",
    "status": "ProductStatus",
    "min_qty": "MinimumQuantity",
}


@dataclass(frozen=True)
class SearchQuery:
    keywords: str
    category_id: str | None = None
    manufacturer_ids: tuple[str, ...] = ()
    status_ids: tuple[str, ...] = ()
    parameter_filters: tuple[tuple[int, tuple[str, ...]], ...] = ()
    in_stock: bool = False
    rohs: bool = False
    has_datasheet: bool = False
    sort: str | None = None  # "price" ascending, "-price" descending
    limit: int = 20
    offset: int = 0


def build_payload(q: SearchQuery) -> dict[str, Any]:
    """The KeywordRequest body for *q* (also what `applied_filters` echoes)."""
    options: dict[str, Any] = {}
    if q.category_id:
        options["CategoryFilter"] = [{"Id": q.category_id}]
    if q.manufacturer_ids:
        options["ManufacturerFilter"] = [{"Id": i} for i in q.manufacturer_ids]
    if q.status_ids:
        options["StatusFilter"] = [{"Id": i} for i in q.status_ids]
    if q.parameter_filters:
        options["ParameterFilterRequest"] = {
            "CategoryFilter": {"Id": q.category_id},
            "ParameterFilters": [
                {"ParameterId": pid, "FilterValues": [{"Id": v} for v in values]}
                for pid, values in q.parameter_filters
            ],
        }
    search_options = [
        name for flag, name in (
            (q.in_stock, "InStock"),
            (q.rohs, "RohsCompliant"),
            (q.has_datasheet, "HasDatasheet"),
        ) if flag
    ]
    if search_options:
        options["SearchOptions"] = search_options
    payload: dict[str, Any] = {"Keywords": q.keywords, "Limit": q.limit, "Offset": q.offset}
    if options:
        payload["FilterOptionsRequest"] = options
    if q.sort:
        name = q.sort.lstrip("-")
        payload["SortOptions"] = {
            "Field": SORT_FIELDS[name],
            "SortOrder": "Descending" if q.sort.startswith("-") else "Ascending",
        }
    return payload


# ---------------------------------------------------------------------------
# network seams — tests monkeypatch these
# ---------------------------------------------------------------------------
def _post_search(token: str, client_id: str, payload: dict[str, Any]) -> tuple[int, Any]:
    url = f"{_API_BASE}{_KEYWORD_SEARCH_PATH}"
    headers = {**_digikey_headers(token, client_id), "Content-Type": "application/json"}
    with make_retrying_client(provider_name="digikey", timeout=_TIMEOUT_SEC) as client:
        resp = client.post(url, headers=headers, json=payload)
    QUOTA.note_remaining("digikey", account_key(client_id), resp.headers)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, _mark_retry_after(resp, body)


def _get_categories(token: str, client_id: str) -> tuple[int, Any]:
    url = f"{_API_BASE}{_CATEGORIES_PATH}"
    with make_retrying_client(provider_name="digikey", timeout=_TIMEOUT_SEC) as client:
        resp = client.get(url, headers=_digikey_headers(token, client_id))
    QUOTA.note_remaining("digikey", account_key(client_id), resp.headers)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, _mark_retry_after(resp, body)


# ---------------------------------------------------------------------------
# calls
# ---------------------------------------------------------------------------
def search(provider: DigiKeyProvider, q: SearchQuery) -> dict[str, Any]:
    """One KeywordSearch. `found: True` on a 200, even with zero products."""
    payload = build_payload(q)
    status, body = _call(provider, lambda tok: _post_search(tok, provider.client_id, payload))
    if status != 200:
        return _not_ok(status, body)
    body = body if isinstance(body, dict) else {}
    products = [p for p in body.get("Products") or [] if isinstance(p, dict)][: q.limit]
    total = _int(body.get("ProductsCount"), default=len(products))
    options = body.get("FilterOptions")
    facets = _facets(options if isinstance(options, dict) else {}, products)
    out: dict[str, Any] = {
        "found": True,
        "total": total,
        "candidates": [_candidate(p) for p in products],
        "facets": facets,
        "applied_filters": payload,
    }
    hints = _hints(q, total, facets)
    if hints:
        out["hints"] = hints
    return out


def _hints(q: SearchQuery, total: int | None, facets: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if not total and _PREFIXED_VALUE.search(q.keywords or ""):
        hints.append(HINT_VALUE_KEYWORDS)
    if total and not facets["parametric_filters"]:
        hints.append(HINT_LEAF_CATEGORY if q.category_id else HINT_NARROW_FOR_PARAMETRICS)
    return hints


def categories(provider: DigiKeyProvider) -> dict[str, Any]:
    """DigiKey's category tree, two levels deep, compact."""
    status, body = _call(provider, lambda tok: _get_categories(tok, provider.client_id))
    if status != 200:
        return _not_ok(status, body)
    rows = (body or {}).get("Categories") if isinstance(body, dict) else None
    return {"found": True, "categories": [_category_node(c, depth=1) for c in rows or []
                                           if isinstance(c, dict)]}


def _call(
    provider: DigiKeyProvider, fn: Callable[[str], tuple[int, Any]]
) -> tuple[int, Any]:
    """Run a seam with the token dance; transport failures become
    `ProviderUpstreamError` (fixed text, never the URL) so the breaker
    counts them, as `lookup_mpn` does."""
    try:
        status, body = provider._request_with_retry(fn)
    except ProviderUpstreamError:
        raise
    except httpx.HTTPStatusError as exc:
        raise ProviderUpstreamError(
            "digikey", f"DigiKey upstream returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.TransportError as exc:
        raise ProviderUpstreamError(
            "digikey", f"DigiKey upstream unavailable ({type(exc).__name__})"
        ) from exc
    if status >= 500:
        raise ProviderUpstreamError("digikey", f"DigiKey upstream returned HTTP {status}")
    return status, body


def _not_ok(status: int, body: Any) -> dict[str, Any]:
    refused = _refusal(status, body)
    if refused is not None:
        return dict(refused)
    return {"found": False, "result": None, "message": f"DigiKey returned HTTP {status}"}


# ---------------------------------------------------------------------------
# shaping
# ---------------------------------------------------------------------------
def _candidate(p: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, str] = {}
    for row in p.get("Parameters") or []:
        if not isinstance(row, dict) or len(params) >= MAX_KEY_PARAMETERS:
            continue
        name = (row.get("ParameterText") or "").strip()
        value = (row.get("ValueText") or "").strip()
        if name and value and value != "-":
            params.setdefault(name, value)
    return {
        "mpn": (p.get("ManufacturerProductNumber") or "").strip() or None,
        "manufacturer": ((p.get("Manufacturer") or {}).get("Name") or "").strip() or None,
        "description": ((p.get("Description") or {}).get("ProductDescription") or "").strip()
        or None,
        "category": _deepest_category_name(p.get("Category")),
        "status": ((p.get("ProductStatus") or {}).get("Status") or "").strip() or None,
        "key_parameters": params,
        "stock": _int(p.get("QuantityAvailable")),
        "unit_price_qty1": _unit_price(p),
        "datasheet_url": (p.get("DatasheetUrl") or "").strip() or None,
        "product_url": (p.get("ProductUrl") or "").strip() or None,
    }


def _unit_price(p: dict[str, Any]) -> str | None:
    """Price at quantity 1: the qty-1 break of any variation, else UnitPrice."""
    for variation in p.get("ProductVariations") or []:
        for tier in (variation or {}).get("StandardPricing") or []:
            if isinstance(tier, dict) and _int(tier.get("BreakQuantity")) == 1:
                return _money(tier.get("UnitPrice"))
    return _money(p.get("UnitPrice"))


def _money(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return f"{number:.4f} {_LOCALE_CURR}" if number > 0 else None


def _facets(options: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
    categories = _top_categories(options.get("TopCategories"))
    source = "top_categories"
    if not categories:
        categories, source = _candidate_categories(products), "candidates"
    return {
        "categories": categories,
        "categories_from": source,
        "manufacturers": _base_filters(options.get("Manufacturers"), MAX_FACET_MANUFACTURERS),
        "status": _base_filters(options.get("Status"), None),
        "parametric_filters": _parametric(options.get("ParametricFilters")),
    }


def _candidate_categories(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Leaf categories of the candidates on this page, most frequent first.

    The fallback for a response without `TopCategories` (most broad
    searches): ids are DigiKey's own, `count` is candidates on this page."""
    found: dict[str, dict[str, Any]] = {}
    for p in products:
        leaf, root = _leaf_category(p.get("Category"))
        if leaf is None:
            continue
        entry = found.setdefault(leaf["id"], {**leaf, "count": 0, "root": root})
        entry["count"] += 1
    ordered = sorted(found.values(), key=lambda r: -r["count"])
    return ordered[:MAX_FACET_CATEGORIES]


def _leaf_category(cat: Any) -> tuple[dict[str, Any] | None, str | None]:
    """(deepest {id, name}, root name) of a product's nested `Category`."""
    root = cat.get("Name") if isinstance(cat, dict) else None
    leaf = None
    while isinstance(cat, dict):
        if cat.get("CategoryId") is not None:
            leaf = {"id": str(cat["CategoryId"]), "name": cat.get("Name")}
        children = [c for c in cat.get("ChildCategories") or [] if isinstance(c, dict)]
        cat = children[0] if children else None
    return leaf, root


def _top_categories(rows: Any) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cat = row.get("Category") or {}
        if not isinstance(cat, dict) or cat.get("Id") is None:
            continue
        root = (row.get("RootCategory") or {}).get("Name")
        out.append({"id": str(cat["Id"]), "name": cat.get("Name"),
                    "count": _int(cat.get("ProductCount")), "root": root})
    return out[:MAX_FACET_CATEGORIES]


def _base_filters(rows: Any, cap: int | None) -> list[dict[str, Any]]:
    out = [
        {"id": str(r["Id"]), "name": r.get("Value"), "count": _int(r.get("ProductCount"))}
        for r in rows or [] if isinstance(r, dict) and r.get("Id") is not None
    ]
    out.sort(key=lambda r: -(r["count"] or 0))
    return out if cap is None else out[:cap]


def _parametric(rows: Any) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("ParameterId") is None:
            continue
        values = [
            {"id": str(v["ValueId"]), "name": v.get("ValueName"),
             "count": _int(v.get("ProductCount"))}
            for v in row.get("FilterValues") or []
            if isinstance(v, dict) and v.get("ValueId") is not None
        ]
        entry = {"parameter_id": _int(row["ParameterId"]), "name": row.get("ParameterName"),
                 "values": values[:MAX_FILTER_VALUES]}
        if len(values) > MAX_FILTER_VALUES:
            entry["values_truncated"] = True
        out.append(entry)
    return out[:MAX_PARAMETRIC_FILTERS]


def _category_node(c: dict[str, Any], *, depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"id": str(c.get("CategoryId")), "name": c.get("Name"),
                            "count": _int(c.get("ProductCount"))}
    children = [x for x in c.get("ChildCategories") or [] if isinstance(x, dict)]
    if children and depth > 0:
        node["children"] = [_category_node(x, depth=depth - 1) for x in children]
    return node


def _int(value: Any, *, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
