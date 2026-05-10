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


@pytest.mark.parametrize(
    ("alert_type", "threshold"),
    [
        ("stock_below", {"qty": -5}),
        ("stock_above", {"qty": -5}),
        ("back_in_stock", {"qty": 1}),
        ("out_of_authorized_stock", {"qty": 1}),
        ("price_changed", {"delta_pct": 150}),
        ("bom_buyable", {"build_quantity": 0}),
    ],
)
def test_create_with_invalid_threshold_returns_422(
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

    disabled = authed_client.get("/api/sourcing/alerts?enabled=false").json()["data"]
    assert {alert["id"] for alert in disabled} == {disabled_alert["id"]}

    stock_above = authed_client.get("/api/sourcing/alerts?alert_type=stock_above").json()["data"]
    assert {alert["id"] for alert in stock_above} == {disabled_alert["id"]}

    part_filtered = authed_client.get(f"/api/sourcing/alerts?part_id={part_b}").json()["data"]
    assert {alert["id"] for alert in part_filtered} == {part_b_alert["id"]}

    project_filtered = authed_client.get(
        f"/api/sourcing/alerts?project_id={project_a}"
    ).json()["data"]
    assert {alert["id"] for alert in project_filtered} == {project_a_alert["id"]}

    include_archived = authed_client.get(
        "/api/sourcing/alerts?include_archived=true"
    ).json()["data"]
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

    data = authed_client.get("/api/sourcing/alerts").json()["data"]

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


def test_list_alerts_paginates(authed_client):
    part_id = create_part(authed_client, name="Paged alert target")
    alerts = [
        _create_stock_alert(authed_client, part_id, threshold={"qty": qty})
        for qty in range(3)
    ]

    first = authed_client.get("/api/sourcing/alerts?limit=1").json()["data"]
    second = authed_client.get("/api/sourcing/alerts?limit=1&offset=1").json()["data"]

    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["id"] != second[0]["id"]
    assert {first[0]["id"], second[0]["id"]}.issubset({alert["id"] for alert in alerts})


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

    data_b = client_b.get("/api/sourcing/alerts").json()["data"]

    assert alert_b["id"] in {alert["id"] for alert in data_b}
    assert alert_a["id"] not in {alert["id"] for alert in data_b}
