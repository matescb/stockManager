from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.core.ratelimit as _ratelimit_mod
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.models import SourcingCache
from app.main import app
from tests._factories import create_part, create_project_with_bom, signup_user


class _TrustedPartsPost:
    calls: list[dict[str, Any]] = []
    missing_mpns: set[str] = set()
    distributor_by_workspace: dict[str, str] = {}

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.missing_mpns = set()
        cls.distributor_by_workspace = {}

    @classmethod
    def post(cls, url: str, json: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        queries = json["Queries"]
        cls.calls.append(
            {
                "url": url,
                "queries": [query["SearchToken"] for query in queries],
                "company_id": json["CompanyId"],
                "use_cached_data": json["UseCachedData"],
                "distributors": json.get("Distributors"),
            }
        )
        return (
            200,
            {
                "RequestId": f"tp-{len(cls.calls)}",
                "PartResults": [
                    _tp_part_result(query["SearchToken"], json["CompanyId"])
                    for query in queries
                    if query["SearchToken"] not in cls.missing_mpns
                ],
            },
        )


@pytest.fixture(autouse=True)
def _sourcing_integration_state(monkeypatch):
    original_limiter_enabled = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = False
    _TrustedPartsPost.reset()
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass
    monkeypatch.setattr("app.domain.sourcing.client._post_tp", _TrustedPartsPost.post)
    yield
    _ratelimit_mod.limiter.enabled = original_limiter_enabled
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def _configure_sourcing(
    client: TestClient,
    *,
    company_id: str = "company-w1",
    preferred: list[str] | None = None,
) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": company_id,
            "sourcing_api_key": f"api-key-{company_id}",
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
            "sourcing_preferred_distributors": preferred or ["DigiKey"],
            "sourcing_use_cached_for_dashboards": False,
        },
    )
    assert r.status_code == 200, r.text


def _current_workspace_id(client: TestClient) -> str:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _authed_client(email_prefix: str = "tp") -> TestClient:
    client = TestClient(app)
    signup_user(client, email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com")
    return client


def _post_sourcing(client: TestClient, project_id: str, build_quantity: int = 1):
    return client.post(
        f"/api/projects/{project_id}/sourcing",
        json={"build_quantity": build_quantity},
    )


def _create_project(client: TestClient, name: str) -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add_bom_entry(
    client: TestClient,
    project_id: str,
    *,
    part_id: str,
    quantity: int = 1,
    entry_type: str = "part",
) -> str:
    r = client.post(
        f"/api/projects/{project_id}/entries",
        json={"entry_type": entry_type, "part_id": part_id, "quantity": quantity},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _create_project_with_parts(
    client: TestClient,
    *,
    count: int,
    prefix: str = "TP-BOM",
    quantity: int = 1,
) -> tuple[str, list[str]]:
    bom = []
    part_ids = []
    for index in range(count):
        mpn = f"{prefix}-{index:02d}"
        part_id = create_part(client, name=mpn, mpn=mpn)
        part_ids.append(part_id)
        bom.append({"part_id": part_id, "quantity": quantity})
    return create_project_with_bom(client, f"{prefix} project", bom), part_ids


def _add_substitute(client: TestClient, part_id: str, substitute_id: str) -> None:
    r = client.post(
        f"/api/parts/{part_id}/substitutes",
        json={"substitute_part_id": substitute_id},
    )
    assert r.status_code == 200, r.text


def _add_meta_member(client: TestClient, meta_id: str, member_id: str) -> None:
    r = client.post(f"/api/parts/{meta_id}/members", json={"member_part_id": member_id})
    assert r.status_code in (200, 201), r.text


def _tp_part_result(mpn: str, company_id: str) -> dict[str, Any]:
    primary = _workspace_distributor(company_id)
    secondary = "Mouser" if primary != "Mouser" else "DigiKey"
    return {
        "PartNumber": mpn,
        "Manufacturer": "IntegrationCo",
        "ProductUrl": f"https://www.trustedparts.com/search/{mpn}",
        "Distributors": [
            {
                "Name": primary,
                "DistributorResults": [
                    _tp_distributor_result(mpn, primary, stock=100, amount="0.1234")
                ],
            },
            {
                "Name": secondary,
                "DistributorResults": [
                    _tp_distributor_result(mpn, secondary, stock=4, amount="0.2345")
                ],
            },
        ],
    }


def _workspace_distributor(company_id: str) -> str:
    if company_id not in _TrustedPartsPost.distributor_by_workspace:
        index = len(_TrustedPartsPost.distributor_by_workspace) + 1
        _TrustedPartsPost.distributor_by_workspace[company_id] = f"WS{index}Supply"
    return _TrustedPartsPost.distributor_by_workspace[company_id]


def _tp_distributor_result(
    mpn: str,
    distributor: str,
    *,
    stock: int,
    amount: str,
) -> dict[str, Any]:
    return {
        "DistributorPartNumber": f"{mpn}-{distributor}",
        "Description": f"{mpn} from {distributor}",
        "Pricing": {
            "CurrencyCode": "EUR",
            "MinimumQuantity": 1,
            "Prices": [{"Quantity": 1, "Amount": amount}],
        },
        "Stock": {"QuantityOnHand": stock},
        "Packaging": [{"PackageType": "Reel", "MinimumOrderQuantity": 1}],
        "Links": [
            {
                "Type": "DistributorProduct",
                "Url": f"https://www.trustedparts.com/{distributor}/{mpn}",
            }
        ],
    }


def _queried_mpns() -> list[str]:
    return [mpn for call in _TrustedPartsPost.calls for mpn in call["queries"]]


def _monetary_values(data: Any):
    monetary_keys = {"unit_price", "est_extended_cost", "est_purchase_cost", "est_total_cost"}
    if isinstance(data, dict):
        for key, value in data.items():
            if key in monetary_keys and value is not None:
                yield key, value
            yield from _monetary_values(value)
    elif isinstance(data, list):
        for item in data:
            yield from _monetary_values(item)


def test_full_bom_pipeline_smoke():
    client = _authed_client()
    _configure_sourcing(client)
    project_id, _parts = _create_project_with_parts(client, count=3, prefix="SMOKE")

    r = _post_sourcing(client, project_id)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["category"] == "ok"
    assert set(body["data"]) >= {
        "rows",
        "coverage",
        "capacity",
        "partial",
        "fetched_at",
    }
    assert body["data"]["partial"] is False
    assert len(body["data"]["rows"]) == 3
    assert body["data"]["coverage"]["rows"]
    assert "links" in body["data"]
    assert "not_a_field" not in body["data"]
    assert all(len(call["queries"]) <= 50 for call in _TrustedPartsPost.calls)


def test_substitutes_are_searched_and_joined():
    client = _authed_client()
    _configure_sourcing(client)
    main = create_part(client, name="Main", mpn="MAIN-MPN")
    alt = create_part(client, name="Alt", mpn="ALT-MPN")
    second = create_part(client, name="Second", mpn="ALT-MPN-2")
    _add_substitute(client, main, alt)
    _add_substitute(client, second, alt)
    project_id = create_project_with_bom(
        client,
        "Substitutes",
        [{"part_id": main, "quantity": 3}, {"part_id": second, "quantity": 2}],
    )

    r = _post_sourcing(client, project_id)

    assert r.status_code == 200, r.text
    queried = _queried_mpns()
    assert queried.count("ALT-MPN") == 1
    assert "UNRELATED-MPN" not in queried
    rows = r.json()["data"]["rows"]
    main_row = next(row for row in rows if row["part_id"] == main)
    assert "ALT-MPN" in {offer["mpn"] for offer in main_row["offers"]}
    assert "UNRELATED-MPN" not in {offer["mpn"] for offer in main_row["offers"]}


def test_meta_part_members_resolve():
    client = _authed_client()
    _configure_sourcing(client)
    meta = create_part(client, name="Meta 10k", part_type="meta")
    member_a = create_part(client, name="10k A", mpn="META-A")
    member_b = create_part(client, name="10k B", mpn="META-B")
    outsider = create_part(client, name="Outsider", mpn="META-OUT")
    _add_meta_member(client, meta, member_a)
    _add_meta_member(client, meta, member_b)
    project_id = _create_project(client, "Meta BOM")
    _add_bom_entry(client, project_id, part_id=meta, quantity=5, entry_type="meta_part")

    r = _post_sourcing(client, project_id)

    assert r.status_code == 200, r.text
    assert {"META-A", "META-B"}.issubset(set(_queried_mpns()))
    assert "META-OUT" not in _queried_mpns()
    row = r.json()["data"]["rows"][0]
    assert {offer["mpn"] for offer in row["offers"]} >= {"META-A", "META-B"}
    assert outsider not in row["substitute_ids"]


def test_60_line_bom_respects_50_mpn_chunk_ceiling():
    client = _authed_client()
    _configure_sourcing(client)
    project_id, _parts = _create_project_with_parts(client, count=60, prefix="CHUNK")

    r = _post_sourcing(client, project_id)

    assert r.status_code == 200, r.text
    assert len(_TrustedPartsPost.calls) == 60
    assert all(len(call["queries"]) <= 50 for call in _TrustedPartsPost.calls)
    assert len(set(_queried_mpns())) == 60
    assert "CHUNK-60" not in _queried_mpns()
    ws_id = uuid.UUID(_current_workspace_id(client))
    assert sum(count for _timestamp, count in BUDGET._events[(ws_id, 10)]) == 60


def test_workspace_isolation_no_cross_leak(db):
    client_a = _authed_client("tp-a")
    client_b = _authed_client("tp-b")
    _configure_sourcing(client_a, company_id="company-a")
    _configure_sourcing(client_b, company_id="company-b")
    project_a, _ = _create_project_with_parts(client_a, count=1, prefix="SHARED")
    project_b, _ = _create_project_with_parts(client_b, count=1, prefix="SHARED")
    ws_a = _current_workspace_id(client_a)
    ws_b = _current_workspace_id(client_b)

    first = _post_sourcing(client_a, project_a)
    second = _post_sourcing(client_b, project_b)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_offer = first.json()["data"]["rows"][0]["offers"][0]
    second_offer = second.json()["data"]["rows"][0]["offers"][0]
    assert first_offer["distributor"] == "WS1Supply"
    assert second_offer["distributor"] == "WS2Supply"
    assert first_offer["distributor"] != second_offer["distributor"]
    cache_rows = db.execute(
        select(SourcingCache).where(SourcingCache.query_json["mpn"].astext == "SHARED-00")
    ).scalars().all()
    assert {str(row.workspace_id) for row in cache_rows} == {ws_a, ws_b}
    assert len({row.query_hash for row in cache_rows}) == 1
    assert all(row.response_json["offers"] for row in cache_rows)


def test_partial_flag_when_budget_degrades():
    client = _authed_client()
    _configure_sourcing(client)
    project_id, _parts = _create_project_with_parts(client, count=2, prefix="PARTIAL")
    BUDGET.record(uuid.UUID(_current_workspace_id(client)), 50)

    r = _post_sourcing(client, project_id)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["partial"] is True
    assert _TrustedPartsPost.calls
    assert all(call["use_cached_data"] is True for call in _TrustedPartsPost.calls)
    assert "PARTIAL-02" not in _queried_mpns()


def test_capacity_blocking_lines_match_coverage_uncovered():
    client = _authed_client()
    _configure_sourcing(client)
    covered = create_part(client, name="Covered", mpn="COVERED")
    uncovered = create_part(client, name="Uncovered", mpn="NO-OFFER")
    project_id = create_project_with_bom(
        client,
        "Capacity Coverage",
        [{"part_id": covered, "quantity": 3}, {"part_id": uncovered, "quantity": 3}],
    )
    _TrustedPartsPost.missing_mpns = {"NO-OFFER"}

    r = _post_sourcing(client, project_id)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    blocking_after = set(data["capacity"]["blocking_lines_after_purchase"])
    uncovered_by_distributor = {
        line_id
        for row in data["coverage"]["rows"]
        for line_id in row["lines_uncovered"]
    }
    assert blocking_after & uncovered_by_distributor
    assert not blocking_after - uncovered_by_distributor
    assert data["capacity"]["can_build_after_purchase"] == 0
    assert "NO-OFFER" in _queried_mpns()
    no_offer_row = next(row for row in data["rows"] if row["mpn"] == "NO-OFFER")
    assert no_offer_row["offers"] == []


def test_decimal_serialisation_roundtrip():
    client = _authed_client()
    _configure_sourcing(client)
    project_id, _parts = _create_project_with_parts(client, count=2, prefix="DEC", quantity=7)

    r = _post_sourcing(client, project_id)

    assert r.status_code == 200, r.text
    monetary = list(_monetary_values(r.json()["data"]))
    assert monetary
    for _key, value in monetary:
        assert isinstance(value, str)
        Decimal(value)
    assert not any(isinstance(value, float) for _key, value in monetary)
    assert "DEC-02" not in _queried_mpns()
