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

        # Take the first match — for partial-match search this is the
        # closest stocked variant.
        p = parts[0]

        # ProductAttributes is a list like
        #   [{"AttributeName": "Resistance", "AttributeValue": "1 kOhms"}, ...]
        # Mouser frequently emits duplicate keys (e.g. three "Packaging"
        # rows for cut-tape / reel / MouseReel) — we collapse them into a
        # single row whose value is the unique values joined with " / ",
        # because custom_fields has a UNIQUE (workspace, object, key)
        # constraint and the user only cares about the union anyway.
        raw_attrs = p.get("ProductAttributes") or []
        specs_by_key: dict[str, list[str]] = {}
        spec_order: list[str] = []
        # Pull a few attribute names out as first-class fields when present.
        footprint: str | None = None
        for a in raw_attrs:
            name = (a.get("AttributeName") or "").strip()
            value = (a.get("AttributeValue") or "").strip()
            if not name or not value:
                continue
            if name not in specs_by_key:
                spec_order.append(name)
                specs_by_key[name] = []
            if value not in specs_by_key[name]:
                specs_by_key[name].append(value)
            # Mouser uses variable label naming for package — match the
            # most common variants.
            if footprint is None and name.lower() in (
                "package / case",
                "package",
                "case",
                "package/case",
                "footprint",
            ):
                footprint = value
        specs: list[dict[str, str]] = [
            {"key": k, "value": " / ".join(specs_by_key[k])} for k in spec_order
        ]

        return {
            "found": True,
            "result": {
                "mpn": p.get("ManufacturerPartNumber") or mpn,
                "manufacturer": p.get("Manufacturer") or None,
                "description": p.get("Description") or None,
                "category": p.get("Category") or None,
                "footprint": footprint,
                "datasheet_url": p.get("DataSheetUrl") or None,
                "image_url": p.get("ImagePath") or None,
                "source_url": p.get("ProductDetailUrl") or "",
                "specs": specs,
            },
            "message": None,
        }
