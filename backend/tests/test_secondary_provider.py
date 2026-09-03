"""Second-provider integration: credentials, namespaced refresh, unlink.

The load-bearing property under test is NON-INTERFERENCE — a refresh
from one provider must leave every other provider's custom_field rows
exactly as it found them. Before the namespace scoping in
`parts_assets._reconcile_provider_fields`, the primary's "delete every
source='provider' row not in my payload" pass ate the secondary's rows
on every DigiKey refresh.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import DEFAULT_PASSWORD, signup_user

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MPN = "RC0402JR-070R"


def _signup(c: TestClient, email: str | None = None) -> str:
    return signup_user(c, email=email).json()["data"]["workspace_id"]


def _enable_digikey_primary(c: TestClient) -> None:
    """DigiKey as the workspace's PRIMARY provider (legacy columns)."""
    r = c.patch(
        "/api/workspaces/current",
        json={
            "parts_provider": "digikey",
            "parts_provider_api_key": "fake-client-id",
            "parts_provider_api_secret": "fake-client-secret",
        },
    )
    assert r.status_code == 200, r.text


def _configure_mouser_secondary(c: TestClient, key: str = "fake-mouser-key") -> None:
    """Mouser as a SECONDARY provider (workspace_provider_credentials)."""
    r = c.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "mouser", "api_key": key},
    )
    assert r.status_code == 200, r.text


def _create_part(c: TestClient, name: str = "Resistor", mpn: str | None = MPN) -> str:
    r = c.post(
        "/api/parts",
        json={"name": name, "part_type": "linked" if mpn else "local", "mpn": mpn},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _fields(c: TestClient, part_id: str) -> dict[str, dict]:
    rows = c.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    return {row["key"]: row for row in rows}


@pytest.fixture
def authed() -> TestClient:
    c = TestClient(app)
    _signup(c)
    return c


# ---- provider stubs (no network) ------------------------------------------

_MOUSER_PART = {
    "Manufacturer": "MOUSER-MFR",
    "ManufacturerPartNumber": MPN,
    "Description": "Mouser description",
    "Category": "Resistors",
    "DataSheetUrl": "https://example.com/mouser-ds.pdf",
    "ImagePath": "https://example.com/mouser.jpg",
    "ProductDetailUrl": "https://www.mouser.com/p/1",
    "ProductAttributes": [
        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
        {"AttributeName": "Tolerance", "AttributeValue": "5 %"},
    ],
}

_DIGIKEY_PRODUCT = {
    "ManufacturerProductNumber": MPN,
    "Manufacturer": {"Name": "DIGIKEY-MFR"},
    "Description": {"ProductDescription": "DigiKey description"},
    "Category": {"Name": "Resistors"},
    "DatasheetUrl": "https://example.com/dk-ds.pdf",
    "PhotoUrl": "https://example.com/dk.jpg",
    "ProductUrl": "https://www.digikey.com/p/1",
    "Parameters": [
        {"ParameterText": "Resistance", "ValueText": "0 Ohms"},
        {"ParameterText": "Package / Case", "ValueText": "0402"},
    ],
}


def mouser_response(part: dict | None = None) -> dict:
    """A one-hit Mouser search payload. Public so the audit-coverage
    meta-test can reuse it rather than re-deriving the shape."""
    return {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [part if part is not None else _MOUSER_PART],
        },
    }


def _stub_mouser(monkeypatch, part: dict | None = None) -> None:
    payload = mouser_response(part)
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, body: payload,
    )


def _stub_mouser_no_match(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, body: {"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}},
    )


def _stub_digikey(monkeypatch, product: dict | None = None) -> None:
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token",
        lambda client_id, client_secret: {"access_token": "tok", "expires_in": 600},
    )
    body = {"Product": product if product is not None else _DIGIKEY_PRODUCT}
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (200, body),
    )


# ---------------------------------------------------------------------------
# credentials_for — resolution order
# ---------------------------------------------------------------------------


def _workspace(db, ws_id: str):
    from app.domain.workspaces.models import Workspace

    return db.get(Workspace, uuid.UUID(ws_id))


def test_credentials_for_prefers_the_credentials_row(authed, db):
    from app.domain.parts.provider_credentials import credentials_for

    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed, "mouser-row-key")
    ws_id = authed.get("/api/workspaces/current").json()["data"]["id"]
    ws = _workspace(db, ws_id)

    assert credentials_for(db, ws, "mouser") == ("mouser-row-key", None)


def test_credentials_for_falls_back_to_legacy_columns_for_the_primary(authed, db):
    """The primary is configured through PATCH /workspaces/current, which
    writes only the legacy columns — and migration 0070 backfills nothing
    into the credentials table, so the fallback is the ONLY way the
    primary resolves."""
    from app.domain.parts.provider_credentials import credentials_for

    _enable_digikey_primary(authed)
    ws_id = authed.get("/api/workspaces/current").json()["data"]["id"]
    ws = _workspace(db, ws_id)

    assert credentials_for(db, ws, "digikey") == ("fake-client-id", "fake-client-secret")


def test_credentials_for_returns_none_for_an_unconfigured_provider(authed, db):
    from app.domain.parts.provider_credentials import credentials_for

    _enable_digikey_primary(authed)
    ws_id = authed.get("/api/workspaces/current").json()["data"]["id"]
    ws = _workspace(db, ws_id)

    assert credentials_for(db, ws, "mouser") is None


def test_credentials_row_never_stores_plaintext(authed, db):
    from app.core.secrets import decrypt
    from app.domain.parts.models import WorkspaceProviderCredential

    plaintext = "MOUSER-PLAINTEXT-DEADBEEF"
    _configure_mouser_secondary(authed, plaintext)

    row = db.query(WorkspaceProviderCredential).filter_by(provider="mouser").one()
    assert row.api_key_encrypted != plaintext
    assert decrypt(row.api_key_encrypted) == plaintext


# ---------------------------------------------------------------------------
# PUT /api/workspaces/current/provider-credentials
# ---------------------------------------------------------------------------


def test_put_credentials_reports_presence_and_never_echoes_the_key(authed):
    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "mouser", "api_key": "super-secret-value"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["provider"] == "mouser"
    assert body["has_api_key"] is True
    assert body["has_api_secret"] is False
    assert "super-secret-value" not in r.text


def test_workspace_current_lists_provider_credentials(authed):
    _configure_mouser_secondary(authed)
    body = authed.get("/api/workspaces/current").json()["data"]
    entries = {e["provider"]: e for e in body["provider_credentials"]}
    assert entries["mouser"] == {
        "provider": "mouser",
        "has_api_key": True,
        "has_api_secret": False,
    }


def test_put_credentials_clearing_both_fields_retires_the_row(authed):
    _configure_mouser_secondary(authed)
    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "mouser", "api_key": "", "api_secret": ""},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["has_api_key"] is False
    body = authed.get("/api/workspaces/current").json()["data"]
    assert body["provider_credentials"] == []


def test_put_credentials_leaves_omitted_fields_alone(authed):
    authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "digikey", "api_key": "id", "api_secret": "secret"},
    )
    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "digikey", "api_key": "new-id"},
    )
    assert r.json()["data"] == {
        "provider": "digikey",
        "has_api_key": True,
        "has_api_secret": True,
        "provider_credentials": [
            {"provider": "digikey", "has_api_key": True, "has_api_secret": True}
        ],
    }


def test_put_credentials_refuses_the_primary_provider(authed):
    """The credentials table holds secondaries only. Storing the primary
    here would give one provider two credential stores that nothing keeps
    in sync."""
    _enable_digikey_primary(authed)

    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "digikey", "api_key": "second-store"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "workspace.provider_is_primary"
    assert r.json()["provider"] == "digikey"
    assert authed.get("/api/workspaces/current").json()["data"]["provider_credentials"] == []


def test_clearing_the_primary_through_the_secondary_route_cannot_report_success(
    authed, monkeypatch
):
    """TRAP: a PUT clearing the primary's key used to 200 while the legacy
    columns kept authenticating — an operator revoking a leaked key would
    have been told it worked. It must refuse, and nothing may change."""
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)

    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "digikey", "api_key": "", "api_secret": ""},
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "workspace.provider_is_primary"

    body = authed.get("/api/workspaces/current").json()["data"]
    assert body["has_parts_provider_api_key"] is True
    assert body["has_parts_provider_api_secret"] is True

    # And the primary still works — proving the refusal changed nothing.
    _stub_digikey(monkeypatch)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["found"] is True


def test_turning_the_primary_off_disarms_it_for_the_provider_query(authed, monkeypatch):
    """TRAP: backfilling the primary's key into the credentials table left
    a workspace that had switched `parts_provider` back to `none` still
    able to reach that provider via `?provider=` — the legacy columns are
    never cleared by the switch. With no backfill there is nothing to
    resolve."""
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)
    r = authed.patch("/api/workspaces/current", json={"parts_provider": "none"})
    assert r.status_code == 200, r.text
    # The legacy key is still sitting there — that's the whole hazard.
    assert authed.get("/api/workspaces/current").json()["data"][
        "has_parts_provider_api_key"
    ] is True

    _stub_digikey(monkeypatch)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=digikey")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "part.provider_not_configured"


def test_a_bare_workspace_has_no_credential_rows(authed, monkeypatch):
    """The literal shape of the same trap: `parts_provider="none"`, no
    rows, no key anywhere."""
    part_id = _create_part(authed)
    assert authed.get("/api/workspaces/current").json()["data"]["provider_credentials"] == []

    _stub_digikey(monkeypatch)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=digikey")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "part.provider_not_configured"


def test_put_credentials_rejects_an_unknown_provider(authed):
    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "octopart", "api_key": "x"},
    )
    assert r.status_code == 422, r.text


def test_put_credentials_rejects_a_body_with_no_credential_field(authed):
    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "mouser"},
    )
    assert r.status_code == 422, r.text


def test_put_credentials_rejects_extra_fields(authed):
    r = authed.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "mouser", "api_key": "x", "token": "y"},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Secondary refresh
# ---------------------------------------------------------------------------


def test_secondary_refresh_writes_namespaced_fields_and_a_link(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    _stub_mouser(monkeypatch)

    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is True
    assert body["provider"] == "mouser"
    assert body["link"]["provider"] == "mouser"
    assert body["link"]["source_url"] == "https://www.mouser.com/p/1"
    assert body["link"]["last_refresh_at"] is not None

    rows = _fields(authed, part_id)
    assert rows["mouser:Resistance"]["value"] == "0 Ohms"
    assert rows["mouser:Resistance"]["source"] == "provider"
    assert rows["mouser:source_url"]["value"] == "https://www.mouser.com/p/1"
    assert rows["mouser:datasheet_url"]["value"] == "https://example.com/mouser-ds.pdf"
    assert rows["mouser:category"]["value"] == "Resistors"
    # Un-namespaced keys belong to the primary — a secondary writes none.
    assert "Resistance" not in rows
    assert "source_url" not in rows


def test_secondary_refresh_never_touches_the_part_columns(authed, monkeypatch):
    """The whole point of the secondary tier: catalog data without letting
    a second vendor rewrite manufacturer/description/linked_provider."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    _stub_digikey(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    before = authed.get(f"/api/parts/{part_id}").json()["data"]
    assert before["manufacturer"] == "DIGIKEY-MFR"

    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")

    after = authed.get(f"/api/parts/{part_id}").json()["data"]
    assert after["manufacturer"] == "DIGIKEY-MFR"
    assert after["description"] == "DigiKey description"
    assert after["linked_provider"] == "digikey"
    assert after["last_refresh_at"] == before["last_refresh_at"]


def test_secondary_refresh_with_no_match_creates_no_link(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    _stub_mouser_no_match(monkeypatch)

    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is False
    assert body["provider"] == "mouser"
    assert "link" not in body

    detail = authed.get(f"/api/parts/{part_id}").json()["data"]
    assert detail["provider_links"] == []


def test_secondary_refresh_without_credentials_is_400(authed, monkeypatch):
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)
    _stub_mouser(monkeypatch)

    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "part.provider_not_configured"
    assert r.json()["provider"] == "mouser"


def test_refresh_with_an_unknown_provider_is_422(authed):
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)

    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=octopart")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "part.provider_unknown"


def test_refresh_naming_the_primary_explicitly_runs_the_primary_flow(authed, monkeypatch):
    """`?provider=digikey` when digikey IS the primary must not degrade to
    the namespaced secondary path."""
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)
    _stub_digikey(monkeypatch)

    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=digikey")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["part"]["manufacturer"] == "DIGIKEY-MFR"
    rows = _fields(authed, part_id)
    assert "Resistance" in rows
    assert "digikey:Resistance" not in rows


def test_primary_refresh_records_a_link_row(authed, monkeypatch):
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)
    _stub_digikey(monkeypatch)

    authed.post(f"/api/parts/{part_id}/refresh-from-provider")

    detail = authed.get(f"/api/parts/{part_id}").json()["data"]
    links = {link["provider"]: link for link in detail["provider_links"]}
    assert links["digikey"]["external_id"] == MPN
    assert links["digikey"]["source_url"] == "https://www.digikey.com/p/1"


# ---------------------------------------------------------------------------
# NON-INTERFERENCE — the pair this whole feature turns on
# ---------------------------------------------------------------------------


def test_primary_refresh_keeps_every_secondary_row(authed, monkeypatch):
    """A DigiKey refresh must not delete the `mouser:` rows. The primary
    reconciliation deletes provider-sourced rows absent from its payload;
    unscoped, that pass eats the entire secondary namespace."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)

    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    mouser_keys = {k for k in _fields(authed, part_id) if k.startswith("mouser:")}
    assert mouser_keys

    _stub_digikey(monkeypatch)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["summary"]["removed"] == 0

    after = _fields(authed, part_id)
    assert mouser_keys <= set(after)
    assert after["mouser:Resistance"]["value"] == "0 Ohms"
    # ...and the primary still wrote its own un-namespaced rows.
    assert after["Resistance"]["value"] == "0 Ohms"
    assert after["Package / Case"]["value"] == "0402"


def test_secondary_refresh_keeps_every_primary_row(authed, monkeypatch):
    """The mirror image: a Mouser refresh must not touch un-namespaced rows."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)

    _stub_digikey(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    primary_rows = {
        k: v["value"] for k, v in _fields(authed, part_id).items() if ":" not in k
    }
    assert primary_rows

    _stub_mouser(monkeypatch)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["summary"]["removed"] == 0

    after = _fields(authed, part_id)
    for key, value in primary_rows.items():
        assert after[key]["value"] == value


def test_a_secondary_refresh_prunes_only_its_own_stale_rows(authed, monkeypatch):
    """Second Mouser payload drops `Tolerance`; that row goes, and nothing
    outside `mouser:` moves."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)

    _stub_digikey(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert "mouser:Tolerance" in _fields(authed, part_id)

    slimmer = dict(_MOUSER_PART)
    slimmer["ProductAttributes"] = [
        {"AttributeName": "Resistance", "AttributeValue": "1 Ohm"}
    ]
    _stub_mouser(monkeypatch, slimmer)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.json()["data"]["summary"]["removed"] == 1

    after = _fields(authed, part_id)
    assert "mouser:Tolerance" not in after
    assert after["mouser:Resistance"]["value"] == "1 Ohm"
    assert after["Package / Case"]["value"] == "0402"


def test_secondary_refresh_leaves_a_manual_row_in_its_namespace_alone(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    r = authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "mouser:Resistance",
            "value": "hand-written",
        },
    )
    assert r.status_code in (200, 201), r.text

    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")

    row = _fields(authed, part_id)["mouser:Resistance"]
    assert row["source"] == "manual"
    assert row["value"] == "hand-written"


def test_an_overlong_upstream_key_is_skipped_not_a_500(authed, monkeypatch):
    """Namespacing adds `len(provider) + 1` chars to a field name we don't
    control. `custom_fields.key` is varchar(256), so without a cap a long
    enough ProductAttributes name is an uncaught DataError."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)

    # "mouser:" is 7 chars — 252 overflows by 3, 248 lands exactly on 255.
    too_long = "L" * 252
    just_fits = "F" * 248
    payload = dict(_MOUSER_PART)
    payload["ProductAttributes"] = [
        {"AttributeName": too_long, "AttributeValue": "dropped"},
        {"AttributeName": just_fits, "AttributeValue": "kept"},
        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
    ]
    _stub_mouser(monkeypatch, payload)

    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["summary"]["skipped"] == 1

    rows = _fields(authed, part_id)
    assert f"mouser:{too_long}" not in rows
    assert rows[f"mouser:{just_fits}"]["value"] == "kept"
    assert rows["mouser:Resistance"]["value"] == "0 Ohms"


def test_the_primary_path_reports_no_skips(authed, monkeypatch):
    """The primary writes bare keys, so the cap never applies to it."""
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)
    _stub_digikey(monkeypatch)

    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.json()["data"]["summary"]["skipped"] == 0


def test_a_colon_in_an_upstream_key_is_not_a_provider_namespace(authed, monkeypatch):
    """Only a KNOWN_PROVIDER_NAMES prefix marks a namespace. A genuine
    upstream spec called `Vref:max` stays the primary's, and a secondary
    refresh must not treat it as its own and delete it."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)

    product = dict(_DIGIKEY_PRODUCT)
    product["Parameters"] = [{"ParameterText": "Vref:max", "ValueText": "3.3 V"}]
    _stub_digikey(monkeypatch, product)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert _fields(authed, part_id)["Vref:max"]["value"] == "3.3 V"

    _stub_mouser(monkeypatch)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.json()["data"]["summary"]["removed"] == 0
    assert _fields(authed, part_id)["Vref:max"]["value"] == "3.3 V"


# ---------------------------------------------------------------------------
# DELETE /api/parts/{id}/provider-links/{provider}
# ---------------------------------------------------------------------------


def test_unlink_secondary_drops_its_link_and_fields_only(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    _stub_digikey(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")

    r = authed.delete(f"/api/parts/{part_id}/provider-links/mouser")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["provider"] == "mouser"
    assert body["removed_fields"] >= 3
    assert [link["provider"] for link in body["provider_links"]] == ["digikey"]

    after = _fields(authed, part_id)
    assert not [k for k in after if k.startswith("mouser:")]
    # Primary untouched.
    assert after["Resistance"]["value"] == "0 Ohms"
    detail = authed.get(f"/api/parts/{part_id}").json()["data"]
    assert detail["linked_provider"] == "digikey"
    assert [link["provider"] for link in detail["provider_links"]] == ["digikey"]


def test_unlink_secondary_demotes_overrides_to_manual(authed, monkeypatch):
    """A user-edited namespaced row is the user's work — it survives the
    unlink as a plain manual field rather than being deleted."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")

    # Re-POSTing a provider row with a new value is what promotes it to
    # `override` (custom_fields.create_or_update).
    r = authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "mouser:Resistance",
            "value": "my value",
        },
    )
    assert r.status_code in (200, 201), r.text
    assert _fields(authed, part_id)["mouser:Resistance"]["source"] == "override"

    authed.delete(f"/api/parts/{part_id}/provider-links/mouser")

    row = _fields(authed, part_id)["mouser:Resistance"]
    assert row["source"] == "manual"
    assert row["value"] == "my value"
    assert row["original_value"] is None


def test_unlink_refuses_the_primary_provider(authed, monkeypatch):
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)
    _stub_digikey(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")

    r = authed.delete(f"/api/parts/{part_id}/provider-links/digikey")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "part.provider_link_is_primary"
    # Nothing was removed.
    assert "Resistance" in _fields(authed, part_id)


def test_unlink_works_after_the_workspace_primary_moves_away(authed, monkeypatch):
    """The guard reads `ws.parts_provider`, not `p.linked_provider`.

    `linked_provider` is sticky per-part and survives an admin switching
    the workspace primary; keying the guard off it would permanently
    strand a link the workspace's own config now calls a secondary.
    """
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    # A user edit on one namespaced row, so the demotion path is covered too.
    authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "mouser:Resistance",
            "value": "my value",
        },
    )

    # Admin makes mouser the primary...
    r = authed.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "k"},
    )
    assert r.status_code == 200, r.text
    # ...while mouser IS the primary, the link is not unlinkable here.
    r = authed.delete(f"/api/parts/{part_id}/provider-links/mouser")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "part.provider_link_is_primary"

    # ...and back to digikey, which returns mouser to secondary status.
    r = authed.patch("/api/workspaces/current", json={"parts_provider": "digikey"})
    assert r.status_code == 200, r.text

    r = authed.delete(f"/api/parts/{part_id}/provider-links/mouser")
    assert r.status_code == 200, r.text
    assert [link["provider"] for link in r.json()["data"]["provider_links"]] == []

    after = _fields(authed, part_id)
    assert not [k for k in after if k.startswith("mouser:") and k != "mouser:Resistance"]
    # The user's edit survives as a plain manual row.
    assert after["mouser:Resistance"]["source"] == "manual"
    assert after["mouser:Resistance"]["value"] == "my value"


def test_unlink_an_absent_link_is_404(authed):
    _enable_digikey_primary(authed)
    part_id = _create_part(authed)

    r = authed.delete(f"/api/parts/{part_id}/provider-links/mouser")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "part.provider_link_not_found"


def test_primary_unlink_via_patch_also_drops_the_primary_link_row(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _create_part(authed)
    _stub_digikey(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    _stub_mouser(monkeypatch)
    authed.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")

    r = authed.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["linked_provider"] is None
    # The secondary link is not collateral damage.
    assert [link["provider"] for link in r.json()["data"]["provider_links"]] == ["mouser"]


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def _two_workspaces() -> tuple[TestClient, TestClient]:
    a, b = TestClient(app), TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    return a, b


def test_provider_credentials_are_not_visible_across_workspaces():
    a, b = _two_workspaces()
    _configure_mouser_secondary(a, "a-only-key")

    body = b.get("/api/workspaces/current").json()["data"]
    assert body["provider_credentials"] == []


def test_secondary_refresh_of_a_foreign_part_is_404(monkeypatch):
    a, b = _two_workspaces()
    _configure_mouser_secondary(b)
    part_a = _create_part(a)
    _stub_mouser(monkeypatch)

    r = b.post(f"/api/parts/{part_a}/refresh-from-provider?provider=mouser")
    assert r.status_code == 404, r.text


def test_unlinking_a_foreign_parts_link_is_404(monkeypatch):
    a, b = _two_workspaces()
    _configure_mouser_secondary(a)
    part_a = _create_part(a)
    _stub_mouser(monkeypatch)
    a.post(f"/api/parts/{part_a}/refresh-from-provider?provider=mouser")

    r = b.delete(f"/api/parts/{part_a}/provider-links/mouser")
    assert r.status_code == 404, r.text
    # A's link survives.
    assert [
        link["provider"]
        for link in a.get(f"/api/parts/{part_a}").json()["data"]["provider_links"]
    ] == ["mouser"]


def test_a_secondary_refresh_uses_the_callers_own_credentials(monkeypatch):
    """B has no Mouser credentials; A does. B's refresh must 400 rather
    than reach for A's key."""
    a, b = _two_workspaces()
    _configure_mouser_secondary(a, "a-only-key")
    part_b = _create_part(b)
    _stub_mouser(monkeypatch)

    r = b.post(f"/api/parts/{part_b}/refresh-from-provider?provider=mouser")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "part.provider_not_configured"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def _member_client(admin_c: TestClient, ws_id: str, role: str) -> TestClient:
    email = f"{role}-{uuid.uuid4().hex[:6]}@example.com"
    r = admin_c.post("/api/invitations", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    token = r.json()["data"]["token"]

    c = TestClient(app)
    signup_user(c, email=email, password=DEFAULT_PASSWORD)
    assert c.post("/api/invitations/accept", json={"token": token}).status_code == 200
    c.post(f"/api/workspaces/{ws_id}/switch")
    return c


@pytest.mark.parametrize("role", ["viewer", "member"])
def test_put_credentials_requires_admin(role):
    admin = TestClient(app)
    ws_id = _signup(admin, f"admin-{uuid.uuid4().hex[:6]}@x.com")
    other = _member_client(admin, ws_id, role)

    r = other.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "mouser", "api_key": "nope"},
    )
    assert r.status_code == 403, r.text


def test_put_credentials_refuses_an_api_token(authed):
    r = authed.post("/api/tokens", json={"label": "agent"})
    assert r.status_code == 201, r.text
    token = r.json()["data"]["token"]

    agent = TestClient(app)
    r = agent.put(
        "/api/workspaces/current/provider-credentials",
        json={"provider": "mouser", "api_key": "smuggled"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "auth.token_no_token_management"


def test_credential_rotation_audit_row_names_the_provider_not_the_secret(authed, db):
    from sqlalchemy import select

    from app.domain.audit.models import AuditLog

    _configure_mouser_secondary(authed, "secret-never-logged")

    row = db.execute(
        select(AuditLog)
        .where(AuditLog.action == "workspace.credentials_rotated")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).scalars().first()
    assert row is not None
    assert row.comment == "provider=mouser,fields=api_key"
    assert "secret-never-logged" not in (row.comment or "")
