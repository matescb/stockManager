"""DigiKey Product Information V4 API — MPN lookup via 2-legged OAuth.

We hit two endpoints:

1. POST /v1/oauth2/token (client_credentials) — mint a short-lived
   bearer token (~10 min). Cached per-instance.
2. GET /products/v4/search/{mpn}/productdetails — return the rich
   product record. Unlike Mouser, DigiKey reliably populates
   `Parameters[]` so most parts come back with a real parametric table.

Locale is fixed to CZ/en/CZK to match the user's environment. Anyone
who needs another locale can plumb it through later.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx

from app.domain.parts.providers.base import (
    MpnLookupResult,
    ProviderUpstreamError,
    match_kind,
)
from app.domain.parts.services.provider_quota import QUOTA, account_key
from app.domain.sourcing.providers import make_retrying_client

_API_BASE = "https://api.digikey.com"
_TOKEN_PATH = "/v1/oauth2/token"
_PRODUCT_DETAILS_PATH = "/products/v4/search/{mpn}/productdetails"
_KEYWORD_SEARCH_PATH = "/products/v4/search/keyword"
_TIMEOUT_SEC = 8.0  # Cap upstream wall-clock to prevent worker stall (BE2-011).

_LOCALE_SITE = "CZ"
_LOCALE_LANG = "en"
_LOCALE_CURR = "CZK"

# What a DigiKey burst suspension reads as. Says "suspended" so that
# `provider_refresh_failures.looks_like_quota` halts the sweep instead of
# recording a miss for every remaining part, and so `provider_cache`
# readers see the same word the breaker uses.
_SUSPENDED_MESSAGE = "DigiKey suspended this application (HTTP 403); retry in about 10 minutes"
_RATE_LIMIT_MESSAGE = "DigiKey rate limit reached"
# Any OTHER 403: the client id is not subscribed to Product Information,
# or was revoked. Deliberately free of the quota/suspension words — it is
# a credential problem (a hard failure for the ordinary 5-strike breaker),
# not a reason to stop every lookup for ten minutes.
_FORBIDDEN_MESSAGE = (
    "DigiKey refused the credentials (HTTP 403): the application is not "
    "authorised for the Product Information API"
)
# Where a seam leaves a 403's Retry-After header for `_refusal` to read.
# The seams return `(status, body)` only, so the header rides in the body.
_RETRY_AFTER_KEY = "_sm_retry_after"
# Words in a 403 body that say "throttled", as opposed to "forbidden".
_THROTTLE_WORDS = ("throttl", "rate limit", "ratelimit", "suspend", "too many", "burst")

# DigiKey ParameterText values that are really package descriptors —
# we lift the first match into the canonical `footprint`.
_FOOTPRINT_KEYS = {
    "package / case",
    "supplier device package",
    "package",
    "footprint",
}


def _post_token(client_id: str, client_secret: str) -> dict[str, Any]:
    """Network seam — tests monkeypatch this."""
    with make_retrying_client(provider_name="digikey", timeout=_TIMEOUT_SEC) as client:
        resp = client.post(
            f"{_API_BASE}{_TOKEN_PATH}",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        if resp.status_code >= 500:
            raise ProviderUpstreamError(
                "digikey",
                f"DigiKey upstream returned HTTP {resp.status_code}",
            )
        resp.raise_for_status()
        return resp.json()


def _digikey_headers(token: str, client_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-DIGIKEY-Client-Id": client_id,
        "X-DIGIKEY-Locale-Site": _LOCALE_SITE,
        "X-DIGIKEY-Locale-Language": _LOCALE_LANG,
        "X-DIGIKEY-Locale-Currency": _LOCALE_CURR,
        "Accept": "application/json",
    }


def _get_product_details(
    token: str, client_id: str, mpn: str
) -> tuple[int, dict[str, Any]]:
    """Network seam. Returns (status_code, json_body). Lets the caller
    distinguish 404 (no match) from 200 (found) without raising."""
    url = f"{_API_BASE}{_PRODUCT_DETAILS_PATH.format(mpn=quote(mpn, safe=''))}"
    with make_retrying_client(provider_name="digikey", timeout=_TIMEOUT_SEC) as client:
        resp = client.get(url, headers=_digikey_headers(token, client_id))
    # Side channel for the day's remaining count (SM-005). Not part of
    # the return value: tests stub this seam with a 2-tuple.
    QUOTA.note_remaining("digikey", account_key(client_id), resp.headers)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, _mark_retry_after(resp, body)


def _post_keyword_search(
    token: str, client_id: str, keywords: str, limit: int = 5
) -> tuple[int, dict[str, Any]]:
    """Network seam for the fuzzy keyword endpoint. Useful when a real
    manufacturer MPN doesn't match DigiKey's canonical indexing exactly
    (e.g. Molex prints `98266-0897` on bags but DigiKey indexes the
    10-digit form `0982660897`)."""
    url = f"{_API_BASE}{_KEYWORD_SEARCH_PATH}"
    payload = {"Keywords": keywords, "Limit": limit}
    headers = {**_digikey_headers(token, client_id), "Content-Type": "application/json"}
    with make_retrying_client(provider_name="digikey", timeout=_TIMEOUT_SEC) as client:
        resp = client.post(url, headers=headers, json=payload)
    QUOTA.note_remaining("digikey", account_key(client_id), resp.headers)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, _mark_retry_after(resp, body)


class DigiKeyProvider:
    name = "digikey"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        # Per-instance token cache. Provider instances are short-lived
        # (built per request in make_provider) so this is mostly for
        # within-call reuse, not cross-request sharing.
        self._token: str | None = None
        self._token_exp: float = 0.0

    def _get_token(self) -> str:
        # Refresh proactively when ≤60s remain on the cached token.
        if self._token and time.monotonic() < self._token_exp - 60:
            return self._token
        body = _post_token(self.client_id, self.client_secret)
        token = body.get("access_token")
        ttl = int(body.get("expires_in") or 600)
        if not isinstance(token, str) or not token:
            raise RuntimeError("DigiKey OAuth: no access_token in response")
        self._token = token
        self._token_exp = time.monotonic() + ttl
        return token

    def _request_with_retry(
        self,
        fn: Callable[[str], tuple[int, dict[str, Any]]],
    ) -> tuple[int, dict[str, Any]]:
        """Run `fn(token)`. On 401 (token expired between mint and use, or
        revoked server-side), invalidate the cache, mint a fresh token,
        and retry once. The proactive 60s buffer in _get_token covers the
        common case; this guards the rare interleaving where DigiKey
        rotates the token in the few seconds we held it."""
        account = account_key(self.client_id)
        token = self._get_token()
        # Counted before the call so a header the call returns already
        # accounts for it (`provider_quota.note_remaining`).
        QUOTA.note_call("digikey", account)
        status, data = fn(token)
        if status == 401:
            self._token = None
            self._token_exp = 0.0
            token = self._get_token()
            QUOTA.note_call("digikey", account)
            status, data = fn(token)
        return status, data

    def lookup_mpn(self, mpn: str) -> MpnLookupResult:
        mpn = mpn.strip()
        if not mpn:
            return {"found": False, "result": None, "message": "empty MPN"}
        try:
            status, data = self._request_with_retry(
                lambda tok: _get_product_details(tok, self.client_id, mpn)
            )
        except ProviderUpstreamError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ProviderUpstreamError(
                "digikey",
                f"DigiKey upstream returned HTTP {exc.response.status_code}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderUpstreamError(
                "digikey",
                f"DigiKey upstream unavailable: timed out ({type(exc).__name__})",
            ) from exc
        except RuntimeError as exc:
            # Raised by _get_token when the OAuth response is malformed.
            return {
                "found": False,
                "result": None,
                "message": f"DigiKey auth failed ({type(exc).__name__})",
            }
        except httpx.ConnectError as exc:
            raise ProviderUpstreamError(
                "digikey",
                f"DigiKey upstream unavailable: connection failed ({type(exc).__name__})",
            ) from exc
        if status >= 500:
            raise ProviderUpstreamError(
                "digikey",
                f"DigiKey upstream returned HTTP {status}",
            )
        refused = _refusal(status, data)
        if refused is not None:
            return refused

        # Happy path — exact-match ProductDetails returned a record.
        if status == 200:
            product = (data or {}).get("Product") or {}
            if product:
                return _found(mpn, product)
            # 200 with empty Product is treated like 404 below.

        # Fallback: ProductDetails requires an exact match against
        # DigiKey's canonical indexing. Distributor-printed MPNs often
        # use a different format (Molex's "98266-0897" vs DigiKey's
        # "0982660897"). The keyword search is fuzzy and typically
        # finds the right product as the top hit.
        if status in (200, 404):
            try:
                kw_status, kw_data = self._request_with_retry(
                    lambda tok: _post_keyword_search(tok, self.client_id, mpn, limit=1)
                )
            except ProviderUpstreamError:
                raise
            except httpx.HTTPStatusError as exc:
                raise ProviderUpstreamError(
                    "digikey",
                    f"DigiKey upstream returned HTTP {exc.response.status_code}",
                ) from exc
            except httpx.TimeoutException as exc:
                raise ProviderUpstreamError(
                    "digikey",
                    f"DigiKey upstream unavailable: timed out ({type(exc).__name__})",
                ) from exc
            except httpx.ConnectError as exc:
                raise ProviderUpstreamError(
                    "digikey",
                    f"DigiKey upstream unavailable: connection failed ({type(exc).__name__})",
                ) from exc
            if kw_status == 200:
                products = (kw_data or {}).get("Products") or []
                if products and isinstance(products[0], dict):
                    # Usually `fuzzy`: the keyword search finds the
                    # product DigiKey indexes under a different spelling,
                    # and `match` is how a caller learns it is not the
                    # string it asked for.
                    return _found(mpn, products[0])
            # A 403/429 here used to fall through to "no match for MPN",
            # which records the provider as never having heard of a part
            # it simply refused to look up.
            refused = _refusal(kw_status, kw_data)
            if refused is not None:
                return refused
            if kw_status >= 500:
                raise ProviderUpstreamError(
                    "digikey",
                    f"DigiKey upstream returned HTTP {kw_status}",
                )
            return {"found": False, "result": None, "message": "no match for MPN"}

        return {
            "found": False,
            "result": None,
            "message": f"DigiKey returned HTTP {status}",
        }


def _mark_retry_after(resp: httpx.Response, body: Any) -> Any:
    """Carry a 403/429's Retry-After header into the body `_refusal` reads."""
    refused = resp.status_code in (403, 429)
    retry_after = resp.headers.get("Retry-After") if refused else None
    if not retry_after:
        return body
    base = body if isinstance(body, dict) else {}
    return {**base, _RETRY_AFTER_KEY: retry_after}


def _retry_after_ms(body: Any) -> int | None:
    """Retry-After (delta-seconds form only) as milliseconds, if present."""
    raw = body.get(_RETRY_AFTER_KEY) if isinstance(body, dict) else None
    try:
        seconds = float(str(raw).strip()) if raw else None
    except ValueError:
        return None
    return int(seconds * 1000) if seconds is not None and seconds >= 0 else None


def _is_throttle(body: Any) -> bool:
    """Does this 403 look like DigiKey's burst suspension?

    A Retry-After header is the strongest sign; otherwise the error text
    has to say so. A bare 403 is a credential/subscription problem.
    """
    if isinstance(body, dict) and body.get(_RETRY_AFTER_KEY):
        return True
    text = json.dumps(body, default=str).lower() if body else ""
    return any(word in text for word in _THROTTLE_WORDS)


def _found(query: str, product: dict[str, Any]) -> MpnLookupResult:
    record = _record_from_product(product)
    return {
        "found": True,
        "result": record,
        "match": match_kind(query, record.get("mpn")),
        "message": None,
    }


def _refusal(status: int, body: Any = None) -> MpnLookupResult | None:
    """DigiKey refusing to answer, as opposed to not knowing the part.

    429 is the day's (or minute's) allowance — matched by message in
    `provider_refresh_failures.looks_like_quota`, so the wording is load-
    bearing. A 403 that looks throttled (`_is_throttle`) is the ten-minute
    burst suspension (DigiKey API docs); `suspended` makes `provider_cache`
    open this credential's breaker for it. Any other 403 is a credential
    problem and is reported as one.
    """
    if status == 429:
        out: MpnLookupResult = {"found": False, "result": None, "message": _RATE_LIMIT_MESSAGE}
        retry_ms = _retry_after_ms(body)
        if retry_ms is not None:
            out["retry_after_ms"] = retry_ms
        return out
    if status == 403 and not _is_throttle(body):
        return {"found": False, "result": None, "message": _FORBIDDEN_MESSAGE}
    if status == 403:
        return {
            "found": False,
            "result": None,
            "message": _SUSPENDED_MESSAGE,
            "suspended": True,
        }
    return None


def _deepest_category_name(cat: Any) -> str | None:
    """Walk Category.ChildCategories down to the deepest leaf and return
    its Name. DigiKey nests categories arbitrarily deep."""
    name: str | None = None
    while isinstance(cat, dict) and cat.get("Name"):
        v = (cat.get("Name") or "").strip()
        if v:
            name = v
        children = cat.get("ChildCategories") or []
        if children and isinstance(children, list) and isinstance(children[0], dict):
            cat = children[0]
        else:
            break
    return name


def _record_from_product(p: dict[str, Any]) -> dict[str, Any]:
    """Map a DigiKey ProductDetails Product object → canonical record."""
    specs_by_key: dict[str, list[str]] = {}
    spec_order: list[str] = []
    footprint: str | None = None

    def add_spec(key: str, value: Any) -> None:
        if not key or value is None:
            return
        v = str(value).strip()
        if not v:
            return
        if key not in specs_by_key:
            spec_order.append(key)
            specs_by_key[key] = [v]
        elif v not in specs_by_key[key]:
            specs_by_key[key].append(v)

    # ---- Parameters[] — the rich parametric table -------------------
    for param in (p.get("Parameters") or []):
        if not isinstance(param, dict):
            continue
        name = (param.get("ParameterText") or "").strip()
        value = (param.get("ValueText") or "").strip()
        if not name or not value:
            continue
        add_spec(name, value)
        if footprint is None and name.lower() in _FOOTPRINT_KEYS:
            footprint = value

    # ---- Lifecycle / supply ----------------------------------------
    status = (((p.get("ProductStatus") or {}).get("Status")) or "").strip()
    if status:
        add_spec("Lifecycle", status)

    classifications = p.get("Classifications") or {}
    if isinstance(classifications, dict):
        rohs = (classifications.get("RohsStatus") or "").strip()
        if rohs:
            add_spec("RoHS", rohs)
        reach = (classifications.get("ReachStatus") or "").strip()
        if reach and "REACH" not in specs_by_key:
            add_spec("REACH", reach)
        msl = (classifications.get("MoistureSensitivityLevel") or "").strip()
        if msl:
            add_spec("MSL", msl)
        htsus = (classifications.get("HtsusCode") or "").strip()
        if htsus:
            add_spec("HTS code", htsus)
        eccn = (classifications.get("ExportControlClassNumber") or "").strip()
        if eccn:
            add_spec("ECCN", eccn)

    qty = p.get("QuantityAvailable")
    if isinstance(qty, (int, float)) and qty > 0:
        # DigiKey's own stock count, rendered as a display string on the
        # part's catalog fields. It never touches our ledger, and the
        # upstream value is already a whole number of packages.
        add_spec("In stock (qty)", str(int(qty)))  # noqa: quantity-decimal

    lead = p.get("ManufacturerLeadWeeks")
    if isinstance(lead, str) and lead.strip():
        s = lead.strip()
        # DigiKey returns either bare "8" or "8 Weeks" — normalize.
        if "week" not in s.lower():
            s = f"{s} weeks"
        add_spec("Lead time", s)

    series = ((p.get("Series") or {}).get("Name") or "").strip()
    if series:
        add_spec("Series", series)

    # ---- ProductVariations[] — packaging + DigiKey P/Ns + pricing --
    variations = p.get("ProductVariations") or []

    # Pricing: pick the variation whose lowest break-quantity is smallest
    # (i.e. the variation that quotes the smallest MOQ — usually cut-tape).
    # A reel variation may have a cheaper per-unit price at 3000+, but that
    # answers a different question ("bulk price") than what the specs row
    # represents ("what does one cost?"). Tie-break on cheapest unit price.
    pricing_source: list[dict[str, Any]] = []
    best_min_qty = float("inf")
    best_min_price = float("inf")
    for v in variations:
        if not isinstance(v, dict):
            continue
        pricing = v.get("StandardPricing") or []
        if not pricing:
            continue
        min_qty: int | None = None
        min_price: float | None = None
        for tier in pricing:
            if not isinstance(tier, dict):
                continue
            try:
                q = int(tier.get("BreakQuantity") or 0)
                pr = float(tier.get("UnitPrice") or 0)
            except (TypeError, ValueError):
                continue
            if q <= 0 or pr <= 0:
                continue
            if min_qty is None or q < min_qty:
                min_qty = q
                min_price = pr
        if min_qty is None or min_price is None:
            continue
        if (min_qty < best_min_qty
                or (min_qty == best_min_qty and min_price < best_min_price)):
            best_min_qty = min_qty
            best_min_price = min_price
            pricing_source = pricing
    for tier in pricing_source:
        if not isinstance(tier, dict):
            continue
        try:
            qty_n = int(tier.get("BreakQuantity") or 0)
            price_n = float(tier.get("UnitPrice") or 0)
        except (TypeError, ValueError):
            continue
        if qty_n <= 0 or price_n <= 0:
            continue
        add_spec(f"Unit price ({qty_n}+)", f"{price_n:.4f} {_LOCALE_CURR}")

    # Packaging names + DigiKey P/Ns — collapsed to one row each
    # (matches the Mouser "Packaging" treatment).
    packaging_names: list[str] = []
    dk_pns: list[str] = []
    for v in variations:
        if not isinstance(v, dict):
            continue
        pkg_name = ((v.get("Packaging") or {}).get("Name") or "").strip()
        if pkg_name and pkg_name not in packaging_names:
            packaging_names.append(pkg_name)
        dk_pn = (v.get("DigiKeyProductNumber") or "").strip()
        if dk_pn and dk_pn not in dk_pns:
            dk_pns.append(dk_pn)
    if packaging_names:
        add_spec("Packaging", " / ".join(packaging_names))
    if dk_pns:
        add_spec("DigiKey P/N", " / ".join(dk_pns))

    # ---- Boolean flags --------------------------------------------
    if p.get("Discontinued"):
        add_spec("Discontinued", "yes")
    if p.get("EndOfLife"):
        add_spec("End of life", "yes")
    if p.get("Ncnr"):
        add_spec("NCNR", "yes")

    # ---- Top-level identity / description -------------------------
    mpn_str = (p.get("ManufacturerProductNumber") or "").strip()
    manufacturer = (
        ((p.get("Manufacturer") or {}).get("Name") or "").strip() or None
    )
    desc_obj = p.get("Description") or {}
    description = (desc_obj.get("ProductDescription") or "").strip() or None
    detailed = (desc_obj.get("DetailedDescription") or "").strip()
    if detailed and detailed != description:
        add_spec("Detailed description", detailed)

    category = _deepest_category_name(p.get("Category"))

    photo_url = (p.get("PhotoUrl") or "").strip() or None
    datasheet_url = (p.get("DatasheetUrl") or "").strip() or None
    product_url = (p.get("ProductUrl") or "").strip() or ""

    specs = [
        {"key": k, "value": " / ".join(specs_by_key[k])}
        for k in spec_order
    ]

    return {
        "mpn": mpn_str,
        "manufacturer": manufacturer,
        "description": description,
        "category": category,
        "footprint": footprint,
        "datasheet_url": datasheet_url,
        "image_url": photo_url,
        "source_url": product_url,
        "specs": specs,
    }
