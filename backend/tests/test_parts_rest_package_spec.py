"""edaflow SM-016 — `package_spec` on the parts REST record.

netsch NS-026 compares a part's case size with its footprint and reads
parts over REST, which carried no specs. `package_spec` is
`{"package", "case"}` from stored rows only — always both keys, null when
absent, no provider call.
"""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import signup_user


def _authed() -> TestClient:
    c = TestClient(app)
    signup_user(c)
    return c


def _part(c: TestClient, mpn: str) -> str:
    r = c.post("/api/parts", json={"name": mpn, "part_type": "local", "mpn": mpn})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _field(c: TestClient, part_id: str, key: str, value: str) -> None:
    r = c.post("/api/custom-fields", json={
        "object_type": "part", "object_id": part_id, "key": key, "value": value,
    })
    assert r.status_code in (200, 201), r.text


def test_single_and_list_carry_package_and_case(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("package_spec must not call a provider")
    monkeypatch.setattr(httpx.Client, "send", no_network)

    c = _authed()
    with_pkg = _part(c, "CRCW0402100RFKED")
    _field(c, with_pkg, "package", "0402 (1005 Metric)")
    bare = _part(c, "NOPKG-1")

    single = c.get(f"/api/parts/{with_pkg}").json()["data"]
    assert single["package_spec"] == {"package": "0402 (1005 Metric)", "case": None}
    assert c.get(f"/api/parts/{bare}").json()["data"]["package_spec"] == {
        "package": None, "case": None,
    }

    rows = {r["id"]: r for r in c.get("/api/parts").json()["data"]}
    assert rows[with_pkg]["package_spec"] == {"package": "0402 (1005 Metric)", "case": None}
    assert rows[bare]["package_spec"] == {"package": None, "case": None}
    by_mpn = c.get("/api/parts", params={"mpn": "CRCW0402100RFKED"}).json()["data"]
    assert by_mpn[0]["package_spec"]["package"] == "0402 (1005 Metric)"


def test_a_hand_entered_case_is_returned():
    c = _authed()
    pid = _part(c, "CASE-ONLY")
    _field(c, pid, "case", "1206")
    assert c.get(f"/api/parts/{pid}").json()["data"]["package_spec"] == {
        "package": None, "case": "1206",
    }


def test_spec_columns_key_is_untouched():
    c = _authed()
    _part(c, "X-1")
    row = c.get("/api/parts").json()["data"][0]
    assert "specs" not in row, "`specs` stays reserved for ?spec_columns="
