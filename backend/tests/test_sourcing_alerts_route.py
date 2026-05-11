from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.core.ratelimit as _ratelimit_mod
from app.domain.sourcing.models import SourcingAlert
from app.main import app
from tests._factories import create_part, create_project_with_bom, signup_user


def _signup(client: TestClient | None = None) -> TestClient:
    client = client or TestClient(app)
    signup_user(client, email=f"alerts-{uuid.uuid4().hex[:8]}@example.com")
    return client


@pytest.fixture(autouse=False)
def limiter_enabled():
    original = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = True
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass
    yield
    _ratelimit_mod.limiter.enabled = original
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def _stock_alert_payload(part_id: str, **extra):
    payload = {
        "alert_type": "stock_below",
        "part_id": part_id,
        "threshold": {"qty": 5},
    }
    payload.update(extra)
    return payload


def _create_stock_alert(client: TestClient, part_id: str, **extra) -> dict:
    r = client.post("/api/sourcing/alerts", json=_stock_alert_payload(part_id, **extra))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _create_project_alert(
    client: TestClient,
    project_id: str,
    *,
    build_quantity: int = 1,
    **extra,
) -> dict:
    payload = {
        "alert_type": "bom_buyable",
        "project_id": project_id,
        "threshold": {"build_quantity": build_quantity},
    }
    payload.update(extra)
    r = client.post("/api/sourcing/alerts", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _list_alerts(client: TestClient, query: str = "") -> dict:
    r = client.get(f"/api/sourcing/alerts{query}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _list_alert_items(client: TestClient, query: str = "") -> list[dict]:
    return _list_alerts(client, query)["items"]


def _single_line_project(client: TestClient, *, name: str = "Alerts BOM") -> tuple[str, str]:
    part_id = create_part(client, name=f"{name} part", mpn=f"ALERT-{uuid.uuid4().hex[:6]}")
    project_id = create_project_with_bom(
        client,
        f"{name} {uuid.uuid4().hex[:6]}",
        [{"part_id": part_id, "quantity": 1}],
    )
    return project_id, part_id


def test_create_stock_below_alert(authed_client):
    part_id = create_part(authed_client, name="Alert resistor")

    data = _create_stock_alert(
        authed_client,
        part_id,
        threshold={"qty": 12},
        country_code="us",
        currency_code="usd",
        distributor_filter=["DigiKey"],
    )

    assert data["alert_type"] == "stock_below"
    assert data["part_id"] == part_id
    assert data["project_id"] is None
    assert data["threshold"] == {"qty": 12}
    assert data["country_code"] is None
    assert data["currency_code"] is None
    assert data["distributor_filter"] is None
    assert data["enabled"] is True
    assert data["archived_at"] is None


def test_create_lifecycle_risk_changed_alert_with_must_contain_threshold(
    authed_client,
):
    part_id = create_part(authed_client, name="Lifecycle target")

    r = authed_client.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": "lifecycle_risk_changed",
            "part_id": part_id,
            "threshold": {"must_contain": "EOL", "case_sensitive": True},
            "country_code": "us",
            "currency_code": "usd",
            "distributor_filter": ["DigiKey"],
        },
    )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["alert_type"] == "lifecycle_risk_changed"
    assert data["part_id"] == part_id
    assert data["project_id"] is None
    assert data["threshold"] == {"must_contain": "EOL", "case_sensitive": True}
    assert data["country_code"] == "US"
    assert data["currency_code"] == "USD"
    assert data["distributor_filter"] == ["DigiKey"]


def test_create_tariff_status_changed_alert_empty_threshold(authed_client):
    part_id = create_part(authed_client, name="Tariff target")

    r = authed_client.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": "tariff_status_changed",
            "part_id": part_id,
            "threshold": {},
        },
    )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["alert_type"] == "tariff_status_changed"
    assert data["threshold"] == {}


@pytest.mark.parametrize(
    ("alert_type", "threshold"),
    [
        ("stock_below", {"qty": -5}),
        ("stock_above", {"qty": -5}),
        ("back_in_stock", {"qty": 1}),
        ("out_of_authorized_stock", {"qty": 1}),
        ("price_changed", {"delta_pct": 150}),
        ("bom_buyable", {"build_quantity": 0}),
        ("lifecycle_risk_changed", {"to_states": ["Obsolete"]}),
        ("supply_chain_risk_changed", {"must_contain": "NRND", "unexpected": True}),
        ("tariff_status_changed", {"must_contain": "yes"}),
    ],
)
def test_create_alert_with_wrong_threshold_shape_returns_422(
    authed_client,
    alert_type: str,
    threshold: dict,
):
    project_id, part_id = _single_line_project(authed_client)
    payload = {
        "alert_type": alert_type,
        "threshold": threshold,
    }
    if alert_type == "bom_buyable":
        payload["project_id"] = project_id
    else:
        payload["part_id"] = part_id

    r = authed_client.post("/api/sourcing/alerts", json=payload)

    assert r.status_code == 422, r.text
    body = r.json()
    assert body["status"]["category"] == "validation_error"
    fields = {error["field"] for error in body["errors"]}
    assert any(field.startswith("body.threshold.") for field in fields)


@pytest.mark.parametrize(
    ("alert_type", "threshold", "expected_threshold"),
    [
        ("stock_below", {"qty": 5}, {"qty": 5}),
        ("stock_above", {"qty": 5}, {"qty": 5}),
        ("back_in_stock", {}, {}),
        ("out_of_authorized_stock", {}, {}),
        ("price_changed", {"delta_pct": 5}, {"delta_pct": "5"}),
        ("bom_buyable", {"build_quantity": 2}, {"build_quantity": 2}),
        (
            "lifecycle_risk_changed",
            {"must_contain": "EOL", "case_sensitive": True},
            {"must_contain": "EOL", "case_sensitive": True},
        ),
        (
            "supply_chain_risk_changed",
            {"must_contain": "NRND", "case_sensitive": False},
            {"must_contain": "NRND", "case_sensitive": False},
        ),
        ("tariff_status_changed", {}, {}),
    ],
)
def test_create_alert_with_valid_threshold_persists(
    authed_client,
    alert_type: str,
    threshold: dict,
    expected_threshold: dict,
):
    project_id, part_id = _single_line_project(authed_client)
    payload = {
        "alert_type": alert_type,
        "threshold": threshold,
    }
    if alert_type == "bom_buyable":
        payload["project_id"] = project_id
    else:
        payload["part_id"] = part_id

    r = authed_client.post("/api/sourcing/alerts", json=payload)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["alert_type"] == alert_type
    assert data["threshold"] == expected_threshold


@pytest.mark.parametrize(
    "alert_type",
    [
        "lifecycle_risk_changed",
        "supply_chain_risk_changed",
        "tariff_status_changed",
    ],
)
def test_gap_field_alerts_require_part_id_not_project_id(
    authed_client,
    alert_type: str,
):
    project_id, _part_id = _single_line_project(authed_client)

    r = authed_client.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": alert_type,
            "project_id": project_id,
            "threshold": {},
        },
    )

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_xor_part_project_validation(authed_client):
    project_id, part_id = _single_line_project(authed_client)

    both = authed_client.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": "stock_below",
            "part_id": part_id,
            "project_id": project_id,
            "threshold": {"qty": 5},
        },
    )
    neither = authed_client.post(
        "/api/sourcing/alerts",
        json={"alert_type": "stock_below", "threshold": {"qty": 5}},
    )

    assert both.status_code == 422, both.text
    assert neither.status_code == 422, neither.text


def test_bom_buyable_requires_project_id_not_part_id(authed_client):
    part_id = create_part(authed_client, name="Not a project")

    r = authed_client.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": "bom_buyable",
            "part_id": part_id,
            "threshold": {"build_quantity": 1},
        },
    )

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_notify_user_ids_validated_against_workspace_members(authed_client):
    part_id = create_part(authed_client, name="Notify target")

    r = authed_client.post(
        "/api/sourcing/alerts",
        json=_stock_alert_payload(
            part_id,
            notify_user_ids=[str(uuid.uuid4())],
        ),
    )

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_foreign_part_id_returns_404():
    client_a = _signup()
    client_b = _signup()
    part_a = create_part(client_a, name="Foreign target")

    r = client_b.post("/api/sourcing/alerts", json=_stock_alert_payload(part_a))

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_list_filters(authed_client):
    part_a = create_part(authed_client, name="Filter A")
    part_b = create_part(authed_client, name="Filter B")
    project_a, _ = _single_line_project(authed_client, name="Filter project A")
    project_b, _ = _single_line_project(authed_client, name="Filter project B")

    enabled_alert = _create_stock_alert(
        authed_client,
        part_a,
        alert_type="stock_below",
        threshold={"qty": 5},
        enabled=True,
    )
    disabled_alert = _create_stock_alert(
        authed_client,
        part_a,
        alert_type="stock_above",
        threshold={"qty": 50},
        enabled=False,
    )
    part_b_alert = _create_stock_alert(
        authed_client,
        part_b,
        threshold={"qty": 7},
    )
    project_a_alert = _create_project_alert(authed_client, project_a, build_quantity=2)
    project_b_alert = _create_project_alert(authed_client, project_b, build_quantity=3)
    archived_alert = _create_stock_alert(
        authed_client,
        part_a,
        threshold={"qty": 9},
    )
    authed_client.delete(f"/api/sourcing/alerts/{archived_alert['id']}")

    disabled = _list_alert_items(authed_client, "?enabled=false")
    assert {alert["id"] for alert in disabled} == {disabled_alert["id"]}

    stock_above = _list_alert_items(authed_client, "?alert_type=stock_above")
    assert {alert["id"] for alert in stock_above} == {disabled_alert["id"]}

    part_filtered = _list_alert_items(authed_client, f"?part_id={part_b}")
    assert {alert["id"] for alert in part_filtered} == {part_b_alert["id"]}

    project_filtered = _list_alert_items(authed_client, f"?project_id={project_a}")
    assert {alert["id"] for alert in project_filtered} == {project_a_alert["id"]}

    include_archived = _list_alert_items(authed_client, "?include_archived=true")
    assert archived_alert["id"] in {alert["id"] for alert in include_archived}
    assert enabled_alert["id"] in {alert["id"] for alert in include_archived}
    assert project_b_alert["id"] in {alert["id"] for alert in include_archived}


def test_patch_cannot_change_alert_type(authed_client):
    part_id = create_part(authed_client, name="Patch target")
    alert = _create_stock_alert(authed_client, part_id)

    r = authed_client.patch(
        f"/api/sourcing/alerts/{alert['id']}",
        json={"alert_type": "stock_above"},
    )

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_delete_archives_not_hard_deletes(authed_client, db):
    part_id = create_part(authed_client, name="Archive target")
    alert = _create_stock_alert(authed_client, part_id)

    r = authed_client.delete(f"/api/sourcing/alerts/{alert['id']}")

    assert r.status_code == 200, r.text
    row = db.execute(
        select(SourcingAlert).where(SourcingAlert.id == uuid.UUID(alert["id"]))
    ).scalar_one()
    assert row.archived_at is not None


def test_archived_excluded_from_default_list(authed_client):
    part_id = create_part(authed_client, name="Default archive target")
    alert = _create_stock_alert(authed_client, part_id)
    authed_client.delete(f"/api/sourcing/alerts/{alert['id']}")

    data = _list_alert_items(authed_client)

    assert alert["id"] not in {item["id"] for item in data}


def test_get_archived_returns_404(authed_client):
    part_id = create_part(authed_client, name="Get archived target")
    alert = _create_stock_alert(authed_client, part_id)
    authed_client.delete(f"/api/sourcing/alerts/{alert['id']}")

    r = authed_client.get(f"/api/sourcing/alerts/{alert['id']}")

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_patch_archived_returns_404(authed_client):
    part_id = create_part(authed_client, name="Patch archived target")
    alert = _create_stock_alert(authed_client, part_id)
    authed_client.delete(f"/api/sourcing/alerts/{alert['id']}")

    r = authed_client.patch(
        f"/api/sourcing/alerts/{alert['id']}",
        json={"enabled": False},
    )

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_delete_archived_returns_404(authed_client):
    part_id = create_part(authed_client, name="Delete archived target")
    alert = _create_stock_alert(authed_client, part_id)
    authed_client.delete(f"/api/sourcing/alerts/{alert['id']}")

    r = authed_client.delete(f"/api/sourcing/alerts/{alert['id']}")

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_list_alerts_default_returns_first_50_with_total(authed_client):
    part_id = create_part(authed_client, name="Default paged alert target")
    alerts = [
        _create_stock_alert(authed_client, part_id, threshold={"qty": qty})
        for qty in range(55)
    ]

    data = _list_alerts(authed_client)

    assert data["total"] == 55
    assert data["limit"] == 50
    assert data["offset"] == 0
    assert len(data["items"]) == 50
    assert {item["id"] for item in data["items"]}.issubset({alert["id"] for alert in alerts})


def test_list_alerts_offset_pagination_works(authed_client):
    part_id = create_part(authed_client, name="Paged alert target")
    alerts = [
        _create_stock_alert(authed_client, part_id, threshold={"qty": qty})
        for qty in range(3)
    ]

    first = _list_alerts(authed_client, "?limit=1")
    second = _list_alerts(authed_client, "?limit=1&offset=1")

    assert first["total"] == 3
    assert first["limit"] == 1
    assert first["offset"] == 0
    assert second["total"] == 3
    assert second["limit"] == 1
    assert second["offset"] == 1
    assert len(first["items"]) == 1
    assert len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert {first["items"][0]["id"], second["items"][0]["id"]}.issubset(
        {alert["id"] for alert in alerts}
    )


def test_list_alerts_limit_max_200(authed_client):
    part_id = create_part(authed_client, name="Max paged alert target")
    for qty in range(205):
        _create_stock_alert(authed_client, part_id, threshold={"qty": qty})

    data = _list_alerts(authed_client, "?limit=200")
    over_limit = authed_client.get("/api/sourcing/alerts?limit=201")

    assert data["total"] == 205
    assert data["limit"] == 200
    assert len(data["items"]) == 200
    assert over_limit.status_code == 422


def test_rate_limit_30_per_minute(authed_client, limiter_enabled):
    part_id = create_part(authed_client, name="Rate limited alert target")
    for qty in range(30):
        r = authed_client.post(
            "/api/sourcing/alerts",
            json=_stock_alert_payload(part_id, threshold={"qty": qty}),
        )
        assert r.status_code == 200, r.text

    r = authed_client.post(
        "/api/sourcing/alerts",
        json=_stock_alert_payload(part_id, threshold={"qty": 1000}),
    )

    assert r.status_code == 429, r.text
    assert r.json()["status"]["category"] == "rate_limited"


def test_workspace_isolation_two_workspaces_same_part_alerts():
    client_a = _signup()
    client_b = _signup()
    part_a = create_part(client_a, name="Shared alert", mpn="ALERT-SHARED")
    part_b = create_part(client_b, name="Shared alert", mpn="ALERT-SHARED")
    alert_a = _create_stock_alert(client_a, part_a, threshold={"qty": 3})
    alert_b = _create_stock_alert(client_b, part_b, threshold={"qty": 3})

    data_b = _list_alert_items(client_b)

    assert alert_b["id"] in {alert["id"] for alert in data_b}
    assert alert_a["id"] not in {alert["id"] for alert in data_b}
