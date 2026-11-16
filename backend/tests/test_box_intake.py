"""Box intake (phase 15): scan bags into a box, one lot per bag."""
from __future__ import annotations

from tests._box_scan_helpers import (
    bag_fields,
    bags,
    box,
    intake,
    mouser_stub,
    no_match,
    part,
    raw_bag,
    signup_client,
)


def test_existing_part_gets_stock_as_its_own_lot():
    c = signup_client(provider=False)
    b = box(c, "Box A")
    pid = part(c, "LM358")
    raw1, raw2 = raw_bag("LM358", 100), raw_bag("LM358", 50)

    r1 = intake(c, b, mpn="LM358", quantity=100, **bag_fields(raw1))
    r2 = intake(c, b, mpn="LM358", quantity=40, **bag_fields(raw2))  # counted, not the label's 50

    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["status"] == "added"
    assert r1.json()["data"]["part"]["id"] == pid
    contents = bags(c, b)
    assert sorted(row["quantity"] for row in contents) == [40, 100]
    assert all(row["lot"] is not None and len(row["bag_signatures"]) == 1 for row in contents)
    assert r2.json()["data"]["lot_id"] != r1.json()["data"]["lot_id"]


def test_unknown_mpn_is_created_from_provider(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: mouser_stub("RC0402JR-070R"),
    )
    c = signup_client()
    b = box(c, "Box A")

    r = intake(c, b, mpn="RC0402JR-070R", quantity=5000, lot_name="Lot X1")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["part_created"] is True
    assert data["part"]["mpn"] == "RC0402JR-070R"
    assert bags(c, b)[0]["lot"]["name"] == "Lot X1"


def test_unknown_mpn_without_provider_match_is_404(monkeypatch):
    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser", no_match)
    c = signup_client()
    b = box(c, "Box A")

    r = intake(c, b, mpn="NOPE-123", quantity=1)

    assert r.status_code == 404
    assert bags(c, b) == []


def test_rescanning_same_bag_is_idempotent_but_corrects_count():
    c = signup_client(provider=False)
    b = box(c, "Box A")
    part(c, "LM358")
    raw = raw_bag("LM358", 100)

    intake(c, b, mpn="LM358", quantity=100, **bag_fields(raw))
    again = intake(c, b, mpn="LM358", quantity=90, **bag_fields(raw))

    assert again.json()["data"]["status"] == "already_here"
    contents = bags(c, b)
    assert len(contents) == 1 and contents[0]["quantity"] == 90


def test_bag_recorded_elsewhere_is_moved_not_duplicated():
    c = signup_client(provider=False)
    old, new = box(c, "Old"), box(c, "New")
    part(c, "LM358")
    raw = raw_bag("LM358", 100)
    intake(c, old, mpn="LM358", quantity=100, **bag_fields(raw))

    resolved = c.post(f"/api/storage/{new}/scan/resolve", json={"mpn": "LM358", **bag_fields(raw)})
    moved = intake(c, new, mpn="LM358", quantity=100, **bag_fields(raw))

    assert resolved.json()["data"]["status"] == "bag_elsewhere"
    assert resolved.json()["data"]["bag"]["locations"][0]["name"] == "Old"
    assert moved.json()["data"]["status"] == "moved"
    assert bags(c, old) == []
    assert [row["quantity"] for row in bags(c, new)] == [100]


def test_add_new_keeps_the_other_bag_in_place():
    c = signup_client(provider=False)
    old, new = box(c, "Old"), box(c, "New")
    part(c, "LM358")
    raw = raw_bag("LM358", 100)
    intake(c, old, mpn="LM358", quantity=100, **bag_fields(raw))

    r = intake(c, new, mpn="LM358", quantity=100, on_existing="add_new", **bag_fields(raw))

    assert r.json()["data"]["status"] == "added"
    assert len(bags(c, old)) == 1 and len(bags(c, new)) == 1


def test_resolve_states():
    c = signup_client(provider=False)
    b = box(c, "Box A")
    part(c, "LM358")
    raw = raw_bag("LM358", 10)

    def resolve(**body):
        return c.post(f"/api/storage/{b}/scan/resolve", json=body).json()["data"]["status"]

    assert resolve(mpn="LM358", **bag_fields(raw)) == "existing_part"
    assert resolve(mpn="UNKNOWN") == "not_found"
    intake(c, b, mpn="LM358", quantity=10, **bag_fields(raw))
    assert resolve(mpn="LM358", **bag_fields(raw)) == "bag_here"


def test_signature_mismatch_is_rejected():
    c = signup_client(provider=False)
    b = box(c, "Box A")
    part(c, "LM358")
    fields = bag_fields(raw_bag("LM358", 10))
    fields["raw_bag_code"] = "something else"

    r = intake(c, b, mpn="LM358", quantity=10, **fields)

    assert r.status_code == 422


def test_intake_writes_audit_log():
    c = signup_client(provider=False)
    b = box(c, "Box A")
    part(c, "LM358")

    intake(c, b, mpn="LM358", quantity=10)

    rows = c.get("/api/audit").json()["data"]
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert any(r["action"] == "storage.intake.added" for r in rows)


def test_other_workspace_cannot_use_box_or_part():
    a = signup_client(provider=False)
    b_client = signup_client(provider=False)
    box_a = box(a, "Box A")
    part_a = part(a, "LM358")
    box_b = box(b_client, "Box B")

    assert intake(b_client, box_a, mpn="LM358", quantity=1).status_code == 404
    assert b_client.get(f"/api/storage/{box_a}/bags").status_code == 404
    assert intake(b_client, box_b, part_id=part_a, quantity=1).status_code == 404
