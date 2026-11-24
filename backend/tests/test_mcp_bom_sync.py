"""`sync_project_bom` — the MCP twin of `POST /api/projects/{id}/bom/sync`.

The diff itself is pinned in `test_bom_sync.py`; this file pins what the
MCP door adds: the write gate, the same result as REST, and the read
tools ignoring a line the sync retired.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.domain.audit.models import AuditLog
from tests._factories import create_part
from tests.test_mcp import _mint, call, call_error, mcp_session


def _project(client, name: str = "MCP board") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _rows(part_id: str) -> list[dict]:
    return [
        {"designators": ["R1", "R2"], "value": "10k", "id_code": f"https://parts.example/parts/{part_id}"},
        {"designators": "U1", "value": "MCU", "mpn": "NO-SUCH-MPN"},
    ]


async def test_read_only_token_is_refused(authed_client, db):
    project = _project(authed_client)
    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        text = await call_error(s, "sync_project_bom", project_id=project, rows=[], dry_run=True)
    assert "auth.token_read_only" in text


async def test_sync_matches_rest_and_readers_skip_removed_lines(authed_client, db):
    c = authed_client
    part = create_part(c, "10k 0603", mpn="RC0603-10K")
    stock = c.post("/api/stock/add", json={"part_id": part, "quantity": 1})
    assert stock.status_code == 200, stock.text
    project = _project(c)
    token = _mint(c)["token"]

    async with mcp_session(token) as s:
        preview = await call(
            s, "sync_project_bom", project_id=project, rows=_rows(part), dry_run=True
        )
        assert preview["dry_run"] is True and preview["added"] == 2
        assert (await call(s, "get_project_bom", project_id=project))["lines"] == []

        out = await call(s, "sync_project_bom", project_id=project, rows=_rows(part))
        assert (out["added"], out["unmatched"]) == (2, 1)
        again = await call(s, "sync_project_bom", project_id=project, rows=_rows(part))
        assert again["unchanged"] == 2

        shortages = await call(s, "bom_shortages", project_id=project)
        assert [row["part_id"] for row in shortages["shortages"]] == [part]

        # Drop R1/R2: archived, and gone from both read tools.
        removed = await call(s, "sync_project_bom", project_id=project, rows=_rows(part)[1:])
        assert removed["removed"] == 1
        bom = await call(s, "get_project_bom", project_id=project)
        assert [line["designators"] for line in bom["lines"]] == [["U1"]]
        shortages = await call(s, "bom_shortages", project_id=project)
        assert shortages["shortages"] == []

    rest = c.post(
        f"/api/projects/{project}/bom/sync", json={"rows": _rows(part)[1:], "dry_run": True}
    )
    assert rest.status_code == 200, rest.text
    assert rest.json()["data"]["unchanged"] == 1

    actions = db.execute(
        select(AuditLog.action, func.count())
        .where(AuditLog.action.like("project_entry.sync_%"))
        .group_by(AuditLog.action)
    ).all()
    assert dict(actions) == {"project_entry.sync_added": 2, "project_entry.sync_removed": 1}


async def test_foreign_project_reads_as_not_found(authed_client, db):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests._factories import signup_user

    other = TestClient(app)
    signup_user(other)
    foreign = _project(other, "Theirs")
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        text = await call_error(s, "sync_project_bom", project_id=foreign, rows=[])
    assert "project.not_found" in text


async def test_invalid_row_is_a_coded_refusal(authed_client, db):
    project = _project(authed_client)
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        text = await call_error(
            s,
            "sync_project_bom",
            project_id=project,
            rows=[{"designators": ["R1"], "quantity": -1}],
        )
    assert "bom.sync_invalid_row" in text
