"""Migration 0080's backfill — which rows it rewrites, and which it leaves.

The legacy state this fixes can no longer be produced through the app
(`domain/parts/part_type.py` now syncs the column at every transition),
so the drift is written here with raw SQL against a part the API built:
that is exactly what the 160 affected prod rows look like.

Then the chain walks down to 0079 and back up to 0080, so what is
asserted is purely the migration's work — the deploy-day path.

`real_db` because the migration runs on its own connection and has to
see committed data; `slow` because the test walks the alembic chain
three times (CI runs it in the dedicated `-m slow` step).
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


def _mouser_echo(_url: str, body: dict) -> dict:
    """A one-hit Mouser response that echoes the MPN it was asked about.

    A fixed MPN would make the primary refresh rewrite every part's
    `mpn` to the same value and trip `uq_parts_ws_mpn`.
    """
    asked = body["SearchByPartRequest"]["mouserPartNumber"]
    return {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "Manufacturer": "YAGEO",
                    "ManufacturerPartNumber": asked,
                    "Description": "0R 0402",
                    "ProductDetailUrl": "https://www.mouser.com/p/1",
                }
            ],
        },
    }


def _alembic_cfg() -> AlembicConfig:
    cfg = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings().DATABASE_URL)
    return cfg


def _part_type(db, part_id: str) -> str:
    return db.execute(
        text("SELECT part_type FROM parts WHERE id = :p"), {"p": part_id}
    ).scalar_one()


def test_0080_promotes_only_linked_parts_that_say_local(db, monkeypatch):
    client = TestClient(app)
    signup_user(client)
    r = client.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        _mouser_echo,
    )

    def _create(name: str, part_type: str, mpn: str | None) -> str:
        resp = client.post(
            "/api/parts", json={"name": name, "part_type": part_type, "mpn": mpn}
        )
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["data"]["id"]

    drifted = _create("drifted", "linked", MPN)
    already_right = _create("already right", "linked", "CC0402-1")
    manual = _create("manual", "local", None)
    meta = _create("meta group", "meta", "CC0402-2")
    # Created `linked`, never refreshed — so no provider backs it. The
    # migration deliberately leaves this bucket alone.
    never_linked = _create("never looked up", "linked", "CC0402-3")
    # Nothing writes a blank provider today, but the column is nullable
    # TEXT. The predicate must read blank as unlinked, the way
    # `part_type.py::sync_part_type_with_link` does.
    blank_provider = _create("blank provider", "local", "CC0402-4")

    for part_id in (drifted, already_right, meta):
        assert client.post(f"/api/parts/{part_id}/refresh-from-provider").status_code == 200

    # The drift: a linked part whose type column was never maintained.
    # `meta` gets the same treatment so the migration's restraint is
    # tested against a row that also carries a provider.
    db.execute(
        text("UPDATE parts SET part_type = 'local' WHERE id = :p"), {"p": drifted}
    )
    db.execute(
        text("UPDATE parts SET linked_provider = '  ' WHERE id = :p"),
        {"p": blank_provider},
    )
    db.commit()

    assert _part_type(db, drifted) == "local"
    assert _part_type(db, meta) == "meta"

    command.downgrade(_alembic_cfg(), "0079")
    command.upgrade(_alembic_cfg(), "0080")

    # The bug: promoted.
    assert _part_type(db, drifted) == "linked"
    # Everything else: untouched. A manual part stays manual, a correct
    # row stays correct, and a declared role is never rewritten even
    # though it carries a provider link.
    assert _part_type(db, already_right) == "linked"
    assert _part_type(db, manual) == "local"
    assert _part_type(db, meta) == "meta"
    assert _part_type(db, never_linked) == "linked"
    assert _part_type(db, blank_provider) == "local"

    # Raw SQL above reads 0080's schema; the ORM below describes `head`.
    # Finish the chain before using the API, or a later migration's new
    # column breaks every ORM SELECT here.
    command.upgrade(_alembic_cfg(), "head")
    assert client.get(f"/api/parts/{drifted}").json()["data"]["part_type"] == "linked"


def test_0080_downgrade_is_a_no_op(db, monkeypatch):
    """Downgrading must not undo the correction.

    The pre-0080 values were wrong and unrecorded; an unconditional
    reverse UPDATE would demote the parts that were right all along.
    """
    client = TestClient(app)
    signup_user(client)
    r = client.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        _mouser_echo,
    )
    resp = client.post(
        "/api/parts", json={"name": "linked part", "part_type": "linked", "mpn": MPN}
    )
    assert resp.status_code in (200, 201), resp.text
    part_id = resp.json()["data"]["id"]
    assert client.post(f"/api/parts/{part_id}/refresh-from-provider").status_code == 200
    db.commit()

    command.downgrade(_alembic_cfg(), "0079")

    assert _part_type(db, part_id) == "linked"

    command.upgrade(_alembic_cfg(), "head")
