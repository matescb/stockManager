"""edaflow SM-019 — a hosted footprint's size on `GET /api/parts/{id}/eda`.

netsch estimates board area and cannot open KiCad, so the part's EDA record
carries `courtyard_mm: {w, h, source}` (F.CrtYd bounding box, else the pads')
and `footprint_file_url` for the `.kicad_mod` itself. Stored bytes only.
"""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import signup_user

_WITH_COURTYARD = """(footprint "L_XAL7030" (layer "F.Cu")
  (attr smd)
  (pad "1" smd rect (at -2.5 0) (size 1.5 6) (layers "F.Cu"))
  (pad "2" smd rect (at 2.5 0) (size 1.5 6) (layers "F.Cu"))
  (fp_rect (start -4 -3.75) (end 4 3.75) (layer "F.CrtYd") (width 0.05))
)
"""

_PADS_ONLY = """(footprint "R_0402" (layer "F.Cu")
  (pad "1" smd rect (at -0.5 0) (size 0.6 0.5) (layers "F.Cu"))
  (pad "2" smd rect (at 0.5 0) (size 0.6 0.5) (layers "F.Cu"))
)
"""


def _authed() -> TestClient:
    c = TestClient(app)
    signup_user(c)
    return c


def _part_with_footprint(c: TestClient, mpn: str, text: str | None) -> str:
    r = c.post("/api/parts", json={"name": mpn, "part_type": "local", "mpn": mpn})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]
    if text is None:
        body = {"footprint_ref_external": "Inductor_SMD:L_Custom"}
    else:
        up = c.post(
            "/api/eda/footprints",
            files={"file": (f"{mpn}.kicad_mod", text.encode(), "application/octet-stream")},
        )
        assert up.status_code in (200, 201), up.text
        body = {"footprint_id": up.json()["data"]["id"]}
    r = c.put(f"/api/parts/{pid}/eda", json=body)
    assert r.status_code == 200, r.text
    return pid


def test_courtyard_size_and_file_url_in_one_read(monkeypatch):
    c = _authed()
    pid = _part_with_footprint(c, "XAL7030-102MEC", _WITH_COURTYARD)

    def refuse(*a, **k):
        raise AssertionError("no outbound call")
    monkeypatch.setattr(httpx.Client, "send", refuse)

    data = c.get(f"/api/parts/{pid}/eda").json()["data"]
    assert data["courtyard_mm"] == {"w": 8.0, "h": 7.5, "source": "courtyard"}
    f = c.get(data["footprint_file_url"])
    assert f.status_code == 200
    assert b'(footprint "L_XAL7030"' in f.content
    assert "L_XAL7030.kicad_mod" in f.headers["content-disposition"]


def test_without_a_courtyard_the_pads_are_measured():
    c = _authed()
    pid = _part_with_footprint(c, "R0402-X", _PADS_ONLY)
    data = c.get(f"/api/parts/{pid}/eda").json()["data"]
    assert data["courtyard_mm"] == {"w": 1.6, "h": 0.5, "source": "pads"}


def test_an_external_footprint_answers_null():
    c = _authed()
    pid = _part_with_footprint(c, "EXT-1", None)
    data = c.get(f"/api/parts/{pid}/eda").json()["data"]
    assert data["courtyard_mm"] is None
    assert data["footprint_file_url"] is None
