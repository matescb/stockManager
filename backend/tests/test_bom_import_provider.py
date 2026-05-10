from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import ratelimit as _ratelimit_mod
from app.main import app


def _signup(c: TestClient, email: str | None = None) -> str:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": email or f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    _enable_mouser(c)
    return c


@pytest.fixture
def limiter_enabled():
    original = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = True
    yield
    _ratelimit_mod.limiter.enabled = original
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def _enable_mouser(c: TestClient) -> None:
    r = c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    assert r.status_code == 200, r.text


def _project(c: TestClient, name: str = "P") -> str:
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _entry(
    c: TestClient,
    project_id: str,
    mpn: str,
    *,
    entry_type: str = "unmatched",
    part_id: str | None = None,
) -> str:
    payload = {
        "entry_type": entry_type,
        "name": mpn,
        "quantity": 2,
    }
    if part_id is not None:
        payload["part_id"] = part_id
    r = c.post(f"/api/projects/{project_id}/entries", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _part(c: TestClient, mpn: str = "EXISTING") -> str:
    r = c.post("/api/parts", json={"part_type": "local", "name": mpn, "mpn": mpn})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _lookup_result(mpn: str, manufacturer: str = "Yageo") -> dict:
    return {
        "found": True,
        "result": {
            "mpn": mpn,
            "manufacturer": manufacturer,
            "description": f"{mpn} resistor",
            "category": "Resistors",
            "footprint": "0402",
            "datasheet_url": "https://example.com/ds.pdf",
            "image_url": "https://example.com/img.jpg",
            "source_url": f"https://example.com/{mpn}",
            "specs": [{"key": "Resistance", "value": "10 kOhms"}],
        },
        "message": None,
    }


def test_bulk_import_creates_real_parts_with_specs(authed, monkeypatch):
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: _lookup_result(mpn),
    )
    project_id = _project(authed)
    entry_id = _entry(authed, project_id, "RC0402-10K")

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["created"] == 1
    assert body["pending_choices"] == []
    assert body["failures"] == []

    entry = authed.get(f"/api/projects/{project_id}/entries").json()["data"][0]
    assert entry["id"] == entry_id
    assert entry["entry_type"] == "part"
    part = authed.get(f"/api/parts/{entry['part_id']}").json()["data"]
    assert part["linked_provider"] != "none"
    assert part["linked_provider"] == "mouser"

    cfs = authed.get(f"/api/custom-fields/by-object/part/{part['id']}").json()["data"]
    provider_specs = [
        row for row in cfs
        if row["source"] == "provider" and row["key"] == "Resistance"
    ]
    assert len(provider_specs) >= 1


def test_bulk_import_skips_already_matched_rows(authed, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: calls.append(mpn) or _lookup_result(mpn),
    )
    project_id = _project(authed)
    part_id = _part(authed)
    _entry(authed, project_id, "EXISTING", entry_type="part", part_id=part_id)

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["created"] == 0
    assert calls == []


def test_bulk_import_links_existing_workspace_part_without_provider_lookup(authed, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: calls.append(mpn) or _lookup_result(mpn),
    )
    project_id = _project(authed)
    part_id = _part(authed, "RC0402-10K")
    entry_id = _entry(authed, project_id, "RC0402-10K")

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["created"] == 1
    assert body["failures"] == []
    assert calls == []

    entry = authed.get(f"/api/projects/{project_id}/entries").json()["data"][0]
    assert entry["id"] == entry_id
    assert entry["entry_type"] == "part"
    assert entry["part_id"] == part_id
    assert len(authed.get("/api/parts?limit=200").json()["data"]) == 1


def test_bulk_import_null_entry_ids_caps_rows_and_reports_truncated(authed, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: calls.append(mpn) or _lookup_result(mpn),
    )
    project_id = _project(authed)
    for i in range(201):
        _entry(authed, project_id, f"CAP-{i:03d}")

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["created"] == 200
    assert body["truncated"] is True
    assert len(calls) == 200
    entries = authed.get(f"/api/projects/{project_id}/entries").json()["data"]
    assert sum(1 for entry in entries if entry["entry_type"] == "unmatched") == 1


def test_ambiguous_mpn_returns_pending_choices_no_commit(authed, monkeypatch):
    def ambiguous(_provider, mpn):
        a = _lookup_result(mpn, "Alpha")["result"]
        b = _lookup_result(mpn, "Beta")["result"]
        return {"found": True, "result": a, "candidates": [a, b], "message": None}

    monkeypatch.setattr("app.domain.projects.bom_import_provider.lookup_with_cache", ambiguous)
    project_id = _project(authed)
    entry_id = _entry(authed, project_id, "AMB-1")

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": [entry_id]},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["created"] == 0
    assert body["pending_choices"][0]["entry_id"] == entry_id
    manufacturers = {c["manufacturer"] for c in body["pending_choices"][0]["candidates"]}
    assert manufacturers == {"Alpha", "Beta"}
    assert (
        authed.get(f"/api/projects/{project_id}/entries").json()["data"][0]["entry_type"]
        == "unmatched"
    )


def test_commit_choices_creates_parts_from_chosen_manufacturer(authed, monkeypatch):
    def ambiguous(_provider, mpn):
        a = _lookup_result(mpn, "Alpha")["result"]
        b = _lookup_result(mpn, "Beta")["result"]
        return {"found": True, "result": a, "candidates": [a, b], "message": None}

    monkeypatch.setattr("app.domain.projects.bom_import_provider.lookup_with_cache", ambiguous)
    project_id = _project(authed)
    entry_id = _entry(authed, project_id, "AMB-2")

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider/commit-choices",
        json={"choices": {entry_id: "Beta"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["created"] == 1
    entry = authed.get(f"/api/projects/{project_id}/entries").json()["data"][0]
    part = authed.get(f"/api/parts/{entry['part_id']}").json()["data"]
    assert part["manufacturer"] == "Beta"


def test_failed_lookup_appears_in_failures_not_as_stub(authed, monkeypatch):
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: {"found": False, "result": None, "message": "no match for MPN"},
    )
    project_id = _project(authed)
    _entry(authed, project_id, "NOPE")

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["created"] == 0
    assert body["failures"][0]["mpn"] == "NOPE"
    assert "no match" in body["failures"][0]["reason"]
    assert (
        authed.get(f"/api/projects/{project_id}/entries").json()["data"][0]["entry_type"]
        == "unmatched"
    )
    assert authed.get("/api/parts?limit=200").json()["data"] == []


def test_per_row_savepoint_isolates_failures(authed, monkeypatch):
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: _lookup_result(mpn),
    )
    real_create = __import__(
        "app.domain.projects.bom_import_provider",
        fromlist=["create_from_provider_lookup"],
    ).create_from_provider_lookup

    def maybe_raise(*args, **kwargs):
        if kwargs["mpn"] == "BAD":
            raise RuntimeError("boom")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.create_from_provider_lookup",
        maybe_raise,
    )
    project_id = _project(authed)
    _entry(authed, project_id, "GOOD1")
    _entry(authed, project_id, "BAD")
    _entry(authed, project_id, "GOOD2")

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["created"] == 2
    assert body["failures"][0]["mpn"] == "BAD"
    rows = authed.get(f"/api/projects/{project_id}/entries").json()["data"]
    assert [row["entry_type"] for row in rows] == ["part", "unmatched", "part"]


def test_foreign_project_returns_404(authed):
    other = TestClient(app)
    _signup(other)
    _enable_mouser(other)
    project_id = _project(authed)

    r = other.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 404
    assert r.json()["status"]["category"] == "not_found"


def test_workspace_isolation_two_workspaces_same_mpn(monkeypatch):
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: _lookup_result(mpn),
    )
    a = TestClient(app)
    b = TestClient(app)
    _signup(a)
    _signup(b)
    _enable_mouser(a)
    _enable_mouser(b)
    project_a = _project(a, "A")
    project_b = _project(b, "B")
    _entry(a, project_a, "SAME-MPN")
    _entry(b, project_b, "SAME-MPN")

    rb = b.post(
        f"/api/projects/{project_b}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert rb.status_code == 200, rb.text
    assert rb.json()["data"]["created"] == 1

    assert (
        a.get(f"/api/projects/{project_a}/entries").json()["data"][0]["entry_type"]
        == "unmatched"
    )
    assert len(a.get("/api/parts?limit=200").json()["data"]) == 0
    assert len(b.get("/api/parts?limit=200").json()["data"]) == 1


def test_rate_limit_30_per_minute(authed, monkeypatch, limiter_enabled):
    monkeypatch.setattr(
        "app.domain.projects.bom_import_provider.lookup_with_cache",
        lambda provider, mpn: {"found": False, "result": None, "message": "no match"},
    )
    project_id = _project(authed)

    for i in range(30):
        r = authed.post(
            f"/api/projects/{project_id}/bom/import-from-provider",
            json={"entry_ids": []},
        )
        assert r.status_code == 200, f"call {i}: {r.status_code} {r.text}"

    r = authed.post(
        f"/api/projects/{project_id}/bom/import-from-provider",
        json={"entry_ids": []},
    )
    assert r.status_code == 429, r.text
    assert r.json()["status"]["category"] == "rate_limited"
