"""Box audit (phase 15): count scanned bags, write off / move the rest."""
from __future__ import annotations

from tests._box_scan_helpers import bag_fields, bags, box, intake, part, raw_bag, signup_client


def _audit(c, box_id, lines):
    return c.post(f"/api/storage/{box_id}/audit", json={"lines": lines})


def _setup_three_bags(c):
    b = box(c, "Box A")
    pid = part(c, "LM358")
    lots = []
    for qty in (10, 20, 30):
        r = intake(c, b, mpn="LM358", quantity=qty, **bag_fields(raw_bag("LM358", qty)))
        lots.append(r.json()["data"]["lot_id"])
    return b, pid, lots


def test_count_write_off_and_unchanged():
    c = signup_client(provider=False)
    b, pid, (l10, l20, l30) = _setup_three_bags(c)

    r = _audit(c, b, [
        {"part_id": pid, "lot_id": l10, "action": "count", "actual_quantity": 8},
        {"part_id": pid, "lot_id": l20, "action": "count", "actual_quantity": 20},
        {"part_id": pid, "lot_id": l30, "action": "write_off"},
    ])

    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"count": 1, "write_off": 1, "move": 0, "unchanged": 1}
    assert sorted(row["quantity"] for row in bags(c, b)) == [8, 20]


def test_move_unscanned_bag_to_another_box():
    c = signup_client(provider=False)
    b, pid, (l10, _l20, _l30) = _setup_three_bags(c)
    other = box(c, "Unsorted")

    r = _audit(c, b, [
        {"part_id": pid, "lot_id": l10, "action": "move", "to_storage_location_id": other},
    ])

    assert r.status_code == 200, r.text
    assert [row["quantity"] for row in bags(c, other)] == [10]
    assert len(bags(c, b)) == 2


def test_untracked_null_lot_stock_can_be_written_off():
    c = signup_client(provider=False)
    b = box(c, "Box A")
    pid = part(c, "LM358")
    r = c.post("/api/stock/add", json={"part_id": pid, "quantity": 77, "storage_location_id": b})
    assert r.status_code == 200, r.text
    contents = bags(c, b)
    assert contents[0]["lot"] is None and contents[0]["bag_signatures"] == []

    r = _audit(c, b, [{"part_id": pid, "lot_id": None, "action": "write_off"}])

    assert r.status_code == 200, r.text
    assert bags(c, b) == []


def test_audit_is_all_or_nothing():
    c = signup_client(provider=False)
    b, pid, (l10, _l20, _l30) = _setup_three_bags(c)

    r = _audit(c, b, [
        {"part_id": pid, "lot_id": l10, "action": "write_off"},
        {"part_id": pid, "lot_id": l10, "action": "count"},  # missing actual_quantity
    ])

    assert r.status_code == 400
    assert sorted(row["quantity"] for row in bags(c, b)) == [10, 20, 30]


def test_audit_writes_audit_log():
    c = signup_client(provider=False)
    b, pid, (l10, _l20, _l30) = _setup_three_bags(c)

    _audit(c, b, [{"part_id": pid, "lot_id": l10, "action": "write_off"}])

    rows = c.get("/api/audit").json()["data"]
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert any(r["action"] == "storage.audited" for r in rows)


def test_other_workspace_cannot_audit_or_move_into_foreign_box():
    a = signup_client(provider=False)
    other = signup_client(provider=False)
    b, pid, (l10, _l20, _l30) = _setup_three_bags(a)
    foreign_box = box(other, "Theirs")
    own_box = box(other, "Mine")

    write_off = [{"part_id": pid, "lot_id": l10, "action": "write_off"}]
    assert _audit(other, b, write_off).status_code == 404
    assert _audit(a, b, [{
        "part_id": pid, "lot_id": l10, "action": "move", "to_storage_location_id": foreign_box,
    }]).status_code == 404
    # A foreign part against one's own box changes nothing.
    assert _audit(other, own_box, write_off).status_code == 400
    assert sorted(row["quantity"] for row in bags(a, b)) == [10, 20, 30]
