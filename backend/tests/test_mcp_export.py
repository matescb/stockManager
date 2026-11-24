"""`export_parts` — complete, cursor-paged enumeration for audits (SM-006).

Driven through the real MCP client like the rest of `test_mcp*.py`. The
properties pinned here are the ones an audit depends on and that a
capped listing cannot give: every active part exactly once, nothing
repeated when the inventory grows mid-walk, real EDA hashes and ledger
figures on each item, and no part from another workspace ever.
"""
from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.pagination import Cursor, encode_cursor
from app.domain.eda.models import (
    EdaDatafile,
    EdaFootprint,
    EdaFootprintModel,
    EdaSymbol,
)
from app.domain.parts.models import Part
from app.main import app
from app.mcp.tools.export import MAX_PAGE_SIZE
from tests._factories import create_part, create_storage, signup_user
from tests.test_eda import SPICE_BYTES
from tests.test_mcp import (
    _b64,
    _mint,
    _snapeda_zip,
    call,
    call_error,
    mcp_session,
)


async def _walk(session, **kwargs) -> list[list[dict]]:
    pages: list[list[dict]] = []
    cursor = None
    while True:
        args = dict(kwargs)
        if cursor is not None:
            args["cursor"] = cursor
        page = await call(session, "export_parts", **args)
        pages.append(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return pages
        assert len(pages) < 100, "cursor never terminated"


async def test_walk_visits_every_active_part_exactly_once(authed_client, db):
    """Acceptance 1: > 3 pages, union == active part count, stable order."""
    ids = {create_part(authed_client, f"Walk {i:02d}") for i in range(11)}
    archived = create_part(authed_client, "Walk archived")
    r = authed_client.post(f"/api/parts/{archived}/archive")
    assert r.status_code == 200, r.text
    token = _mint(authed_client, read_only=True)["token"]

    async with mcp_session(token) as s:
        pages = await _walk(s, page_size=3)
        again = await _walk(s, page_size=3)
        with_archived = await _walk(s, page_size=5, include_archived=True)

    assert len(pages) == 4  # 3 + 3 + 3 + 2
    assert all(len(page) == 3 for page in pages[:-1])
    seen = [item["id"] for page in pages for item in page]
    assert len(seen) == len(set(seen)) == 11
    assert set(seen) == ids
    assert seen == sorted(seen, key=uuid.UUID)
    # Same order on a second walk: the key is stable.
    assert seen == [item["id"] for page in again for item in page]

    everything = {item["id"]: item for page in with_archived for item in page}
    assert set(everything) == ids | {archived}
    assert everything[archived]["archived"] is True
    assert "archived" not in everything[next(iter(ids))]


async def test_parts_inserted_mid_walk_are_never_repeated(authed_client, db):
    """Acceptance 2: concurrent inserts may be missed, never served twice."""
    for i in range(8):
        create_part(authed_client, f"Before {i}")
    token = _mint(authed_client, read_only=True)["token"]

    seen: list[str] = []
    inserted: set[str] = set()
    async with mcp_session(token) as s:
        cursor = None
        while True:
            args = {"page_size": 3}
            if cursor:
                args["cursor"] = cursor
            page = await call(s, "export_parts", **args)
            seen += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
            # Two inserts between every pair of calls; uuid4 ids land on
            # both sides of the cursor.
            inserted |= {create_part(authed_client, f"During {uuid.uuid4().hex[:6]}")}
            inserted |= {create_part(authed_client, f"During {uuid.uuid4().hex[:6]}")}

    assert len(seen) == len(set(seen)), "a part was served twice"
    originals = {
        str(p.id) for p in db.query(Part).all() if str(p.id) not in inserted
    }
    assert originals <= set(seen), "a part that existed throughout was missed"


async def test_items_carry_eda_hashes_and_ledger_figures(authed_client, db):
    """Acceptance 3: real EDA presence + sha256 and on_hand/reserved/available."""
    cat = authed_client.post("/api/categories", json={"name": "Export cat"})
    assert cat.status_code in (200, 201), cat.text
    category = cat.json()["data"]
    part_id = create_part(
        authed_client,
        "Exported",
        mpn="EXP-1",
        manufacturer="Acme",
        category_id=category["id"],
    )
    bare_id = create_part(authed_client, "Bare")
    storage = create_storage(authed_client, "Export bin")
    r = authed_client.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 40, "storage_location_id": storage},
    )
    assert r.status_code == 200, r.text
    project = authed_client.post("/api/projects", json={"name": "Export board"})
    project_id = project.json()["data"]["id"]
    entry = authed_client.post(
        f"/api/projects/{project_id}/entries",
        json={"part_id": part_id, "quantity": 3},
    )
    assert entry.status_code in (200, 201), entry.text
    build = authed_client.post(
        "/api/builds",
        json={"name": "Export build", "project_id": project_id, "quantity": 4},
    )
    assert build.status_code == 201, build.text

    full = _mint(authed_client)["token"]
    async with mcp_session(full) as s:
        imported = await call(
            s, "import_vendor_zip", part_id=part_id, content_base64=_b64(_snapeda_zip("EXPP"))
        )
        spice = await call(
            s,
            "upload_eda_asset",
            kind="spice",
            filename="EXPP.lib",
            content_base64=_b64(SPICE_BYTES),
            part_id=part_id,
        )
        levels = await call(s, "stock_levels", part_id=part_id)
    assert imported["part_eda_updated"] is True
    assert spice["part_eda_updated"] is True

    readonly = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(readonly) as s:
        page = await call(s, "export_parts")
        stray = await call_error(s, "export_parts", cursor="not-a-cursor")
    assert page["next_cursor"] is None
    items = {item["id"]: item for item in page["items"]}
    item, bare = items[part_id], items[bare_id]

    assert item["name"] == "Exported"
    assert item["mpn"] == "EXP-1"
    assert item["manufacturer"] == "Acme"
    assert item["category"] == {
        "id": category["id"],
        "name": "Export cat",
        "path": "Export cat",
    }
    assert bare["category"] is None and bare["mpn"] is None

    # Hosted files are content-addressed; the hash is the one stored.
    eda = item["eda"]
    assert {k: v["present"] for k, v in eda.items()} == {
        "symbol": True,
        "footprint": True,
        "model3d": True,
        "spice": True,
    }
    assert all(len(v["sha256"]) == 64 for v in eda.values())
    assert eda["spice"]["sha256"] == spice["sha256"]
    assert eda["spice"]["sha256"] == hashlib.sha256(SPICE_BYTES).hexdigest()
    symbol = db.get(EdaSymbol, uuid.UUID(imported["symbols"][0]["id"]))
    footprint = db.get(EdaFootprint, uuid.UUID(imported["footprints"][0]["id"]))
    model = (
        db.query(EdaDatafile)
        .join(EdaFootprintModel, EdaFootprintModel.datafile_id == EdaDatafile.id)
        .filter(EdaFootprintModel.footprint_id == footprint.id)
        .one()
    )
    assert eda["symbol"]["sha256"] == symbol.sha256
    assert eda["footprint"]["sha256"] == footprint.sha256
    assert eda["model3d"]["sha256"] == model.sha256
    assert bare["eda"] == {
        k: {"present": False} for k in ("symbol", "footprint", "model3d", "spice")
    }

    # 40 in, 12 reserved for the 4-board build: the ledger, not a column.
    assert item["stock"] == {"on_hand": 40, "reserved": 12, "available": 28}
    assert {k: levels["parts"][0][k] for k in ("on_hand", "reserved", "available")} == (
        item["stock"]
    )
    assert bare["stock"] == {"on_hand": 0, "reserved": 0, "available": 0}

    assert "mcp.invalid_cursor" in stray


async def test_external_refs_report_present_without_a_hash(authed_client):
    part_id = create_part(authed_client, "Local lib")
    full = _mint(authed_client)["token"]
    async with mcp_session(full) as s:
        await call(
            s,
            "set_part_eda",
            part_id=part_id,
            symbol_ref_external="Device:R",
            footprint_ref_external="Resistor_SMD:R_0603_1608Metric",
        )
        page = await call(s, "export_parts")
    eda = page["items"][0]["eda"]
    assert eda["symbol"] == {"present": True, "ref_external": "Device:R"}
    assert eda["footprint"] == {
        "present": True,
        "ref_external": "Resistor_SMD:R_0603_1608Metric",
    }
    assert eda["model3d"] == {"present": False}


async def test_another_workspace_never_appears(authed_client, db):
    """Acceptance 4: workspace-scoped; read-only token suffices."""
    mine = {create_part(authed_client, f"Mine {i}") for i in range(3)}
    other = TestClient(app)
    signup_user(other)
    theirs = {create_part(other, f"Theirs {i}") for i in range(4)}
    their_token = _mint(other, read_only=True)["token"]
    my_token = _mint(authed_client, read_only=True)["token"]

    async with mcp_session(my_token) as s:
        pages = await _walk(s, page_size=2, include_archived=True)
    seen = {item["id"] for page in pages for item in page}
    assert seen == mine and not seen & theirs

    # A cursor minted for the other workspace only seeks; it cannot widen
    # the workspace filter.
    async with mcp_session(their_token) as s:
        first = await call(s, "export_parts", page_size=1)
        rest = await _walk(s, page_size=10)
    assert {i["id"] for p in rest for i in p} == theirs
    async with mcp_session(my_token) as s:
        borrowed = await call(s, "export_parts", cursor=first["next_cursor"])
    assert not {i["id"] for i in borrowed["items"]} & theirs


async def test_page_size_is_clamped(authed_client):
    """Acceptance 5: over the ceiling is clamped to 200, and says so."""
    create_part(authed_client, "Clamp")
    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        big = await call(s, "export_parts", page_size=10_000)
        small = await call(s, "export_parts", page_size=0)
    assert big["page_size"] == MAX_PAGE_SIZE == 200
    assert small["page_size"] == 1


async def test_foreign_cursors_are_refused(authed_client):
    """A REST cursor is HMAC-valid but names another ordering."""
    create_part(authed_client, "Cursor")
    token = _mint(authed_client, read_only=True)["token"]
    rest_cursor = encode_cursor(Cursor(id=uuid.uuid4(), sort_key="Cursor"))
    unscoped = encode_cursor(Cursor(id=uuid.uuid4()))
    async with mcp_session(token) as s:
        for bad in (rest_cursor, unscoped, "abc.def", rest_cursor + "x"):
            message = await call_error(s, "export_parts", cursor=bad)
            assert "mcp.invalid_cursor" in message, message


async def test_query_count_does_not_grow_with_page_size(authed_client, db):
    """Batched lookups: a page of 12 costs what a page of 2 costs."""
    for i in range(12):
        create_part(authed_client, f"Q {i}")
    token = _mint(authed_client, read_only=True)["token"]
    engine = db.get_bind().engine
    counts: list[int] = []

    def counter(*_args, **_kwargs):
        # The session handshake also queries (token auth); only count
        # once a measured call has started.
        if counts:
            counts[-1] += 1

    event.listen(engine, "before_cursor_execute", counter)
    try:
        async with mcp_session(token) as s:
            # The first call pays one-off costs (token telemetry); warm up.
            for size in (1, 2, 12):
                counts.append(0)
                await call(s, "export_parts", page_size=size)
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    assert counts[1] == counts[2], counts
