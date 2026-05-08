"""TrustedParts API v2 client for live sourcing lookups."""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingPriceBreak,
    SourcingQuery,
    SourcingSearchRaw,
)

TP_V2_URL = "https://api.trustedparts.com/v2/search"
TP_TIMEOUT_SECONDS = 8.0
MAX_TP_QUERIES = 50


class SourcingClientError(Exception):
    """Base error for TrustedParts sourcing client failures."""


class SourcingAuthError(SourcingClientError):
    """TrustedParts rejected the supplied credentials."""


class SourcingRateLimitError(SourcingClientError):
    """TrustedParts rate-limited the request."""


class SourcingUpstreamError(SourcingClientError):
    """TrustedParts returned a server-side error."""


class SourcingTimeoutError(SourcingClientError):
    """TrustedParts did not respond before the client timeout."""


class SourcingValidationError(SourcingClientError):
    """TrustedParts returned a response this client cannot parse."""


def _post_tp(url: str, json: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Network seam — monkeypatched in tests. Returns (status_code, body_dict)."""
    with httpx.Client(timeout=TP_TIMEOUT_SECONDS) as client:
        response = client.post(
            url,
            json=json,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
    try:
        body = response.json()
    except ValueError as exc:
        if 200 <= response.status_code <= 299:
            raise SourcingValidationError("TrustedParts returned invalid JSON") from exc
        body = {}
    if not isinstance(body, dict):
        if 200 <= response.status_code <= 299:
            raise SourcingValidationError("TrustedParts returned a non-object JSON body")
        body = {}
    return response.status_code, body


class TrustedPartsClient:
    name = "trustedparts"

    def __init__(
        self,
        company_id: str,
        api_key: str,
        country_code: str | None,
        currency_code: str | None,
        user_agent: str,
    ) -> None:
        self.company_id = company_id
        self.api_key = api_key
        self.country_code = country_code
        self.currency_code = currency_code
        self.user_agent = user_agent

    def search(
        self,
        queries: list[SourcingQuery],
        *,
        exact_match: bool = True,
        in_stock_only: bool = False,
        distributors: list[str] | None = None,
        use_cached_data: bool = False,
        is_crawler: bool = False,
    ) -> SourcingSearchRaw:
        if not queries:
            raise ValueError("TrustedParts search requires at least one query")
        if len(queries) > MAX_TP_QUERIES:
            raise ValueError("TrustedParts search accepts at most 50 queries")

        payload = self._payload(
            queries,
            exact_match=exact_match,
            in_stock_only=in_stock_only,
            distributors=distributors,
            use_cached_data=use_cached_data,
            is_crawler=is_crawler,
        )
        try:
            status, body = _post_tp(TP_V2_URL, payload)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise SourcingTimeoutError("TrustedParts request timed out") from exc

        if status in (401, 403):
            raise SourcingAuthError("TrustedParts rejected the supplied credentials")
        if status == 429:
            raise SourcingRateLimitError("TrustedParts rate limit reached")
        if 500 <= status <= 599:
            raise SourcingUpstreamError(f"TrustedParts returned HTTP {status}")
        if not 200 <= status <= 299:
            raise SourcingClientError(f"TrustedParts returned HTTP {status}")

        return _parse_search_response(body)

    def _payload(
        self,
        queries: list[SourcingQuery],
        *,
        exact_match: bool,
        in_stock_only: bool,
        distributors: list[str] | None,
        use_cached_data: bool,
        is_crawler: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "CompanyId": self.company_id,
            "ApiKey": self.api_key,
            "CountryCode": self.country_code,
            "CurrencyCode": self.currency_code,
            "UserAgent": self.user_agent,
            "InStockOnly": in_stock_only,
            "ExactMatch": True if len(queries) > 1 else exact_match,
            "IsCrawler": is_crawler,
            "UseCachedData": use_cached_data,
            "Queries": [_query_payload(query) for query in queries],
        }
        if distributors:
            payload["Distributors"] = distributors
        return payload


def _query_payload(query: SourcingQuery) -> dict[str, Any]:
    item: dict[str, Any] = {"SearchToken": query.search_token}
    if query.manufacturers:
        item["Manufacturers"] = query.manufacturers
    return item


def _parse_search_response(body: dict[str, Any]) -> SourcingSearchRaw:
    part_results = body.get("PartResults")
    if not isinstance(part_results, list):
        raise SourcingValidationError("TrustedParts response PartResults is not a list")

    offers: list[SourcingOffer] = []
    for part in part_results:
        if not isinstance(part, dict):
            raise SourcingValidationError("TrustedParts response contains an invalid part")
        offers.append(_offer_from_part(part))

    request_id = body.get("RequestId") or body.get("RequestID") or body.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        request_id = str(request_id)

    try:
        return SourcingSearchRaw.model_validate(
            {"offers": offers, "request_id": request_id}
        )
    except PydanticValidationError as exc:
        raise SourcingValidationError("TrustedParts response did not match sourcing DTOs") from exc


def _offer_from_part(part: dict[str, Any]) -> SourcingOffer:
    distributors: list[SourcingDistributor] = []
    description: str | None = None
    datasheet_url: str | None = None
    manufacturer_url: str | None = None

    for distributor in _list_of_dicts(part.get("Distributors"), "Distributors"):
        name = _str_or_none(distributor.get("Name")) or ""
        for result in _list_of_dicts(distributor.get("DistributorResults"), "DistributorResults"):
            if description is None:
                description = _str_or_none(result.get("Description"))
            links = _links_by_type(result.get("Links"))
            if datasheet_url is None:
                datasheet_url = _first_matching_link(links, "datasheet")
            if manufacturer_url is None:
                manufacturer_url = _first_matching_link(links, "manufacturer")
            distributors.append(_distributor_from_result(name, result, links))

    try:
        return SourcingOffer.model_validate(
            {
                "mpn": _str_or_none(part.get("PartNumber")) or "",
                "manufacturer": _str_or_none(part.get("Manufacturer")),
                "description": description,
                "distributors": distributors,
                "links": SourcingLinks(
                    primary=_str_or_none(part.get("ProductUrl")),
                    manufacturer=manufacturer_url,
                    datasheet=datasheet_url,
                ),
            }
        )
    except PydanticValidationError as exc:
        raise SourcingValidationError("TrustedParts part result did not match DTOs") from exc


def _distributor_from_result(
    distributor_name: str,
    result: dict[str, Any],
    links: dict[str, str],
) -> SourcingDistributor:
    pricing = result.get("Pricing")
    if pricing is None:
        pricing = {}
    if not isinstance(pricing, dict):
        raise SourcingValidationError("TrustedParts Pricing is not an object")

    stock = result.get("Stock")
    if stock is None:
        stock = {}
    if not isinstance(stock, dict):
        raise SourcingValidationError("TrustedParts Stock is not an object")

    packages = _list_of_dicts(result.get("Packaging"), "Packaging")
    price_breaks = _price_breaks(pricing.get("Prices"))
    product_url = (
        _first_matching_link(links, "distributor")
        or _first_matching_link(links, "product")
        or _first_non_matching_link(links, {"datasheet", "manufacturer"})
    )

    try:
        return SourcingDistributor.model_validate(
            {
                "name": distributor_name,
                "sku": _str_or_none(result.get("DistributorPartNumber")),
                "packaging": _first_package_type(packages),
                "moq": _moq(packages, pricing),
                "lead_time_days": None,
                "stock": _int_or_none(stock.get("QuantityOnHand")),
                "unit_price": price_breaks[0].unit_price if price_breaks else None,
                "currency": _str_or_none(pricing.get("CurrencyCode")),
                "price_breaks": price_breaks,
                "product_url": product_url,
            }
        )
    except PydanticValidationError as exc:
        raise SourcingValidationError(
            "TrustedParts distributor result did not match DTOs"
        ) from exc


def _price_breaks(raw_prices: Any) -> list[SourcingPriceBreak]:
    price_rows = _list_of_dicts(raw_prices, "Prices")
    breaks: list[SourcingPriceBreak] = []
    for row in price_rows:
        quantity = _int_or_none(row.get("Quantity"))
        amount = _float_or_none(row.get("Amount"))
        if quantity is None or amount is None:
            continue
        try:
            breaks.append(
                SourcingPriceBreak.model_validate(
                    {"quantity": quantity, "unit_price": amount}
                )
            )
        except PydanticValidationError as exc:
            raise SourcingValidationError(
                "TrustedParts price break did not match DTOs"
            ) from exc
    return breaks


def _links_by_type(raw_links: Any) -> dict[str, str]:
    links: dict[str, str] = {}
    for link in _list_of_dicts(raw_links, "Links"):
        link_type = _str_or_none(link.get("Type"))
        url = _str_or_none(link.get("Url"))
        if link_type and url:
            links[link_type.lower()] = url
    return links


def _first_matching_link(links: dict[str, str], needle: str) -> str | None:
    for link_type, url in links.items():
        if needle in link_type:
            return url
    return None


def _first_non_matching_link(links: dict[str, str], needles: set[str]) -> str | None:
    for link_type, url in links.items():
        if all(needle not in link_type for needle in needles):
            return url
    return None


def _list_of_dicts(raw_items: Any, field_name: str) -> list[dict[str, Any]]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise SourcingValidationError(f"TrustedParts {field_name} is not a list")
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise SourcingValidationError(f"TrustedParts {field_name} contains a non-object")
        items.append(item)
    return items


def _first_package_type(packages: list[dict[str, Any]]) -> str | None:
    for package in packages:
        value = _str_or_none(package.get("PackageType"))
        if value:
            return value
    return None


def _moq(packages: list[dict[str, Any]], pricing: dict[str, Any]) -> int | None:
    values = [
        value
        for package in packages
        if (value := _int_or_none(package.get("MinimumOrderQuantity"))) is not None
    ]
    if values:
        return min(values)
    return _int_or_none(pricing.get("MinimumQuantity"))


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
