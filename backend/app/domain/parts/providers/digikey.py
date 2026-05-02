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

import time
from typing import Any, Callable
from urllib.parse import quote

import httpx

from app.domain.parts.providers.base import MpnLookupResult


_API_BASE = "https://api.digikey.com"
_TOKEN_PATH = "/v1/oauth2/token"
_PRODUCT_DETAILS_PATH = "/products/v4/search/{mpn}/productdetails"
_KEYWORD_SEARCH_PATH = "/products/v4/search/keyword"
_TIMEOUT_SEC = 8.0  # Cap upstream wall-clock to prevent worker stall (BE2-011).

_LOCALE_SITE = "CZ"
_LOCALE_LANG = "en"
_LOCALE_CURR = "CZK"

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
    with httpx.Client(timeout=_TIMEOUT_SEC) as client:
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
    with httpx.Client(timeout=_TIMEOUT_SEC) as client:
        resp = client.get(url, headers=_digikey_headers(token, client_id))
    try:
        body = resp.json()
    except Exception:
        body = {}
    return resp.status_code, body


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
    with httpx.Client(timeout=_TIMEOUT_SEC) as client:
        resp = client.post(url, headers=headers, json=payload)
    try:
        body = resp.json()
    except Exception:
        body = {}
    return resp.status_code, body


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
        token = self._get_token()
        status, data = fn(token)
        if status == 401:
            self._token = None
            self._token_exp = 0.0
            token = self._get_token()
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
        except RuntimeError as exc:
            # Raised by _get_token when the OAuth response is malformed.
            return {
                "found": False,
                "result": None,
                "message": f"DigiKey auth failed ({type(exc).__name__})",
            }
        except Exception as exc:
            return {
                "found": False,
                "result": None,
                "message": f"upstream unavailable ({type(exc).__name__})",
            }
        if status == 429:
            return {
                "found": False,
                "result": None,
                "message": "DigiKey rate limit reached",
            }

        # Happy path — exact-match ProductDetails returned a record.
        if status == 200:
            product = (data or {}).get("Product") or {}
            if product:
                return {
                    "found": True,
                    "result": _record_from_product(product),
                    "message": None,
                }
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
            except Exception as exc:
                return {
                    "found": False,
                    "result": None,
                    "message": f"upstream unavailable ({type(exc).__name__})",
                }
            if kw_status == 200:
                products = (kw_data or {}).get("Products") or []
                if products and isinstance(products[0], dict):
                    return {
                        "found": True,
                        "result": _record_from_product(products[0]),
                        "message": None,
                    }
            return {"found": False, "result": None, "message": "no match for MPN"}

        return {
            "found": False,
            "result": None,
            "message": f"DigiKey returned HTTP {status}",
        }


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
        add_spec("In stock (qty)", str(int(qty)))

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
