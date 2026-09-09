"""The parts LIST payload — the fields the `/parts` table can column on.

Two things are pinned here.

**The row contract.** `web/src/routes/parts/partsColumns.tsx` builds a
column per field on the list row; a field that quietly stops being
serialized turns into a blank column rather than an error, so the shape
is asserted here rather than discovered in the UI.

**`provider_links` is batched.** Lists now carry the link rows so the
"Distributors" column can link out, and the obvious implementation —
`provider_links_for` in the per-row loop — is an N+1 on the busiest
endpoint in the app: a 200-row page would fire 200 extra round-trips.
`test_provider_links_do_not_scale_with_row_count` compares the query
count for a small page against a large one, which fails on any per-row
query while tolerating an unrelated extra lookup.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.domain.parts.provider_links import upsert_link
from app.main import app
from tests._factories import create_part, signup_user

# Every key a parts-list column reads. `provider_links` is checked
# separately (its *presence* is the B2 change; its content needs rows).
ROW_FIELDS = [
    "id",
    "name",
    "part_type",
    "manufacturer",
    "mpn",
    "internal_part_number",
    "footprint",
    "category_id",
    "linked_provider",
    "linked_external_id",
    "last_refresh_at",
    "updated_at",
    "on_hand",
    "reserved",
    "available",
    "published",
    "serialized",
    "low_stock_report_quantity",
    "image_url",
]


def _ws(c: TestClient, email: str | None = None) -> uuid.UUID:
    return uuid.UUID(signup_user(c, email=email).json()["data"]["workspace_id"])


def _rows(c: TestClient, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in {"paged": "true", **params}.items())
    r = c.get(f"/api/parts?{query}")
    assert r.status_code == 200, r.text
    return r.json()["data"]["items"]


def _row_for(c: TestClient, part_id: str) -> dict:
    row = next((p for p in _rows(c) if p["id"] == part_id), None)
    assert row is not None, "part missing from its own workspace's list"
    return row


@pytest.fixture
def authed(db) -> TestClient:
    c = TestClient(app)
    _ws(c)
    return c


# ---------------------------------------------------------------------
# B1 — the row contract
# ---------------------------------------------------------------------


def test_list_row_carries_every_column_field(authed):
    part_id = create_part(
        authed,
        "LC-1 resistor",
        mpn="RC0402-1",
        manufacturer="Yageo",
        internal_part_number="IPN-0001",
        footprint="R_0402",
        low_stock_report_quantity=25,
        serialized=False,
    )
    row = _row_for(authed, part_id)
    missing = [f for f in ROW_FIELDS if f not in row]
    assert missing == [], f"list row lost fields the parts table columns on: {missing}"
    assert row["internal_part_number"] == "IPN-0001"
    assert row["footprint"] == "R_0402"
    assert row["low_stock_report_quantity"] == 25


def test_updated_at_is_present_and_tracks_the_last_change(authed):
    """"Last change" needs no column of its own — the `WorkspaceOwned`
    mixin already maintains `updated_at` with an `onupdate`. This test is
    what says so: if a future change serializes `created_at` under the
    name, or the mixin loses the `onupdate`, the value stops moving."""
    part_id = create_part(authed, "LC-2 capacitor")
    before = _row_for(authed, part_id)["updated_at"]
    assert before is not None

    r = authed.patch(f"/api/parts/{part_id}", json={"description": "touched"})
    assert r.status_code == 200, r.text

    after = _row_for(authed, part_id)["updated_at"]
    assert after > before, f"updated_at did not move on PATCH ({before} -> {after})"


def test_category_id_is_on_the_row_so_the_name_can_be_resolved_client_side(authed):
    """The Category column renders a *name*, but the row carries only the
    id — the frontend joins it against the category list it already
    fetches for the rail. That trade only holds while the id is here."""
    r = authed.post("/api/categories", json={"name": "Passives"})
    assert r.status_code in (200, 201), r.text
    category_id = r.json()["data"]["id"]

    part_id = create_part(authed, "LC-3 filed part", category_id=category_id)
    assert _row_for(authed, part_id)["category_id"] == category_id


# ---------------------------------------------------------------------
# B2 — provider links on list rows
# ---------------------------------------------------------------------


def test_list_rows_carry_provider_links(authed, db):
    part_id = create_part(authed, "LC-4 linked part", mpn="RC0402-4")
    ws_id = uuid.UUID(authed.get("/api/workspaces/current").json()["data"]["id"])
    for provider, url in (
        ("digikey", "https://www.digikey.com/en/products/detail/x/1"),
        ("mouser", "https://www.mouser.com/ProductDetail/x"),
    ):
        upsert_link(
            db,
            workspace_id=ws_id,
            part_id=uuid.UUID(part_id),
            user_id=None,
            provider=provider,
            external_id=f"{provider}-ext",
            source_url=url,
        )
    db.commit()

    row = _row_for(authed, part_id)
    links = {link["provider"]: link for link in row["provider_links"]}
    assert sorted(links) == ["digikey", "mouser"]
    assert links["digikey"]["source_url"].startswith("https://www.digikey.com/")
    assert links["mouser"]["external_id"] == "mouser-ext"


def test_a_part_with_no_links_gets_an_empty_list_not_a_missing_key(authed):
    """"Looked, found none" and "did not look" are different facts, and
    the frontend schema keeps `provider_links` optional so it can tell
    them apart. A list row has now looked."""
    part_id = create_part(authed, "LC-5 unlinked part")
    assert _row_for(authed, part_id)["provider_links"] == []


def test_create_part_response_still_omits_provider_links(authed):
    """The other half of that distinction: a response that echoes a part
    without touching the link table must leave the key absent rather than
    send `[]`."""
    r = authed.post("/api/parts", json={"name": "LC-6 fresh part", "part_type": "local"})
    assert r.status_code == 201, r.text
    assert "provider_links" not in r.json()["data"]


def test_provider_links_do_not_scale_with_row_count(authed, db, engine):
    """An N+1 would add one query per row. Comparing two page sizes
    rather than asserting an exact count keeps this from breaking on an
    unrelated extra lookup while still catching any per-row query."""
    ws_id = uuid.UUID(authed.get("/api/workspaces/current").json()["data"]["id"])

    def add_parts(prefix: str, count: int) -> None:
        for index in range(count):
            part_id = create_part(authed, f"{prefix}-{index}", mpn=f"{prefix}-{index}")
            upsert_link(
                db,
                workspace_id=ws_id,
                part_id=uuid.UUID(part_id),
                user_id=None,
                provider="digikey",
                external_id=f"{prefix}-{index}",
                source_url=f"https://www.digikey.com/en/products/detail/{prefix}/{index}",
            )
        db.commit()

    def query_count(expect: int) -> int:
        count = 0

        def _on_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal count
            count += 1

        # Listen on the conftest `engine`, NOT `infra.db.get_engine()` —
        # the test session is bound to a connection off the former, and a
        # listener on the latter counts nothing, which would make the
        # comparison below pass for the wrong reason.
        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            rows = _rows(authed, limit=200)
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)
        assert len(rows) == expect, f"expected {expect} rows, got {len(rows)}"
        assert count > 0, "the counter saw nothing — it is on the wrong engine"
        return count

    add_parts("LC7-small", 2)
    small = query_count(2)
    add_parts("LC7-large", 18)
    large = query_count(20)

    # Ten times the rows, every one of them linked. A per-row
    # `provider_links_for` would be +18 here; the tolerance of one only
    # absorbs savepoint bookkeeping, which is not row-count-driven.
    assert large <= small + 1, (
        f"listing issued {large} queries for 20 linked parts vs {small} for 2 — "
        "provider-link loading has regressed into an N+1"
    )


def test_provider_links_never_cross_a_workspace_boundary(db):
    """Workspace isolation is enforced in code, not the DB (CLAUDE.md).
    The batched link query takes a set of part ids, so the predicate that
    keeps one workspace's links off another's rows is the explicit
    `workspace_id ==` — this test is what holds it there."""
    a = TestClient(app)
    ws_a = _ws(a, email=f"a-{uuid.uuid4().hex[:8]}@example.com")
    b = TestClient(app)
    ws_b = _ws(b, email=f"b-{uuid.uuid4().hex[:8]}@example.com")

    part_a = create_part(a, "LC-8 workspace A part", mpn="LC8-A")
    part_b = create_part(b, "LC-8 workspace B part", mpn="LC8-B")
    upsert_link(
        db,
        workspace_id=ws_a,
        part_id=uuid.UUID(part_a),
        user_id=None,
        provider="digikey",
        external_id="A-ONLY",
        source_url="https://www.digikey.com/en/products/detail/a/1",
    )
    upsert_link(
        db,
        workspace_id=ws_b,
        part_id=uuid.UUID(part_b),
        user_id=None,
        provider="mouser",
        external_id="B-ONLY",
        source_url="https://www.mouser.com/ProductDetail/b",
    )
    db.commit()

    rows_a = _rows(a)
    assert [row["id"] for row in rows_a] == [part_a]
    assert [link["external_id"] for link in rows_a[0]["provider_links"]] == ["A-ONLY"]

    rows_b = _rows(b)
    assert [row["id"] for row in rows_b] == [part_b]
    assert [link["external_id"] for link in rows_b[0]["provider_links"]] == ["B-ONLY"]
