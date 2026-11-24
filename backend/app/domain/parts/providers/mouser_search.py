"""Mouser Search API keyword search (SM-002).

Mouser offers keyword search only: up to 50 records per call, paged by
`startingRecord`, with one of `None`, `Rohs`, `InStock`, `RohsAndInStock`
as `searchOptions` and no category, manufacturer or parametric filter.
`catalog_search` refuses those filters for Mouser rather than pretending
to apply them.

The API key travels in the query string, as Mouser requires, so nothing
here logs or returns a URL; failures carry fixed text. Mouser's caching
terms have not been read (edaflow Q3), so nothing is cached past the
request that fetched it.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.domain.parts.providers.base import ProviderUpstreamError
from app.domain.parts.providers.mouser import MouserProvider, parse_description_specs
from app.domain.parts.services.provider_quota import QUOTA, account_key
from app.domain.sourcing.providers import make_retrying_client

__all__ = ["search", "search_option"]

_ENDPOINT = "https://api.mouser.com/api/v1/search/keyword"
_TIMEOUT_SEC = 8.0
MAX_RECORDS = 50
MAX_KEY_PARAMETERS = 8


def _post_mouser_keyword(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Network seam — tests monkeypatch this."""
    with make_retrying_client(provider_name="mouser", timeout=_TIMEOUT_SEC) as client:
        resp = client.post(url, json=payload, headers={"Accept": "application/json"})
        if resp.status_code >= 500:
            raise ProviderUpstreamError(
                "mouser", f"Mouser upstream returned HTTP {resp.status_code}"
            )
        resp.raise_for_status()
        return resp.json()


def search_option(*, in_stock: bool, rohs: bool) -> str:
    if in_stock and rohs:
        return "RohsAndInStock"
    if in_stock:
        return "InStock"
    if rohs:
        return "Rohs"
    return "None"


def search(
    provider: MouserProvider, *, keywords: str, limit: int, offset: int,
    in_stock: bool, rohs: bool,
) -> dict[str, Any]:
    request = {
        "keyword": keywords,
        "records": min(limit, MAX_RECORDS),
        "startingRecord": offset,
        "searchOptions": search_option(in_stock=in_stock, rohs=rohs),
        "searchWithYourSignUpLanguage": "false",
    }
    QUOTA.note_call("mouser", account_key(provider.api_key))
    try:
        data = _post_mouser_keyword(
            f"{_ENDPOINT}?apiKey={provider.api_key}", {"SearchByKeywordRequest": request}
        )
    except ProviderUpstreamError:
        raise
    except httpx.HTTPStatusError as exc:
        raise ProviderUpstreamError(
            "mouser", f"Mouser upstream returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.TransportError as exc:
        raise ProviderUpstreamError(
            "mouser", f"Mouser upstream unavailable ({type(exc).__name__})"
        ) from exc

    errors = (data or {}).get("Errors") or []
    if errors:
        message = (errors[0] or {}).get("Message") or "Mouser returned an error"
        return {"found": False, "result": None, "message": f"Mouser: {message}"}
    results = (data or {}).get("SearchResults") or {}
    parts = [p for p in results.get("Parts") or [] if isinstance(p, dict)]
    applied = {k: v for k, v in request.items() if k != "searchWithYourSignUpLanguage"}
    return {
        "found": True,
        "total": _int(results.get("NumberOfResult"), default=len(parts)),
        "candidates": [_candidate(p) for p in parts[:limit]],
        "facets": None,
        "applied_filters": applied,
    }


def _candidate(p: dict[str, Any]) -> dict[str, Any]:
    description = (p.get("Description") or "").strip() or None
    params: dict[str, str] = {}
    for key, value in parse_description_specs(description):
        params.setdefault(key, value)
    for attr in p.get("ProductAttributes") or []:
        name = ((attr or {}).get("AttributeName") or "").strip()
        value = ((attr or {}).get("AttributeValue") or "").strip()
        if name and value and len(params) < MAX_KEY_PARAMETERS:
            params.setdefault(name, value)
    return {
        "mpn": (p.get("ManufacturerPartNumber") or "").strip() or None,
        "manufacturer": (p.get("Manufacturer") or "").strip() or None,
        "description": description,
        "category": (p.get("Category") or "").strip() or None,
        "status": (p.get("LifecycleStatus") or "").strip() or None,
        "key_parameters": dict(list(params.items())[:MAX_KEY_PARAMETERS]),
        "stock": _int(str(p.get("AvailabilityInStock") or "").replace(",", "") or None),
        "unit_price_qty1": _qty1_price(p.get("PriceBreaks")),
        "datasheet_url": (p.get("DataSheetUrl") or "").strip() or None,
        "product_url": (p.get("ProductDetailUrl") or "").strip() or None,
    }


def _qty1_price(breaks: Any) -> str | None:
    for tier in breaks or []:
        if isinstance(tier, dict) and _int(tier.get("Quantity")) == 1:
            price = (tier.get("Price") or "").strip()
            currency = (tier.get("Currency") or "").strip()
            return f"{price} {currency}".strip() or None
    return None


def _int(value: Any, *, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
