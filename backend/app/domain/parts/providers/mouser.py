"""Mouser Search API — MPN lookup."""
from __future__ import annotations

import re
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


# ---------------------------------------------------------------------------
# Description → spec inference
#
# Mouser's `ProductAttributes` is mostly packaging info; the actual
# parametric values (resistance, tolerance, power, etc.) live in
# `Description` as comma-separated text:
#
#   "Thick Film Resistors - SMD General Purpose Chip Resistor 0402, 0Ohms, 5%, 1/16W"
#
# We split on commas (after stripping the category prefix) and try to
# label each token using simple unit-aware regexes. Tokens that don't
# match a known pattern are skipped — better to leave a row out than to
# write garbage. The user can always add specs manually on the Specs tab.
# ---------------------------------------------------------------------------

# Each pattern matches a whole token; the matched token itself becomes
# the spec value. Order matters — more specific patterns first.
_TOKEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\d+(?:\.\d+)?\s*[kKmMuµμnNpP]?\s*(?:Ohms?|Ω)$", re.IGNORECASE), "Resistance"),
    (re.compile(r"^\d+(?:\.\d+)?\s*[pPnNuµμmM]?F$"), "Capacitance"),
    (re.compile(r"^\d+(?:\.\d+)?\s*[pPnNuµμmM]?H$"), "Inductance"),
    (re.compile(r"^\d+(?:\.\d+)?\s*V(?:DC|AC)?$", re.IGNORECASE), "Voltage"),
    (re.compile(r"^\d+(?:\.\d+)?\s*[mMuµμ]?A$"), "Current"),
    (re.compile(r"^\d+(?:[/.]\d+)?\s*[mMkK]?W$"), "Power"),
    (re.compile(r"^\d+(?:\.\d+)?\s*[kKmMgG]?Hz$"), "Frequency"),
    (re.compile(r"^±?\s*\d+(?:\.\d+)?\s*%$"), "Tolerance"),
]

# Embedded package-code recogniser (used in addition to the per-token regexes
# because the package usually appears inside a longer descriptive token like
# "SMD General Purpose Chip Resistor 0402").
_PACKAGE_RE = re.compile(
    r"\b("
    r"\d{4}"                         # 0402, 0603, 0805, 1206, 2010, 2512, …
    r"|SOIC[- ]?\d+"
    r"|SOP[- ]?\d+"
    r"|TSSOP[- ]?\d+"
    r"|SSOP[- ]?\d+"
    r"|QFN[- ]?\d+"
    r"|TQFP[- ]?\d+"
    r"|LQFP[- ]?\d+"
    r"|SOT[- ]?\d+(?:-\d+)?"
    r"|DIP[- ]?\d+"
    r"|BGA[- ]?\d+"
    r")\b",
    re.IGNORECASE,
)


def parse_description_specs(description: str | None) -> list[tuple[str, str]]:
    """Best-effort extraction of (key, value) pairs from a Mouser
    description string. Returns an ordered list, deduped by key. Skips
    tokens that don't match a known pattern."""
    if not description:
        return []
    # Strip category prefix: everything before the first " - ".
    body = description.split(" - ", 1)[-1]
    # Split on commas + semicolons.
    tokens = [t.strip() for t in re.split(r"[,;]", body) if t.strip()]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for token in tokens:
        # 1) embedded package code
        if "Package" not in seen:
            m = _PACKAGE_RE.search(token)
            if m:
                out.append(("Package", m.group(1).upper().replace(" ", "-")))
                seen.add("Package")
        # 2) labelled patterns over the whole token
        for pattern, label in _TOKEN_PATTERNS:
            if label in seen:
                continue
            if pattern.match(token):
                out.append((label, token))
                seen.add(label)
                break
    return out


# Mouser fields beyond ProductAttributes that we surface as provider
# specs. These come back reliably and round out the part profile.
_DIRECT_FIELDS: list[tuple[str, str]] = [
    # (Mouser key in `Parts[0]`, our spec key)
    ("LifecycleStatus", "Lifecycle"),
    ("ROHSStatus",      "RoHS"),
    ("Availability",    "Availability"),
    ("LeadTime",        "Lead time"),
]


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
            msg = errors[0].get("Message") or "Mouser returned an error"
            return {"found": False, "result": None, "message": msg}

        parts = (data.get("SearchResults") or {}).get("Parts") or []
        if not parts:
            return {"found": False, "result": None, "message": "no match for MPN"}

        # Take the first match — for partial-match search this is the
        # closest stocked variant.
        p = parts[0]

        # ---- ProductAttributes (often just packaging) ----------------
        # Mouser frequently emits duplicate keys (three "Packaging" rows
        # for cut-tape / reel / MouseReel). Collapse them into a single
        # row joined by " / " — custom_fields has a UNIQUE (workspace,
        # object, key) constraint and the user only cares about the union.
        raw_attrs = p.get("ProductAttributes") or []
        specs_by_key: dict[str, list[str]] = {}
        spec_order: list[str] = []
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
            if footprint is None and name.lower() in (
                "package / case", "package", "case", "package/case", "footprint",
            ):
                footprint = value

        # ---- Description-inferred specs -------------------------------
        # ProductAttributes is sparse on most categories; the actual
        # parametric values live in Description. Extract what we can.
        description = (p.get("Description") or "").strip() or None
        for key, value in parse_description_specs(description):
            if key not in specs_by_key:
                spec_order.append(key)
                specs_by_key[key] = [value]
                if key == "Package" and footprint is None:
                    footprint = value
            elif value not in specs_by_key[key]:
                specs_by_key[key].append(value)

        # ---- Additional Mouser fields that are reliably populated -----
        for mouser_key, our_key in _DIRECT_FIELDS:
            v = p.get(mouser_key)
            if not isinstance(v, str):
                continue
            v = v.strip()
            if not v or v.lower() in {"", "n/a", "none"}:
                continue
            if our_key not in specs_by_key:
                spec_order.append(our_key)
                specs_by_key[our_key] = [v]

        # MOQ / multiples are integers. Show only when they're non-trivial:
        # MOQ=1 / Mult=1 are the boring defaults that just bloat the table.
        moq = p.get("Min")
        try:
            moq_int = int(moq) if moq is not None else 0
        except (TypeError, ValueError):
            moq_int = 0
        if moq_int > 1 and "MOQ" not in specs_by_key:
            spec_order.append("MOQ")
            specs_by_key["MOQ"] = [str(moq_int)]

        mult = p.get("Mult")
        try:
            mult_int = int(mult) if mult is not None else 0
        except (TypeError, ValueError):
            mult_int = 0
        if mult_int > 1 and "Order multiple" not in specs_by_key:
            spec_order.append("Order multiple")
            specs_by_key["Order multiple"] = [str(mult_int)]

        # Lowest unit price across price breaks (informational; treated as
        # a snapshot — pricing isn't kept fresh, the link to the Mouser
        # page is the source of truth for live prices).
        price_breaks = p.get("PriceBreaks") or []
        if price_breaks and "Unit price (1+)" not in specs_by_key:
            first = price_breaks[0]
            price = (first.get("Price") or "").strip() if isinstance(first, dict) else ""
            qty = first.get("Quantity") if isinstance(first, dict) else None
            if price:
                label_qty = f"({qty}+)" if qty else ""
                key = f"Unit price {label_qty}".strip()
                spec_order.append(key)
                specs_by_key[key] = [price]

        specs: list[dict[str, str]] = [
            {"key": k, "value": " / ".join(specs_by_key[k])} for k in spec_order
        ]

        return {
            "found": True,
            "result": {
                "mpn": p.get("ManufacturerPartNumber") or mpn,
                "manufacturer": p.get("Manufacturer") or None,
                "description": description,
                "category": p.get("Category") or None,
                "footprint": footprint,
                "datasheet_url": p.get("DataSheetUrl") or None,
                "image_url": p.get("ImagePath") or None,
                "source_url": p.get("ProductDetailUrl") or "",
                "specs": specs,
            },
            "message": None,
        }
