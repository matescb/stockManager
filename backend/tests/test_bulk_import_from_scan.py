from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> str:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
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
    # Bulk-import requires a configured provider. Always Mouser in these
    # tests since the underlying seam is monkeypatched anyway.
    c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    return c


def _stub_mouser_response(mpn: str = "RC0402JR-070R") -> dict:
    return {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "Manufacturer": "Yageo",
                    "ManufacturerPartNumber": mpn,
                    "Description": "Thick Film Resistors - SMD 0R 1/16W 5% 0402",
                    "Category": "Resistors",
                    "DataSheetUrl": "https://example.com/ds.pdf",
                    "ImagePath": "https://example.com/img.jpg",
                    "ProductDetailUrl": f"https://www.mouser.com/{mpn}",
                    "ProductAttributes": [
                        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
                        {"AttributeName": "Tolerance", "AttributeValue": "5 %"},
                        {"AttributeName": "Package / Case", "AttributeValue": "0402"},
                    ],
                }
            ],
        },
    }


def _create_storage(c: TestClient, name: str = "A1") -> str:
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_bulk_import_creates_part_with_provider_specs(authed, monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(),
    )
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["summary"]["created"] == 1
    assert body["summary"]["duplicate"] == 0
    assert body["provider"] == "mouser"
    row = body["rows"][0]
    assert row["status"] == "created"
    assert row["mpn"] == "RC0402JR-070R"
    part_id = row["part_id"]

    # The part exists and is linked to mouser.
    p = authed.get(f"/api/parts/{part_id}").json()["data"]
    assert p["mpn"] == "RC0402JR-070R"
    assert p["manufacturer"] == "Yageo"
    assert p["footprint"] == "0402"
    assert p["linked_provider"] == "mouser"
    assert p["part_type"] == "linked"

    # Provider specs land as source='provider' custom_fields.
    cfs = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    by_key = {row["key"]: row for row in cfs}
    assert by_key["Resistance"]["source"] == "provider"
    assert by_key["Tolerance"]["source"] == "provider"
    # image_url and datasheet_url stored too (consumed by PartInfo's media card).
    assert by_key["image_url"]["value"] == "https://example.com/img.jpg"
    assert by_key["datasheet_url"]["value"] == "https://example.com/ds.pdf"


def test_bulk_import_marks_truncated_provider_specs(authed, monkeypatch):
    long_value = "x" * 1200
    mouser_response = _stub_mouser_response()
    mouser_response["SearchResults"]["Parts"][0]["ProductAttributes"] = [
        {"AttributeName": "Long Spec", "AttributeValue": long_value}
    ]
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: mouser_response,
    )

    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}]},
    )
    assert r.status_code == 200, r.text
    part_id = r.json()["data"]["rows"][0]["part_id"]

    cfs = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    by_key = {row["key"]: row for row in cfs}
    stored = by_key["Long Spec"]["value"]
    assert len(stored) == 1024
    assert stored.endswith("[truncated by provider import]")


def test_bulk_import_surfaces_ambiguous_provider_match(authed, monkeypatch):
    mpn = "AMB-100"
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {
            "Errors": [],
            "SearchResults": {
                "NumberOfResult": 2,
                "Parts": [
                    {
                        "Manufacturer": "Alpha",
                        "ManufacturerPartNumber": mpn,
                        "Description": "Alpha variant",
                        "ProductDetailUrl": "https://www.mouser.com/alpha",
                        "ProductAttributes": [],
                    },
                    {
                        "Manufacturer": "Beta",
                        "ManufacturerPartNumber": mpn,
                        "Description": "Beta variant",
                        "ProductDetailUrl": "https://www.mouser.com/beta",
                        "ProductAttributes": [],
                    },
                ],
            },
        },
    )

    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": mpn}]},
    )

    assert r.status_code == 200, r.text
    body = r.json()["data"]
    row = body["rows"][0]
    assert row["status"] == "created"
    assert row["needs_disambiguation"] is True
    assert row["candidate_count"] == 2
    assert row["selected_manufacturer"] == "Alpha"
    assert body["summary"]["needs_disambiguation"] == 1


def test_bulk_import_with_quantity_creates_initial_stock(authed, monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(),
    )
    storage_id = _create_storage(authed, "Bin 1")
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [
            {"mpn": "RC0402JR-070R", "quantity": 50, "storage_location_id": storage_id}
        ]},
    )
    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["status"] == "created"
    assert row["quantity_added"] == 50
    assert row["stock_error"] is None

    # The on_hand count reflects the initial stock entry.
    p = authed.get(f"/api/parts/{row['part_id']}").json()["data"]
    assert p["on_hand"] == 50


def test_bulk_import_recognises_bag_rescan(authed, monkeypatch):
    """Re-scanning the same physical bag (same bag_signature) doesn't
    double-import. The second import surfaces a `bag_rescan` row
    carrying the prior import's (part, lot, location, qty) so the UI
    can offer an inline consume-from-this-bag affordance instead of
    silently creating a duplicate stock entry."""
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(),
    )
    storage_id = _create_storage(authed, "Bin A")
    sig = "a" * 64  # 64-char hex digest, the route accepts any [a-f0-9]{64}
    # First import: standard `created` outcome plus an on_hand stock_entry
    # carrying the bag_signature.
    first = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{
            "mpn": "RC0402JR-070R",
            "quantity": 50,
            "storage_location_id": storage_id,
            "lot_name": "Lot 12345",
            "bag_signature": sig,
        }]},
    )
    first_row = first.json()["data"]["rows"][0]
    assert first_row["status"] == "created", first_row

    # Second import — same signature. No new part, no new stock; we get
    # back the prior coordinates so the UI can offer "remove qty from this lot".
    second = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{
            "mpn": "RC0402JR-070R",
            "bag_signature": sig,
        }]},
    )
    body = second.json()["data"]
    rescan = body["rows"][0]
    assert rescan["status"] == "bag_rescan", rescan
    assert rescan["part_id"] == first_row["part_id"]
    assert rescan["storage_location_id"] == storage_id
    assert rescan["quantity"] == 50
    assert rescan["lot_id"] is not None
    assert body["summary"]["bag_rescan"] == 1
    assert body["summary"]["created"] == 0
    assert body["summary"]["duplicate"] == 0


def test_bulk_import_persists_lot_and_comments_for_traceability(authed, monkeypatch):
    """When the bag carries traceability fields (lot, date code, PO,
    invoice), they must land on the lot row + stock-entry comments so
    you can trace this physical bag back to its source order months
    later."""
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(),
    )
    storage_id = _create_storage(authed, "Bin A")
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{
            "mpn": "RC0402JR-070R",
            "quantity": 50,
            "storage_location_id": storage_id,
            "lot_name": "Lot 12345 · DC 2545",
            "comments": "Order #44861 · invoice 078101306",
        }]},
    )
    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["status"] == "created"
    part_id = row["part_id"]

    # Lot was created with the synthesised name and is wired to the stock entry.
    lots = authed.get(f"/api/parts/{part_id}/lots").json()["data"]
    assert len(lots) == 1
    assert lots[0]["name"] == "Lot 12345 · DC 2545"

    # Stock entry carries the order/invoice references in its comments.
    history = authed.get("/api/stock/history").json()["data"]
    mine = [e for e in history if e["part_id"] == part_id]
    assert len(mine) == 1
    assert mine[0]["comments"] == "Order #44861 · invoice 078101306"
    assert mine[0]["lot_id"] == lots[0]["id"]


def test_bulk_import_omits_lot_when_bag_has_no_traceability(authed, monkeypatch):
    """Bags without lot/date/serial info should not invent a Lot row —
    the import falls back to a bare stock entry, same as a manual add."""
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(),
    )
    storage_id = _create_storage(authed, "Bin B")
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{
            "mpn": "RC0402JR-070R",
            "quantity": 50,
            "storage_location_id": storage_id,
        }]},
    )
    part_id = r.json()["data"]["rows"][0]["part_id"]
    lots = authed.get(f"/api/parts/{part_id}/lots").json()["data"]
    assert lots == []
    history = authed.get("/api/stock/history").json()["data"]
    mine = [e for e in history if e["part_id"] == part_id]
    assert len(mine) == 1
    assert mine[0]["lot_id"] is None
    assert mine[0]["comments"] is None


def test_bulk_import_quantity_without_location_still_creates_stock(authed, monkeypatch):
    """The bag's Q field should always land on-hand. No storage location
    means the stock entry is recorded with location=NULL — the operator
    can file it from the Stock view later. This matches the physical
    reality of a freshly-arrived bag (count is known, bin isn't yet)."""
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(),
    )
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R", "quantity": 50}]},
    )
    row = r.json()["data"]["rows"][0]
    assert row["status"] == "created"
    assert row["quantity_added"] == 50
    # And the stock count reflects the bag's qty.
    p = authed.get(f"/api/parts/{row['part_id']}").json()["data"]
    assert p["on_hand"] == 50


# ---------------------------------------------------------------------------
# Per-row outcomes
# ---------------------------------------------------------------------------


def test_bulk_import_marks_duplicate_when_mpn_already_in_workspace(authed, monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(),
    )
    # First call creates the part.
    first = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}]},
    )
    first_part_id = first.json()["data"]["rows"][0]["part_id"]

    # Second call with the same MPN must NOT create a duplicate.
    second = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}]},
    )
    body = second.json()["data"]
    assert body["summary"]["created"] == 0
    assert body["summary"]["duplicate"] == 1
    row = body["rows"][0]
    assert row["status"] == "duplicate"
    assert row["part_id"] == first_part_id


def test_bulk_import_marks_lookup_failed_when_no_match(authed, monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {"Errors": [], "SearchResults": {"Parts": []}},
    )
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "NOSUCHTHING"}]},
    )
    body = r.json()["data"]
    assert body["summary"]["lookup_failed"] == 1
    assert body["rows"][0]["status"] == "lookup_failed"
    assert "no match" in body["rows"][0]["error"].lower()


def test_bulk_import_mixed_batch_returns_per_row_status(authed, monkeypatch):
    # Provider returns a hit for the first MPN, no match for the second,
    # and again a hit for the third (pre-existing duplicate of the first).
    def fake_post(url, payload):
        mpn = payload["SearchByPartRequest"]["mouserPartNumber"]
        if mpn == "MISS-ME":
            return {"Errors": [], "SearchResults": {"Parts": []}}
        return _stub_mouser_response(mpn=mpn)

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser", fake_post
    )
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                {"mpn": "RC0402JR-070R"},
                {"mpn": "MISS-ME"},
                {"mpn": "RC0402JR-070R"},  # duplicates the first, post-creation
            ]
        },
    )
    body = r.json()["data"]
    statuses = [row["status"] for row in body["rows"]]
    assert statuses == ["created", "lookup_failed", "duplicate"]
    assert body["summary"] == {
        "created": 1,
        "duplicate": 1,
        "bag_rescan": 0,
        "bag_signature_mismatch": 0,
        "lookup_failed": 1,
        "invalid": 0,
        "row_failed": 0,
        "deadline_exceeded": 0,
        "needs_disambiguation": 0,
    }


# ---------------------------------------------------------------------------
# Top-level error paths
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-row savepoints (Sec CRIT-6) — a single row's unanticipated write
# failure must not roll back the rest of the batch.
# ---------------------------------------------------------------------------


def test_bulk_import_row_failure_does_not_roll_back_other_rows(authed, monkeypatch):
    """Force the second row's write step to raise something OTHER than
    StockError (which is caught inline), then verify rows 1 and 3
    persisted while row 2 was savepoint-rolled-back."""
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(
            mpn=payload["SearchByPartRequest"]["mouserPartNumber"]
        ),
    )

    # Trigger a mid-row failure on row 2. We patch the per-row helper
    # to raise on its second invocation — easier than crafting a
    # legitimate IntegrityError mid-flight, and exercises the same
    # savepoint-rollback path.
    import app.api.routes.parts_scan as parts_mod

    real_helper = parts_mod._import_one_scan_row
    call_count = {"n": 0}

    def flaky_helper(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated mid-row crash")
        return real_helper(*args, **kwargs)

    monkeypatch.setattr(parts_mod, "_import_one_scan_row", flaky_helper)

    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                {"mpn": "RC0402JR-070R", "quantity": 1},
                {"mpn": "RC0402JR-070K", "quantity": 1},
                {"mpn": "RC0402JR-070M", "quantity": 1},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    statuses = [row["status"] for row in body["rows"]]
    assert statuses == ["created", "row_failed", "created"]
    assert body["summary"]["row_failed"] == 1
    assert body["summary"]["created"] == 2

    # Surviving rows must actually be in the DB — not just claimed
    # in the response.
    listed = authed.get("/api/parts").json()["data"]
    mpns = {p["mpn"] for p in listed}
    assert "RC0402JR-070R" in mpns
    assert "RC0402JR-070M" in mpns
    # Row 2's MPN must NOT be in the DB (savepoint rolled back).
    assert "RC0402JR-070K" not in mpns


def test_bulk_import_provider_exception_does_not_abort_batch(authed, monkeypatch):
    """Pre-existing behaviour: a provider exception on one row marks
    that row lookup_failed, the rest of the batch still processes.
    Pinned here to prevent regression from the savepoint refactor."""
    call_count = {"n": 0}

    def flaky_post(url, payload):
        call_count["n"] += 1
        mpn = payload["SearchByPartRequest"]["mouserPartNumber"]
        if call_count["n"] == 2:
            raise RuntimeError("simulated provider crash")
        return _stub_mouser_response(mpn=mpn)

    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser", flaky_post)

    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                {"mpn": "RC0402JR-070R"},
                {"mpn": "RC0402JR-070K"},
                {"mpn": "RC0402JR-070M"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    statuses = [row["status"] for row in r.json()["data"]["rows"]]
    # Provider exceptions resolve as per-row lookup_failed results; the
    # surrounding batch keeps processing — that's the load-bearing pin.
    assert statuses[0] == "created"
    assert statuses[1] == "lookup_failed"
    assert statuses[2] == "created"


def test_bulk_import_fails_when_no_provider_configured():
    c = TestClient(app)
    _signup(c)
    # No PATCH to set a provider.
    r = c.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "ANY"}]},
    )
    assert r.status_code == 400
    assert "provider" in r.json()["status"]["message"]


def test_bulk_import_rejects_empty_rows(authed):
    r = authed.post("/api/parts/bulk-import-from-scan", json={"rows": []})
    assert r.status_code == 422


def test_bulk_import_rejects_extra_fields(authed):
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "X", "color": "blue"}]},
    )
    assert r.status_code == 422
