"""Mouser Search API — MPN lookup."""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.domain.parts.providers.base import MpnLookupResult


_ENDPOINT = "https://api.mouser.com/api/v1/search/partnumber"
_TIMEOUT_SEC = 8.0  # Cap upstream wall-clock to prevent worker stall (BE2-011).


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
            err = errors[0]
            raw_msg = err.get("Message") or "Mouser returned an error"
            # Mouser surfaces a key-rejection as `Invalid unique identifier.`
            # with PropertyName="API Key" — useless to the operator. Translate
            # to something they can act on.
            prop = (err.get("PropertyName") or "").strip().lower()
            rkey = (err.get("ResourceKey") or "").strip().lower()
            if prop == "api key" or rkey == "invalidapikey" or rkey == "invalididentifier":
                return {
                    "found": False,
                    "result": None,
                    "message": (
                        "Mouser rejected the API key. Re-paste a valid key in "
                        "Settings → Workspace → Parts data provider."
                    ),
                }
            return {"found": False, "result": None, "message": raw_msg}

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

        # ---- Pricing — full ladder, one row per tier ----------------
        # Pricing is a snapshot — refresh-from-provider re-pulls; the
        # Mouser product link stays the source of truth for live prices.
        for tier in (p.get("PriceBreaks") or []):
            if not isinstance(tier, dict):
                continue
            price = (tier.get("Price") or "").strip()
            qty = tier.get("Quantity")
            if not price:
                continue
            label_qty = f"({qty}+)" if qty else ""
            key = f"Unit price {label_qty}".strip()
            if key not in specs_by_key:
                spec_order.append(key)
                specs_by_key[key] = [price]

        # ---- Identity (extra) ---------------------------------------
        mouser_pn = (p.get("MouserPartNumber") or "").strip()
        if mouser_pn and "Mouser P/N" not in specs_by_key:
            spec_order.append("Mouser P/N")
            specs_by_key["Mouser P/N"] = [mouser_pn]

        actual_mfr = (p.get("ActualMfrName") or "").strip()
        manufacturer = (p.get("Manufacturer") or "").strip()
        if (
            actual_mfr
            and actual_mfr.lower() != manufacturer.lower()
            and "Distributor mfr name" not in specs_by_key
        ):
            spec_order.append("Distributor mfr name")
            specs_by_key["Distributor mfr name"] = [actual_mfr]

        # ---- Compliance / regulatory --------------------------------
        for entry in (p.get("ProductCompliance") or []):
            if not isinstance(entry, dict):
                continue
            cname = (entry.get("ComplianceName") or "").strip()
            cval = (entry.get("ComplianceValue") or "").strip()
            if not cname or not cval:
                continue
            if cname in specs_by_key:
                # Don't shadow an existing row (e.g. RoHS already emitted
                # from ROHSStatus).
                continue
            spec_order.append(cname)
            specs_by_key[cname] = [cval]

        reach = p.get("REACH-SVHC") or []
        if isinstance(reach, list):
            reach_values = [s.strip() for s in reach if isinstance(s, str) and s.strip()]
            if reach_values and "REACH SVHC" not in specs_by_key:
                spec_order.append("REACH SVHC")
                specs_by_key["REACH SVHC"] = [", ".join(reach_values)]

        # ---- Lifecycle / supply (extra) -----------------------------
        is_disc = p.get("IsDiscontinued")
        # Mouser sends this as a string ("True"/"False") or sometimes a bool.
        if isinstance(is_disc, str):
            disc_bool = is_disc.strip().lower() in {"true", "yes", "1"}
        else:
            disc_bool = bool(is_disc)
        if disc_bool and "Discontinued" not in specs_by_key:
            spec_order.append("Discontinued")
            specs_by_key["Discontinued"] = ["yes"]

        for mouser_key, our_key in (
            ("SuggestedReplacement", "Suggested replacement"),
            ("RestrictionMessage",   "Restriction"),
            ("IPCCode",              "IPC code"),
        ):
            v = p.get(mouser_key)
            if not isinstance(v, str):
                continue
            v = v.strip()
            if not v or v.lower() in {"n/a", "none"}:
                continue
            if our_key not in specs_by_key:
                spec_order.append(our_key)
                specs_by_key[our_key] = [v]

        # AvailabilityInStock / AvailableOnOrder are int-flavoured; surface
        # only when they parse to a positive integer so they don't duplicate
        # the human-readable `Availability` string ("5,000 In Stock").
        for mouser_key, our_key in (
            ("AvailabilityInStock", "In stock (qty)"),
            ("AvailableOnOrder",    "On order (qty)"),
            ("SalesMaximumOrderQty", "Max order qty"),
        ):
            v = p.get(mouser_key)
            try:
                ival = int(str(v).replace(",", "").strip()) if v not in (None, "") else 0
            except (TypeError, ValueError):
                ival = 0
            if ival > 0 and our_key not in specs_by_key:
                spec_order.append(our_key)
                specs_by_key[our_key] = [str(ival)]

        alt = p.get("AlternatePackagings") or []
        alt_pns = [
            (a.get("APMfrPN") or "").strip()
            for a in alt
            if isinstance(a, dict) and (a.get("APMfrPN") or "").strip()
        ]
        if alt_pns and "Alternate packagings" not in specs_by_key:
            spec_order.append("Alternate packagings")
            specs_by_key["Alternate packagings"] = [" / ".join(dict.fromkeys(alt_pns))]

        # ---- Physical -----------------------------------------------
        weight_obj = p.get("UnitWeightKg")
        if isinstance(weight_obj, dict):
            w = weight_obj.get("UnitWeight")
            try:
                w_float = float(w) if w is not None else 0.0
            except (TypeError, ValueError):
                w_float = 0.0
            if w_float > 0 and "Unit weight" not in specs_by_key:
                # Trim trailing zeros for display: 0.0001 not 0.000100
                w_str = ("%.6f" % w_float).rstrip("0").rstrip(".")
                spec_order.append("Unit weight")
                specs_by_key["Unit weight"] = [f"{w_str} kg"]

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
