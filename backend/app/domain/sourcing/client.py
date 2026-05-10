"""TrustedParts API v2 client for live sourcing lookups."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from app.domain.sourcing._generated.trustedparts_v2 import (
    InventoryApiResponse,
    InventoryDistributorResult,
    InventoryPartResult,
    Price,
    ProductPackageType,
    ProductPricing,
    SearchApiLink,
)
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingPriceBreak,
    SourcingQuery,
    SourcingRohsCompliance,
    SourcingSearchRaw,
    SourcingSpecification,
)

TP_V2_URL = "https://api.trustedparts.com/v2/search"
TP_TIMEOUT_SECONDS = 8.0
MAX_TP_QUERIES = 50
MIN_SEARCH_TOKEN_LENGTH = 2
MAX_SEARCH_TOKEN_LENGTH = 100

logger = logging.getLogger(__name__)


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


def _post_tp(
    url: str,
    json_body: dict[str, Any],
    headers: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    """Network seam — monkeypatched in tests. Returns (status_code, body_dict)."""
    with httpx.Client(timeout=TP_TIMEOUT_SECONDS) as client:
        response = client.post(
            url,
            json=json_body,
            headers=headers,
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
        language_code: str | None = None,
    ) -> None:
        self.company_id = company_id
        self.api_key = api_key
        self.country_code = country_code
        self.currency_code = currency_code
        self.language_code = language_code
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
        _validate_search_tokens(queries)

        payload = self._payload(
            queries,
            exact_match=exact_match,
            in_stock_only=in_stock_only,
            distributors=distributors,
            use_cached_data=use_cached_data,
            is_crawler=is_crawler,
        )
        request_hash = _request_hash(payload)
        try:
            status, body = _post_tp(TP_V2_URL, payload, _headers(self.api_key))
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

        request_id = _legacy_request_id(body)
        validated = _validate_inventory_response(
            _body_without_legacy_request_id(body),
            request_hash,
        )
        _handle_tp_messages(validated, request_hash)
        error_message = _str_or_none(validated.ErrorMessage)
        if error_message:
            raise SourcingUpstreamError(f"TP error: {error_message}")

        return _parse_search_response(validated, request_id=request_id)

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
        if self.language_code:
            payload["LanguageCode"] = self.language_code
        return payload


def _query_payload(query: SourcingQuery) -> dict[str, Any]:
    item: dict[str, Any] = {"SearchToken": query.search_token}
    if query.manufacturers:
        item["Manufacturers"] = query.manufacturers
    return item


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }


def _validate_search_tokens(queries: list[SourcingQuery]) -> None:
    for query in queries:
        token_length = len(query.search_token)
        if token_length < MIN_SEARCH_TOKEN_LENGTH or token_length > MAX_SEARCH_TOKEN_LENGTH:
            raise ValueError("TrustedParts SearchToken length must be between 2 and 100")


def _request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_inventory_response(
    body: dict[str, Any],
    request_hash: str,
) -> InventoryApiResponse:
    try:
        return InventoryApiResponse.model_validate(body)
    except PydanticValidationError as exc:
        logger.warning(
            "TrustedParts response validation failed request_hash=%s errors=%s",
            request_hash,
            _validation_error_summary(exc),
            extra={
                "request_hash": request_hash,
                "validation_errors": _validation_error_summary(exc),
            },
        )
        raise SourcingValidationError(
            "TrustedParts response did not match generated schema"
        ) from exc


def _legacy_request_id(body: dict[str, Any]) -> str | None:
    for key in ("RequestId", "RequestID", "request_id"):
        if key not in body:
            continue
        value = body[key]
        return value if isinstance(value, str) else str(value)
    return None


def _body_without_legacy_request_id(body: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(body)
    for key in ("RequestId", "RequestID", "request_id"):
        cleaned.pop(key, None)
    return cleaned


def _validation_error_summary(exc: PydanticValidationError) -> list[dict[str, str]]:
    return [
        {
            "type": str(error["type"]),
            "path": ".".join(str(part) for part in error["loc"]),
        }
        for error in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    ]


def _handle_tp_messages(response: InventoryApiResponse, request_hash: str) -> None:
    for message in response.Messages or []:
        logger.info(
            "TrustedParts tp_message: %s",
            message,
            extra={"request_hash": request_hash, "tp_message": message},
        )


def _parse_search_response(
    response: InventoryApiResponse,
    *,
    request_id: str | None,
) -> SourcingSearchRaw:
    part_results = response.PartResults
    if part_results is None:
        raise SourcingValidationError("TrustedParts response PartResults is not a list")

    offers: list[SourcingOffer] = []
    for part in part_results:
        offers.append(_offer_from_part(part))

    try:
        return SourcingSearchRaw.model_validate(
            {
                "offers": offers,
                "request_id": request_id,
                "tp_current_date": response.CurrentDate,
                "tp_response_time": _str_or_none(response.ResponseTime),
            }
        )
    except PydanticValidationError as exc:
        raise SourcingValidationError("TrustedParts response did not match sourcing DTOs") from exc


def _offer_from_part(part: InventoryPartResult) -> SourcingOffer:
    distributors: list[SourcingDistributor] = []
    description: str | None = None
    datasheet_url: str | None = None
    manufacturer_url: str | None = None

    for distributor in part.Distributors or []:
        name = _str_or_none(distributor.Name) or ""
        for result in distributor.DistributorResults or []:
            if description is None:
                description = _str_or_none(result.Description)
            links = _links_by_type(result.Links)
            if datasheet_url is None:
                datasheet_url = _first_matching_link(links, "datasheet")
            if manufacturer_url is None:
                manufacturer_url = _first_matching_link(links, "manufacturer")
            distributors.append(
                _distributor_from_result(
                    name,
                    distributor.Id,
                    result,
                    links,
                )
            )

    try:
        return SourcingOffer.model_validate(
            {
                "mpn": _str_or_none(part.PartNumber) or "",
                "manufacturer": _str_or_none(part.Manufacturer),
                "description": description,
                "distributors": distributors,
                "links": SourcingLinks(
                    primary=_str_or_none(part.ProductUrl),
                    manufacturer=manufacturer_url,
                    datasheet=datasheet_url,
                ),
                "lifecycle_risk": _str_or_none(part.LifecycleRisk),
                "supply_chain_risk": _str_or_none(part.SupplyChainRisk),
                "is_affected_by_tariff": part.IsAffectedByTariff,
                "manufacturer_id": part.ManufacturerId,
                "specifications": _specifications(part.Specifications),
            }
        )
    except PydanticValidationError as exc:
        raise SourcingValidationError("TrustedParts part result did not match DTOs") from exc


def _distributor_from_result(
    distributor_name: str,
    distributor_id: int | None,
    result: InventoryDistributorResult,
    links: dict[str, str],
) -> SourcingDistributor:
    pricing = result.Pricing
    stock = result.Stock
    packages = result.Packaging or []
    price_breaks = _price_breaks(pricing.Prices if pricing is not None else None)
    product_url = (
        _first_matching_link(links, "distributor")
        or _first_matching_link(links, "product")
        or _first_non_matching_link(links, {"datasheet", "manufacturer"})
    )

    try:
        return SourcingDistributor.model_validate(
            {
                "distributor_id": distributor_id,
                "name": distributor_name,
                "sku": _str_or_none(result.DistributorPartNumber),
                "packaging": _first_package_type(packages),
                "moq": _moq(packages, pricing),
                "lead_time_days": None,
                "stock": _int_or_none(stock.QuantityOnHand if stock is not None else None),
                "unit_price": price_breaks[0].unit_price if price_breaks else None,
                "currency": _str_or_none(pricing.CurrencyCode if pricing is not None else None),
                "price_breaks": price_breaks,
                "product_url": product_url,
                "rohs_compliance": _rohs_compliance(result),
                "availability_text": _str_or_none(
                    stock.Availability if stock is not None else None
                ),
                "quantity_multiple": _int_count_or_none(
                    pricing.QuantityMultiple if pricing is not None else None
                ),
            }
        )
    except PydanticValidationError as exc:
        raise SourcingValidationError("TrustedParts distributor result did not match DTOs") from exc


def _price_breaks(price_rows: list[Price] | None) -> list[SourcingPriceBreak]:
    breaks: list[SourcingPriceBreak] = []
    for row in price_rows or []:
        quantity = _int_or_none(row.Quantity)
        amount = _float_or_none(row.Amount)
        if quantity is None or amount is None:
            continue
        try:
            breaks.append(
                SourcingPriceBreak.model_validate(
                    {
                        "quantity": quantity,
                        "unit_price": amount,
                        "formatted_amount": _str_or_none(row.FormattedAmount),
                        "text": _str_or_none(row.Text),
                    }
                )
            )
        except PydanticValidationError as exc:
            raise SourcingValidationError("TrustedParts price break did not match DTOs") from exc
    return breaks


def _specifications(raw_specs: Any) -> list[SourcingSpecification]:
    specs: list[SourcingSpecification] = []
    for item in raw_specs or []:
        key = _str_or_none(item.Key)
        value = _str_or_none(item.Value)
        if key is None or value is None:
            continue
        specs.append(SourcingSpecification(key=key, value=value))
    return specs


def _rohs_compliance(result: InventoryDistributorResult) -> list[SourcingRohsCompliance]:
    compliance = result.Compliance
    rows = compliance.RoHS if compliance is not None else None
    out: list[SourcingRohsCompliance] = []
    for row in rows or []:
        region = _str_or_none(row.Region)
        if region is None or row.IsCompliant is None:
            continue
        out.append(
            SourcingRohsCompliance(
                region=region,
                is_compliant=row.IsCompliant,
                description=_str_or_none(row.Description),
            )
        )
    return out


def _links_by_type(raw_links: list[SearchApiLink] | None) -> dict[str, str]:
    links: dict[str, str] = {}
    for link in raw_links or []:
        link_type = _str_or_none(link.Type)
        url = _str_or_none(link.Url)
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


def _first_package_type(packages: list[ProductPackageType]) -> str | None:
    for package in packages:
        value = _str_or_none(package.PackageType)
        if value:
            return value
    return None


def _moq(
    packages: list[ProductPackageType],
    pricing: ProductPricing | None,
) -> int | None:
    values = [
        value
        for package in packages
        if (value := _int_or_none(package.MinimumOrderQuantity)) is not None
    ]
    if values:
        return min(values)
    return _int_or_none(pricing.MinimumQuantity if pricing is not None else None)


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


def _int_count_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
