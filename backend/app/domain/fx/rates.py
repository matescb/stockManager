"""ECB daily FX rate fetch and conversion helpers."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Mapping
from xml.etree import ElementTree

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.fx.models import FxRateSnapshot

ECB_DAILY_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_TIMEOUT_SECONDS = 8.0
EUR = "EUR"
MIN_QUANT = Decimal("0.0001")


class FxRateError(Exception):
    """Base error for ECB reference-rate failures."""


class FxRateFetchError(FxRateError):
    """ECB daily XML could not be fetched."""


class FxRateParseError(FxRateError):
    """ECB daily XML could not be parsed into rates."""


def _get_ecb_daily_xml() -> str:
    """Network seam for tests."""
    try:
        with httpx.Client(timeout=ECB_TIMEOUT_SECONDS) as client:
            response = client.get(ECB_DAILY_RATES_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FxRateFetchError("ECB daily rates fetch failed") from exc
    return response.text


def fetch_ecb_daily_rates() -> dict[str, Decimal]:
    """Fetch and parse today's ECB daily reference rates.

    The returned rates are base-EUR: 1 EUR equals ``rates[code]`` units of
    that currency. EUR is always included as ``Decimal("1")``.
    """
    xml_text = _get_ecb_daily_xml()
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise FxRateParseError("ECB daily rates XML is invalid") from exc

    rates: dict[str, Decimal] = {EUR: Decimal("1")}
    for element in root.iter():
        currency = element.attrib.get("currency")
        raw_rate = element.attrib.get("rate")
        if not currency or not raw_rate:
            continue
        code = currency.strip().upper()
        try:
            rates[code] = Decimal(raw_rate)
        except InvalidOperation as exc:
            raise FxRateParseError(f"ECB daily rate for {code} is invalid") from exc

    if len(rates) == 1:
        raise FxRateParseError("ECB daily rates XML contained no currency rates")
    return rates


def get_or_fetch_today(db: Session, *, on_date: date | None = None) -> dict[str, Decimal]:
    """Return the cached ECB snapshot for one UTC date, fetching on miss."""
    fetched_date = on_date or utcnow().date()
    existing = db.execute(
        select(FxRateSnapshot).where(FxRateSnapshot.fetched_date == fetched_date)
    ).scalar_one_or_none()
    if existing is not None:
        return _deserialize_rates(existing.rates)

    rates = fetch_ecb_daily_rates()
    db.add(
        FxRateSnapshot(
            fetched_date=fetched_date,
            rates={code: str(rate) for code, rate in rates.items()},
        )
    )
    db.flush()
    return rates


def convert(
    amount: Decimal,
    *,
    from_currency: str,
    to_currency: str,
    rates: Mapping[str, Decimal | str],
    on_date: date | None = None,
) -> Decimal | None:
    """Convert ``amount`` across ECB base-EUR rates.

    ``on_date`` is accepted for call-site readability; the caller supplies the
    already-selected snapshot through ``rates``.
    """
    del on_date
    source = from_currency.strip().upper()
    target = to_currency.strip().upper()
    if not source or not target:
        return None

    normalized = _deserialize_rates(rates)
    source_rate = _rate_for(normalized, source)
    target_rate = _rate_for(normalized, target)
    if source_rate is None or target_rate is None:
        return None

    converted = (amount / source_rate) * target_rate
    return converted.quantize(MIN_QUANT, rounding=ROUND_HALF_UP)


def _rate_for(rates: Mapping[str, Decimal], currency: str) -> Decimal | None:
    if currency == EUR:
        return Decimal("1")
    return rates.get(currency)


def _deserialize_rates(raw_rates: Mapping[str, Decimal | str]) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {}
    for currency, raw_rate in raw_rates.items():
        code = currency.strip().upper()
        try:
            rates[code] = raw_rate if isinstance(raw_rate, Decimal) else Decimal(str(raw_rate))
        except InvalidOperation as exc:
            raise FxRateParseError(f"cached ECB daily rate for {code} is invalid") from exc
    rates.setdefault(EUR, Decimal("1"))
    return rates

