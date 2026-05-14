from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import signup_user

pytestmark = pytest.mark.real_db


def _session_cookie(client: TestClient):
    return next(cookie for cookie in client.cookies.jar)


@pytest.fixture
def authed_owner():
    client = TestClient(app)
    signup_user(client)
    return client


def test_concurrent_create_same_mpn(authed_owner, monkeypatch):
    import app.api.routes.parts_core as parts_core

    original_lookup = parts_core._active_part_by_mpn
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    precheck_calls = 0

    def racing_lookup(db, *, workspace_id, mpn):
        nonlocal precheck_calls
        existing = original_lookup(db, workspace_id=workspace_id, mpn=mpn)
        with lock:
            should_wait = existing is None and precheck_calls < 2
            if should_wait:
                precheck_calls += 1
        if should_wait:
            barrier.wait(timeout=10)
        return existing

    monkeypatch.setattr(parts_core, "_active_part_by_mpn", racing_lookup)

    cookie = _session_cookie(authed_owner)
    mpn = f"RACE-{uuid.uuid4().hex[:8]}"
    results: list[tuple[int, dict]] = []

    def do_create(name: str) -> None:
        client = TestClient(app)
        client.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        response = client.post("/api/parts", json={"name": name, "mpn": mpn})
        results.append((response.status_code, response.json()))

    threads = [
        threading.Thread(target=do_create, args=("Race winner",)),
        threading.Thread(target=do_create, args=("Race loser",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 2, results
    assert sorted(status_code for status_code, _body in results) == [201, 409]

    created = next(body for status_code, body in results if status_code == 201)["data"]
    conflict = next(body for status_code, body in results if status_code == 409)
    assert conflict["existing_id"] == created["id"]
    assert conflict["existing_name"] == created["name"]
    assert conflict["status"]["category"] == "conflict"

    parts = authed_owner.get("/api/parts", params={"mpn": mpn}).json()["data"]
    assert len(parts) == 1
    assert parts[0]["id"] == created["id"]


def test_bulk_import_same_mpn_race_returns_duplicate(authed_owner, monkeypatch):
    import app.api.routes.parts_scan as parts_scan
    import app.domain.parts.providers.mouser as mouser_mod

    authed_owner.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )

    original_lookup = parts_scan._active_part_by_mpn
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    precheck_calls = 0

    def racing_lookup(db, *, workspace_id, mpn):
        nonlocal precheck_calls
        existing = original_lookup(db, workspace_id=workspace_id, mpn=mpn)
        with lock:
            should_wait = existing is None and precheck_calls < 2
            if should_wait:
                precheck_calls += 1
        if should_wait:
            barrier.wait(timeout=10)
        return existing

    def fake_mouser(_url, payload):
        mpn = payload["SearchByPartRequest"]["mouserPartNumber"]
        return {
            "Errors": [],
            "SearchResults": {
                "NumberOfResult": 1,
                "Parts": [
                    {
                        "Manufacturer": "Yageo",
                        "ManufacturerPartNumber": mpn,
                        "Description": "Race import resistor",
                        "ProductAttributes": [],
                    }
                ],
            },
        }

    monkeypatch.setattr(parts_scan, "_active_part_by_mpn", racing_lookup)
    monkeypatch.setattr(mouser_mod, "_post_mouser", fake_mouser)

    cookie = _session_cookie(authed_owner)
    mpn = f"BULK-RACE-{uuid.uuid4().hex[:8]}"
    results: list[dict] = []

    def do_import(key: str) -> None:
        client = TestClient(app)
        client.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        response = client.post(
            "/api/parts/bulk-import-from-scan",
            json={"rows": [{"mpn": mpn}], "idempotency_key": key},
        )
        assert response.status_code == 200, response.text
        results.append(response.json()["data"])

    threads = [
        threading.Thread(target=do_import, args=("race-one",)),
        threading.Thread(target=do_import, args=("race-two",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(results) == 2, results
    row_statuses = sorted(result["rows"][0]["status"] for result in results)
    assert row_statuses == ["created", "duplicate"]

    duplicate = next(
        result["rows"][0]
        for result in results
        if result["rows"][0]["status"] == "duplicate"
    )
    created = next(
        result["rows"][0]
        for result in results
        if result["rows"][0]["status"] == "created"
    )
    assert duplicate["part_id"] == created["part_id"]
    assert sum(result["summary"]["created"] for result in results) == 1
    assert sum(result["summary"]["duplicate"] for result in results) == 1
