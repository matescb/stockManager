"""`parts.part_type` follows the provider link, at every transition.

`part_type` used to be written once, at creation, and never again: the
refresh route set `linked_provider` and the unlink PATCH cleared it
without either touching the column. Prod ended up with 160 of 324 parts
reading `local` while carrying a provider link, and the UI pill renders
the raw column — so provider-backed parts advertised themselves as
manual ones.

`domain/parts/part_type.py` is the one place that decides. These tests
pin the four directions it can go plus the two it must refuse: `meta`
and `sub_assembly` are user-declared roles, not derived state, and no
link event may rewrite them.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.domain.parts.models import Part
from app.domain.parts.part_type import sync_part_type_with_link
from app.main import app
from tests._factories import signup_user

MPN = "RC0402JR-070R"

_FAKE_MOUSER_PART = {
    "Manufacturer": "YAGEO",
    "ManufacturerPartNumber": MPN,
    "Description": "0R 0402",
    "Category": "Resistors",
    "ProductDetailUrl": "https://www.mouser.com/p/1",
    "ProductAttributes": [
        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
    ],
}


def _mouser_payload() -> dict:
    return {
        "Errors": [],
        "SearchResults": {"NumberOfResult": 1, "Parts": [_FAKE_MOUSER_PART]},
    }


def _stub_mouser(monkeypatch) -> None:
    payload = _mouser_payload()
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, body: payload,
    )


def _enable_mouser_primary(c: TestClient) -> None:
    r = c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    assert r.status_code == 200, r.text


def _configure_digikey_secondary(c: TestClient) -> None:
    r = c.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "digikey", "api_key": "id", "api_secret": "secret"},
    )
    assert r.status_code == 200, r.text


def _create_part(c: TestClient, part_type: str, mpn: str | None = MPN) -> str:
    r = c.post(
        "/api/parts",
        json={"name": f"P-{uuid.uuid4().hex[:6]}", "part_type": part_type, "mpn": mpn},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _refresh(c: TestClient, part_id: str, query: str = "") -> dict:
    r = c.post(f"/api/parts/{part_id}/refresh-from-provider{query}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _part_type(c: TestClient, part_id: str) -> str:
    r = c.get(f"/api/parts/{part_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]["part_type"]


@pytest.fixture
def authed() -> TestClient:
    c = TestClient(app)
    signup_user(c)
    return c


# ---------------------------------------------------------------------------
# The helper itself — the only place the rule is expressed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("part_type", "linked_provider", "expected", "changed"),
    [
        ("local", "mouser", "linked", True),
        ("linked", None, "local", True),
        ("linked", "mouser", "linked", False),
        ("local", None, "local", False),
        # Declared roles. A link event describes where the DATA comes
        # from; it does not turn a meta-part into a normal one.
        ("meta", "mouser", "meta", False),
        ("meta", None, "meta", False),
        ("sub_assembly", "mouser", "sub_assembly", False),
        ("sub_assembly", None, "sub_assembly", False),
    ],
)
def test_sync_part_type_with_link(part_type, linked_provider, expected, changed):
    part = Part(part_type=part_type, linked_provider=linked_provider, name="P")

    assert sync_part_type_with_link(part) is changed
    assert part.part_type == expected


def test_sync_part_type_treats_blank_provider_as_unlinked():
    """An empty string is not a link. Nothing writes one today, but the
    column is nullable TEXT and the helper is the guard."""
    part = Part(part_type="linked", linked_provider="", name="P")

    assert sync_part_type_with_link(part) is True
    assert part.part_type == "local"


# ---------------------------------------------------------------------------
# Route transitions
# ---------------------------------------------------------------------------


def test_refresh_promotes_a_local_part_to_linked(authed, monkeypatch):
    _enable_mouser_primary(authed)
    _stub_mouser(monkeypatch)
    part_id = _create_part(authed, "local")

    data = _refresh(authed, part_id)

    assert data["found"] is True
    assert data["part"]["linked_provider"] == "mouser"
    # Both the echoed part and a fresh read — the column changed, not
    # just the serialization.
    assert data["part"]["part_type"] == "linked"
    assert _part_type(authed, part_id) == "linked"


def test_unlink_demotes_a_linked_part_to_local(authed, monkeypatch):
    _enable_mouser_primary(authed)
    _stub_mouser(monkeypatch)
    part_id = _create_part(authed, "linked")
    _refresh(authed, part_id)

    r = authed.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert r.status_code == 200, r.text

    assert r.json()["data"]["linked_provider"] is None
    assert r.json()["data"]["part_type"] == "local"
    assert _part_type(authed, part_id) == "local"


def test_create_local_then_link_then_unlink_round_trips(authed, monkeypatch):
    """The prod shape: a part created `local` (BOM auto-create, REST
    create with the default) that later gets linked."""
    _enable_mouser_primary(authed)
    _stub_mouser(monkeypatch)
    part_id = _create_part(authed, "local")
    assert _part_type(authed, part_id) == "local"

    _refresh(authed, part_id)
    assert _part_type(authed, part_id) == "linked"

    r = authed.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert r.status_code == 200, r.text
    assert _part_type(authed, part_id) == "local"


@pytest.mark.parametrize("declared", ["meta", "sub_assembly"])
def test_declared_part_types_survive_link_and_unlink(authed, monkeypatch, declared):
    _enable_mouser_primary(authed)
    _stub_mouser(monkeypatch)
    part_id = _create_part(authed, declared)

    data = _refresh(authed, part_id)
    assert data["part"]["linked_provider"] == "mouser"
    assert _part_type(authed, part_id) == declared

    r = authed.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert r.status_code == 200, r.text
    assert _part_type(authed, part_id) == declared


def test_secondary_refresh_leaves_part_type_alone(authed, monkeypatch):
    """A secondary provider writes no part column — including this one.

    `linked_provider` names the PRIMARY that owns the part's columns
    (ADR-0031). A DigiKey secondary refresh must not promote a part the
    primary has never seen.
    """
    _enable_mouser_primary(authed)
    _configure_digikey_secondary(authed)
    part_id = _create_part(authed, "local")

    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token",
        lambda client_id, client_secret: {"access_token": "tok", "expires_in": 600},
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (
            200,
            {
                "Product": {
                    "ManufacturerProductNumber": MPN,
                    "Manufacturer": {"Name": "YAGEO"},
                    "Description": {"ProductDescription": "0R 0402"},
                    "ProductUrl": "https://www.digikey.com/p/1",
                }
            },
        ),
    )
    data = _refresh(authed, part_id, "?provider=digikey")

    assert data["found"] is True
    assert data["part"]["linked_provider"] is None
    assert _part_type(authed, part_id) == "local"


def test_failed_lookup_does_not_promote(authed, monkeypatch):
    """No match means no link, so no promotion either."""
    _enable_mouser_primary(authed)
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, body: {"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}},
    )
    part_id = _create_part(authed, "local")

    data = _refresh(authed, part_id)

    assert data["found"] is False
    assert _part_type(authed, part_id) == "local"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _part_type_rows(db) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == "part.type_synced")
            .order_by(AuditLog.created_at)
        ).scalars()
    )


def _rows_for(db, action: str) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
        ).scalars()
    )


def test_part_type_transitions_are_audited(db, monkeypatch):
    client = TestClient(app)
    signup_user(client)
    _enable_mouser_primary(client)
    _stub_mouser(monkeypatch)
    part_id = _create_part(client, "local")

    _refresh(client, part_id)
    rows = _part_type_rows(db)
    assert [r.comment for r in rows] == ["part_type: local→linked"]
    assert rows[0].target_type == "part"
    assert rows[0].target_ids == [uuid.UUID(part_id)]
    assert rows[0].workspace_id is not None
    assert rows[0].user_id is not None

    # A second refresh changes nothing, so it writes nothing.
    _refresh(client, part_id)
    assert len(_part_type_rows(db)) == 1

    client.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert [r.comment for r in _part_type_rows(db)] == [
        "part_type: local→linked",
        "part_type: linked→local",
    ]


def test_the_sync_row_does_not_collide_with_the_patch_own_row(db, monkeypatch):
    """The unlink PATCH writes two rows, under two different actions.

    Readers across the suite pull "the latest row for this action" with
    `scalar_one()`. A derived change filed under `part.updated` would put
    two rows there for one request and break every one of them.
    """
    client = TestClient(app)
    signup_user(client)
    _enable_mouser_primary(client)
    _stub_mouser(monkeypatch)
    part_id = _create_part(client, "local")
    _refresh(client, part_id)

    before = len(_rows_for(db, "part.updated"))
    r = client.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert r.status_code == 200, r.text

    after = _rows_for(db, "part.updated")
    assert len(after) == before + 1
    assert after[-1].comment == "fields=unlink_provider"
    assert [row.comment for row in _part_type_rows(db)][-1] == "part_type: linked→local"


def test_dropping_a_secondary_link_leaves_part_type_alone(db, monkeypatch):
    """Unlinking a SECONDARY must not demote a primary-linked part.

    The delete-provider-link route never touches `p.linked_provider`
    (that is PATCH's job), so the type must not move either — ADR-0031.
    """
    client = TestClient(app)
    signup_user(client)
    _enable_mouser_primary(client)
    _configure_digikey_secondary(client)
    _stub_mouser(monkeypatch)
    part_id = _create_part(client, "local")
    _refresh(client, part_id)
    assert _part_type(client, part_id) == "linked"

    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token",
        lambda client_id, client_secret: {"access_token": "tok", "expires_in": 600},
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (
            200,
            {
                "Product": {
                    "ManufacturerProductNumber": MPN,
                    "Manufacturer": {"Name": "YAGEO"},
                    "Description": {"ProductDescription": "0R 0402"},
                    "ProductUrl": "https://www.digikey.com/p/1",
                }
            },
        ),
    )
    _refresh(client, part_id, "?provider=digikey")
    before = len(_part_type_rows(db))

    r = client.delete(f"/api/parts/{part_id}/provider-links/digikey")
    assert r.status_code == 200, r.text

    assert _part_type(client, part_id) == "linked"
    assert len(_part_type_rows(db)) == before


def test_no_audit_row_when_the_type_already_matches(db, monkeypatch):
    client = TestClient(app)
    signup_user(client)
    _enable_mouser_primary(client)
    _stub_mouser(monkeypatch)
    part_id = _create_part(client, "linked")

    _refresh(client, part_id)

    assert _part_type_rows(db) == []


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def test_refresh_cannot_promote_a_part_in_another_workspace(monkeypatch):
    owner = TestClient(app)
    signup_user(owner)
    _enable_mouser_primary(owner)
    _stub_mouser(monkeypatch)
    part_id = _create_part(owner, "local")

    intruder = TestClient(app)
    signup_user(intruder)
    _enable_mouser_primary(intruder)

    r = intruder.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 404, r.text

    assert _part_type(owner, part_id) == "local"


def test_unlink_cannot_demote_a_part_in_another_workspace(monkeypatch):
    owner = TestClient(app)
    signup_user(owner)
    _enable_mouser_primary(owner)
    _stub_mouser(monkeypatch)
    part_id = _create_part(owner, "local")
    _refresh(owner, part_id)
    assert _part_type(owner, part_id) == "linked"

    intruder = TestClient(app)
    signup_user(intruder)

    r = intruder.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert r.status_code == 404, r.text

    assert _part_type(owner, part_id) == "linked"
