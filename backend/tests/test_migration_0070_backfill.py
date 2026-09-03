"""Migration 0070's backfill — what it writes, and what it must not.

Rather than hand-seed legacy state in SQL, this builds it through the
app — a workspace with DigiKey primary credentials and a refreshed,
provider-linked part — then walks the chain DOWN to 0069 (dropping both
new tables and everything in them) and back UP to 0070. What comes out
of the second upgrade is purely the migration's work, which is exactly
the deploy-day path: existing prod rows, new tables.

`part_provider_links` IS backfilled. `workspace_provider_credentials`
is deliberately NOT: it holds secondaries only, and copying the primary's
key into it would give one provider two credential stores that nothing
keeps in sync (and would re-arm a provider the workspace had switched
off). The second test here is that omission's regression net — it is not
a gap.

`real_db` because the migration runs on its own connection and has to
see committed data; the marker also gets the schema reset around the
test so the downgrade can't leak into anything else. `slow` because each
test costs two schema resets and three walks of the alembic chain —
these run in CI's dedicated `-m slow` step, alongside `test_migrations.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.core.config import settings
from app.main import app
from tests._factories import signup_user

pytestmark = [pytest.mark.real_db, pytest.mark.slow]

MPN = "RC0402JR-070R"
_BACKEND_ROOT = Path(__file__).resolve().parents[1]

_DIGIKEY_PRODUCT = {
    "ManufacturerProductNumber": MPN,
    "Manufacturer": {"Name": "DIGIKEY-MFR"},
    "Description": {"ProductDescription": "DigiKey description"},
    "Category": {"Name": "Resistors"},
    "ProductUrl": "https://www.digikey.com/p/1",
    "Parameters": [{"ParameterText": "Resistance", "ValueText": "0 Ohms"}],
}


def _alembic_cfg() -> AlembicConfig:
    cfg = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings().DATABASE_URL)
    return cfg


def test_0070_backfills_links_from_legacy_state(db, monkeypatch):
    client = TestClient(app)
    signup_user(client)

    r = client.patch(
        "/api/workspaces/current",
        json={
            "parts_provider": "digikey",
            "parts_provider_api_key": "legacy-client-id",
            "parts_provider_api_secret": "legacy-client-secret",
        },
    )
    assert r.status_code == 200, r.text

    part_id = client.post(
        "/api/parts", json={"name": "Resistor", "part_type": "linked", "mpn": MPN}
    ).json()["data"]["id"]

    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token",
        lambda client_id, client_secret: {"access_token": "tok", "expires_in": 600},
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (200, {"Product": _DIGIKEY_PRODUCT}),
    )
    r = client.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 200, r.text

    db.commit()

    # Down to 0069: both tables and every row in them are gone.
    command.downgrade(_alembic_cfg(), "0069")
    assert not db.execute(
        text("SELECT to_regclass('public.part_provider_links')")
    ).scalar_one()

    # ...and back up. Everything below is the backfill's output.
    command.upgrade(_alembic_cfg(), "0070")

    link = db.execute(
        text(
            "SELECT provider, external_id, last_refresh_at, source_url"
            " FROM part_provider_links WHERE part_id = :p"
        ),
        {"p": part_id},
    ).one()
    assert link.provider == "digikey"
    assert link.external_id == MPN
    assert link.last_refresh_at is not None
    # source_url has no legacy column to come from; the next refresh fills it.
    assert link.source_url is None

    # The backfilled state is live, not just present: the API reads it.
    detail = client.get(f"/api/parts/{part_id}").json()["data"]
    assert [link["provider"] for link in detail["provider_links"]] == ["digikey"]


def test_0070_backfills_no_credentials_for_the_primary(db, monkeypatch):
    """The credentials table must come up EMPTY even for a workspace with
    primary credentials already stored.

    Copying the key here would leave two stores for one provider —
    clearing either reports success while the other keeps authenticating
    — and would re-arm a provider the workspace later switched off, since
    `PATCH /current` never clears the legacy columns.
    """
    client = TestClient(app)
    signup_user(client)
    r = client.patch(
        "/api/workspaces/current",
        json={
            "parts_provider": "digikey",
            "parts_provider_api_key": "legacy-client-id",
            "parts_provider_api_secret": "legacy-client-secret",
        },
    )
    assert r.status_code == 200, r.text
    db.commit()

    command.downgrade(_alembic_cfg(), "0069")
    command.upgrade(_alembic_cfg(), "0070")

    count = db.execute(
        text("SELECT count(*) FROM workspace_provider_credentials"),
    ).scalar_one()
    assert count == 0
    assert (
        client.get("/api/workspaces/current").json()["data"]["provider_credentials"] == []
    )

    # The primary still resolves — through the legacy columns, which is
    # the only path it has ever had.
    part_id = client.post(
        "/api/parts", json={"name": "Resistor", "part_type": "linked", "mpn": MPN}
    ).json()["data"]["id"]
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token",
        lambda client_id, client_secret: {"access_token": "tok", "expires_in": 600},
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (200, {"Product": _DIGIKEY_PRODUCT}),
    )
    r = client.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["found"] is True
