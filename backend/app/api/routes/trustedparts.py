from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.core.responses import ok

router = APIRouter()


SEARCH_URL = "https://www.trustedparts.com/en/search/{mpn}"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 stockmgr/0.1"
)
_TIMEOUT_SEC = 8.0


class LookupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str = Field(min_length=1, max_length=200)


def _fetch_html(url: str) -> tuple[str, str]:
    """Network seam: fetch the URL, follow redirects, return (final_url, html).

    Wrapped in its own function so tests can monkeypatch this single
    point and avoid real network traffic.
    """
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(follow_redirects=True, timeout=_TIMEOUT_SEC, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return str(resp.url), resp.text


_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?P<key>[^"\']+)["\'][^>]*content=["\'](?P<val>[^"\']*)["\']',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_PDF_HREF_RE = re.compile(
    r'href=["\'](?P<url>[^"\']+\.pdf(?:\?[^"\']*)?)["\']',
    re.IGNORECASE,
)


def _extract_meta(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _META_RE.finditer(html):
        out[m.group("key").lower()] = unescape(m.group("val")).strip()
    return out


def _first_datasheet(html: str, base_url: str) -> str | None:
    """Best-effort: find the first link whose href ends in .pdf (typically
    a datasheet on a part page)."""
    m = _PDF_HREF_RE.search(html)
    if not m:
        return None
    href = unescape(m.group("url"))
    if href.startswith(("http://", "https://", "//")):
        if href.startswith("//"):
            return "https:" + href
        return href
    return urljoin(base_url, href)


def _label_value(html: str, label: str) -> str | None:
    """Try to extract the value next to a label in a typical
    <dt>label</dt><dd>value</dd> or <th>label</th><td>value</td> layout.
    Returns None if not present."""
    pattern = re.compile(
        rf"<(?:dt|th)[^>]*>\s*{re.escape(label)}\s*</(?:dt|th)>\s*"
        rf"<(?:dd|td)[^>]*>(?P<val>.*?)</(?:dd|td)>",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return None
    raw = re.sub(r"<[^>]+>", "", m.group("val"))
    raw = unescape(raw).strip()
    return raw or None


def _parse_product_html(html: str, base_url: str, mpn: str) -> dict[str, Any]:
    meta = _extract_meta(html)

    description = (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
        or _label_value(html, "Description")
    )
    title = None
    t = _TITLE_RE.search(html)
    if t:
        title = re.sub(r"\s+", " ", unescape(t.group(1))).strip() or None

    manufacturer = (
        meta.get("product:brand")
        or meta.get("og:brand")
        or _label_value(html, "Manufacturer")
        or _label_value(html, "Brand")
    )
    category = (
        meta.get("product:category")
        or meta.get("og:category")
        or _label_value(html, "Category")
    )
    footprint = (
        _label_value(html, "Package")
        or _label_value(html, "Package / Case")
        or _label_value(html, "Footprint")
        or _label_value(html, "Case")
    )
    datasheet_url = _first_datasheet(html, base_url)

    return {
        "mpn": mpn,
        "manufacturer": manufacturer or None,
        "description": description or title,
        "category": category or None,
        "footprint": footprint or None,
        "datasheet_url": datasheet_url,
        "source_url": base_url,
    }


def _looks_like_product_page(html: str, parsed: dict[str, Any]) -> bool:
    """Heuristic: a TrustedParts search URL redirects to a product page
    when there's a unique match. We treat the page as a hit if we got at
    least one of {manufacturer, description, datasheet_url}."""
    return bool(parsed.get("manufacturer") or parsed.get("description") or parsed.get("datasheet_url"))


@router.post("/lookup")
def lookup(payload: LookupIn):
    mpn = payload.mpn.strip()
    if not mpn:
        return ok({"found": False, "result": None, "message": "empty MPN"})

    search_url = SEARCH_URL.format(mpn=mpn)
    try:
        final_url, html = _fetch_html(search_url)
    except Exception as exc:  # network / parse failure is expected UX
        return ok(
            {
                "found": False,
                "result": None,
                "message": f"upstream unavailable ({type(exc).__name__})",
            }
        )

    try:
        parsed = _parse_product_html(html, final_url, mpn)
    except Exception as exc:
        return ok(
            {
                "found": False,
                "result": None,
                "message": f"could not parse upstream response ({type(exc).__name__})",
            }
        )

    if not _looks_like_product_page(html, parsed):
        return ok(
            {
                "found": False,
                "result": None,
                "message": "no unique match for MPN",
            }
        )

    return ok({"found": True, "result": parsed, "message": None})
