"""Stored price breaks as structured data (edaflow SM-017).

Providers store a part's price ladder as display strings, one custom-field
row per tier: DigiKey writes ``"Unit price (10+)" = "0.5040 CZK"``, Mouser
``"Unit price (10+)" = "0,017 €"`` (decimal comma). A secondary provider
writes the same key under its namespace (``"digikey:Unit price (10+)"``,
ADR-0031). This module reads those rows back as numbers so the REST record
can carry ``prices: [{provider, source, currency, breaks, refreshed_at}]``.

Stored rows only: nothing here calls a provider, refreshes or converts.

Grouping. One entry per (namespace, provider, currency):

* ``source="primary"`` — un-namespaced rows. Provider is the row's own
  ``provider`` stamp, else the part's ``linked_provider`` (rows written
  before alembic 0081 carry no stamp). ``refreshed_at`` is
  ``parts.last_refresh_at``.
* ``source="secondary"`` — ``"{provider}:"`` rows. ``refreshed_at`` is that
  provider's ``part_provider_links.last_refresh_at``.

Either falls back to the newest row ``updated_at`` when the refresh stamp is
missing. A value the parser cannot read is counted in ``unparsed`` rather
than guessed at — a wrong number would be silently wrong.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part, PartProviderLink
from app.domain.parts.provider_fields import KNOWN_PROVIDER_NAMES

__all__ = ["parse_price", "stored_prices_for_parts"]

_KEY = re.compile(
    r"^(?:(?P<ns>" + "|".join(KNOWN_PROVIDER_NAMES) + r"):)?Unit price \((?P<qty>\d+)\+\)$"
)

#: Currency marks seen in stored values, mapped to ISO 4217.
_CURRENCY = {
    "CZK": "CZK", "KČ": "CZK",
    "EUR": "EUR", "€": "EUR",
    "USD": "USD", "$": "USD",
    "GBP": "GBP", "£": "GBP",
}

_VALUE = re.compile(
    r"^\s*(?:(?P<pre>[€$£])\s*)?(?P<num>\d+(?:[.,]\d+)?)\s*(?P<post>[A-Za-zČč€$£]+)?\s*$"
)


def parse_price(value: str | None) -> tuple[Decimal, str] | None:
    """`"0,017 €"` -> `(Decimal("0.017"), "EUR")`; None when not exactly readable.

    One decimal separator at most, so a thousands-grouped value
    (`"1.234,50 €"`) is refused, not misread.
    """
    m = _VALUE.match(value or "")
    if not m:
        return None
    pre, post = m.group("pre"), m.group("post")
    if bool(pre) == bool(post):
        return None
    currency = _CURRENCY.get((pre or post).upper())
    if currency is None:
        return None
    try:
        amount = Decimal(m.group("num").replace(",", "."))
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return amount, currency


def stored_prices_for_parts(db, ws_id, part_ids: Sequence) -> dict[Any, list[dict]]:
    """`part_id -> [price entry, ...]` in three queries; `[]` for a part with none."""
    out: dict[Any, list[dict]] = {pid: [] for pid in part_ids}
    if not part_ids:
        return out
    rows = db.execute(
        select(
            CustomField.object_id, CustomField.key, CustomField.value,
            CustomField.provider, CustomField.updated_at,
        ).where(
            CustomField.workspace_id == ws_id,
            CustomField.object_type == "part",
            CustomField.object_id.in_(part_ids),
            CustomField.archived_at.is_(None),
            CustomField.key.like("%Unit price (%+)"),
        )
    ).all()
    if not rows:
        return out

    parts = {
        r.id: r for r in db.execute(
            select(Part.id, Part.linked_provider, Part.last_refresh_at).where(
                Part.workspace_id == ws_id, Part.id.in_(part_ids),
            )
        ).all()
    }
    link_refresh = {
        (r.part_id, r.provider): r.last_refresh_at for r in db.execute(
            select(
                PartProviderLink.part_id, PartProviderLink.provider,
                PartProviderLink.last_refresh_at,
            ).where(
                PartProviderLink.workspace_id == ws_id,
                PartProviderLink.part_id.in_(part_ids),
                PartProviderLink.archived_at.is_(None),
            )
        ).all()
    }

    groups: dict[tuple, dict] = {}
    unparsed: dict[tuple, int] = {}
    for row in rows:
        m = _KEY.match(row.key or "")
        part = parts.get(row.object_id)
        if not m or part is None:
            continue
        ns = m.group("ns")
        source = "secondary" if ns else "primary"
        provider = ns or row.provider or part.linked_provider
        parsed = parse_price(row.value)
        if parsed is None:
            key = (row.object_id, source, provider)
            unparsed[key] = unparsed.get(key, 0) + 1
            continue
        amount, currency = parsed
        gkey = (row.object_id, source, provider, currency)
        g = groups.setdefault(gkey, {"breaks": {}, "newest_row": None})
        g["breaks"][int(m.group("qty"))] = amount
        if row.updated_at and (g["newest_row"] is None or row.updated_at > g["newest_row"]):
            g["newest_row"] = row.updated_at

    for (pid, source, provider, currency), g in groups.items():
        part = parts[pid]
        stamp = (
            part.last_refresh_at if source == "primary"
            else link_refresh.get((pid, provider))
        ) or g["newest_row"]
        out[pid].append({
            "provider": provider,
            "source": source,
            "currency": currency,
            "breaks": [
                {"min_qty": q, "unit_price": float(p)}
                for q, p in sorted(g["breaks"].items())
            ],
            "refreshed_at": stamp.astimezone(UTC).isoformat() if stamp else None,
            "unparsed": unparsed.pop((pid, source, provider), 0),
        })
    for (pid, source, provider), n in unparsed.items():
        out[pid].append({
            "provider": provider, "source": source, "currency": None,
            "breaks": [], "refreshed_at": None, "unparsed": n,
        })
    for entries in out.values():
        # Newest first; an entry with no stamp sorts last.
        entries.sort(key=lambda e: e["refreshed_at"] or "", reverse=True)
    return out
