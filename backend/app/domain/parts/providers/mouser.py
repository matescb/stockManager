"""Mouser Search API — MPN lookup."""
from __future__ import annotations

from typing import Any

import httpx

from app.domain.parts.providers.base import MpnLookupResult


_ENDPOINT = "https://api.mouser.com/api/v1/search/partnumber"
_TIMEOUT_SEC = 10.0


def _post_mouser(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Network seam — tests monkeypatch this single function."""
    with httpx.Client(timeout=_TIMEOUT_SEC) as client:
        resp = client.post(url, json=payload, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()


class MouserProvider:
    name = "mouser"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def lookup_mpn(self, mpn: str) -> MpnLookupResult:
        mpn = mpn.strip()
        if not mpn:
            return {"found": False, "result": None, "message": "empty MPN"}

        url = f"{_ENDPOINT}?apiKey={self.api_key}"
        # `partSearchOptions: "Exact"` matches only Mouser's own part numbers,
        # not manufacturer MPNs (the field name is misleading). We want
        # manufacturer-MPN search, so leave the option off — the API then
        # searches both Mouser P/N and Manufacturer P/N with partial match.
        body = {"SearchByPartRequest": {"mouserPartNumber": mpn}}
        try:
            data = _post_mouser(url, body)
        except Exception as exc:
            return {
                "found": False,
                "result": None,
                "message": f"upstream unavailable ({type(exc).__name__})",
            }

        errors = data.get("Errors") or []
        if errors:
            # Mouser surfaces auth + quota problems here. Pick the first.
            msg = errors[0].get("Message") or "Mouser returned an error"
            return {"found": False, "result": None, "message": msg}

        parts = (data.get("SearchResults") or {}).get("Parts") or []
        if not parts:
            return {"found": False, "result": None, "message": "no match for MPN"}

        # Take the first match — the API is asked for `Exact` so this is
        # almost always 0 or 1.
        p = parts[0]
        return {
            "found": True,
            "result": {
                "mpn": p.get("ManufacturerPartNumber") or mpn,
                "manufacturer": p.get("Manufacturer") or None,
                "description": p.get("Description") or None,
                "category": p.get("Category") or None,
                # Mouser exposes package via ProductAttributes (variable shape).
                # Skip for v1 — better to leave blank than to mislabel.
                "footprint": None,
                "datasheet_url": p.get("DataSheetUrl") or None,
                "source_url": p.get("ProductDetailUrl") or "",
            },
            "message": None,
        }
