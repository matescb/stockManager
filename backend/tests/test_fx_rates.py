from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.domain.fx import rates as fx_rates
from app.domain.fx.models import FxRateSnapshot

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-05-08">
      <Cube currency="USD" rate="1.25"/>
      <Cube currency="CZK" rate="25.00"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


def test_fetch_ecb_daily_rates_parses_xml(monkeypatch):
    monkeypatch.setattr(fx_rates, "_get_ecb_daily_xml", lambda: ECB_XML)

    parsed = fx_rates.fetch_ecb_daily_rates()

    assert parsed == {
        "EUR": Decimal("1"),
        "USD": Decimal("1.25"),
        "CZK": Decimal("25.00"),
    }


def test_get_or_fetch_today_caches_per_date(db, monkeypatch):
    calls = 0

    def fake_fetch():
        nonlocal calls
        calls += 1
        return {"EUR": Decimal("1"), "USD": Decimal("1.25")}

    monkeypatch.setattr(fx_rates, "fetch_ecb_daily_rates", fake_fetch)

    first = fx_rates.get_or_fetch_today(db, on_date=date(2026, 5, 8))
    second = fx_rates.get_or_fetch_today(db, on_date=date(2026, 5, 8))

    assert first == second
    assert calls == 1


def test_convert_uses_eur_cross_rate():
    converted = fx_rates.convert(
        Decimal("100"),
        from_currency="USD",
        to_currency="CZK",
        rates={
            "EUR": Decimal("1"),
            "USD": Decimal("1.25"),
            "CZK": Decimal("25"),
        },
    )

    assert converted == Decimal("2000.0000")


def test_convert_returns_none_for_unknown_currency():
    converted = fx_rates.convert(
        Decimal("100"),
        from_currency="USD",
        to_currency="GBP",
        rates={"EUR": Decimal("1"), "USD": Decimal("1.25")},
    )

    assert converted is None


def test_no_workspace_id_column_on_fx_table(engine):
    columns = inspect(engine).get_columns("fx_rate_snapshots")

    assert "workspace_id" not in {column["name"] for column in columns}


def test_unique_constraint_on_fetched_date(db):
    db.add(
        FxRateSnapshot(
            fetched_date=date(2026, 5, 8),
            rates={"EUR": "1", "USD": "1.25"},
        )
    )
    db.flush()
    db.add(
        FxRateSnapshot(
            fetched_date=date(2026, 5, 8),
            rates={"EUR": "1", "USD": "1.25"},
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

