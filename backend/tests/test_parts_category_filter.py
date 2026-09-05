"""`GET /api/parts?category_id=&include_descendants=`.

The load-bearing test here is
`test_category_filter_survives_a_page_boundary`. `list_parts`' cursor is an
HMAC-signed `(name, id)` seek position over whatever statement produced the
page (`core/pagination.py`), so the category predicate has to be part of
that statement. Filtering the returned page instead would hand back short
pages and, once a whole page's worth of rows failed the filter, an empty
page with a non-null `next_cursor` — rows vanishing from the middle of a
listing with nothing in the response to say so.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import create_part, signup_user


@pytest.fixture
def other_client(db):
    c = TestClient(app)
    signup_user(c)
    return c


def _category(client: TestClient, name: str, parent_id: str | None = None) -> str:
    body: dict = {"name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    r = client.post("/api/categories", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _names(client: TestClient, query: str) -> set[str]:
    r = client.get(f"/api/parts?{query}")
    assert r.status_code == 200, r.text
    return {p["name"] for p in r.json()["data"]}


@pytest.fixture
def tree(authed_client):
    """passives → {resistors → thin_film, capacitors}, plus a lone actives.

    One part filed at each node, named after it.
    """
    c = authed_client
    ids = {
        "passives": _category(c, "Passives"),
        "actives": _category(c, "Actives"),
    }
    ids["resistors"] = _category(c, "Resistors", ids["passives"])
    ids["capacitors"] = _category(c, "Capacitors", ids["passives"])
    ids["thin_film"] = _category(c, "Thin film", ids["resistors"])
    for key, cid in ids.items():
        create_part(c, name=f"part-{key}", category_id=cid)
    create_part(c, name="part-uncategorised")
    return ids


# ---------------------------------------------------------------------
# Descendant expansion
# ---------------------------------------------------------------------


def test_descendants_are_included_by_default(authed_client, tree):
    """The default matters: clicking a branch node and seeing nothing
    because every part is filed on its leaves is what makes a tree feel
    broken."""
    assert _names(authed_client, f"category_id={tree['passives']}") == {
        "part-passives",
        "part-resistors",
        "part-capacitors",
        "part-thin_film",
    }


def test_include_descendants_false_is_an_exact_match(authed_client, tree):
    assert _names(
        authed_client,
        f"category_id={tree['passives']}&include_descendants=false",
    ) == {"part-passives"}


def test_a_sibling_branch_is_excluded(authed_client, tree):
    assert _names(authed_client, f"category_id={tree['actives']}") == {
        "part-actives"
    }


def test_a_leaf_returns_only_itself(authed_client, tree):
    assert _names(authed_client, f"category_id={tree['thin_film']}") == {
        "part-thin_film"
    }


def test_no_category_filter_returns_everything(authed_client, tree):
    assert "part-uncategorised" in _names(authed_client, "limit=200")


def test_filter_composes_with_the_search_term(authed_client, tree):
    assert _names(
        authed_client, f"category_id={tree['passives']}&q=resistors"
    ) == {"part-resistors"}


def test_reparenting_changes_what_the_ancestor_returns(authed_client, tree):
    """The descendant set is resolved per-request from `parent_id`, so a
    move is reflected immediately with no denormalised path to rebuild."""
    r = authed_client.patch(
        f"/api/categories/{tree['resistors']}",
        json={"parent_id": tree["actives"]},
    )
    assert r.status_code == 200, r.text

    assert _names(authed_client, f"category_id={tree['passives']}") == {
        "part-passives",
        "part-capacitors",
    }
    assert _names(authed_client, f"category_id={tree['actives']}") == {
        "part-actives",
        "part-resistors",
        "part-thin_film",
    }


# ---------------------------------------------------------------------
# Pagination — the filter must run BEFORE paginate()
# ---------------------------------------------------------------------


def test_category_filter_survives_a_page_boundary(authed_client):
    """Walk a filtered listing across several pages.

    Completeness alone does NOT pin this. A post-`paginate` filter still
    reaches every matching row eventually — it advances the cursor over the
    *unfiltered* set and drops non-matches from each page after the fact,
    so a walk to exhaustion collects the same names. What it destroys is
    the page shape, and that is what is asserted here:

      * **Every page but the last is exactly `limit` rows.** That is the
        whole promise of a cursor-paged endpoint. Post-filtering yields
        pages of whatever survived, which for a 1-in-3 selectivity is
        1 or 2 rows out of 5.
      * **No page is empty while `next_cursor` is non-null.** Once the
        decoys outnumber the matches, post-filtering produces exactly that
        — and `items.length === 0` is what most clients (this repo's
        `useInfiniteQuery` included, via `getNextPageParam`) or a human
        reading the response treat as end-of-list.
      * **The walk takes ceil(matches / limit) pages**, not
        ceil(all_rows / limit).

    The decoys are interleaved by name (parts sort by `name, id`) so they
    land inside the matches' pages rather than after them.
    """
    c = authed_client
    parent = _category(c, "Passives")
    child = _category(c, "Resistors", parent)
    decoy_category = _category(c, "Actives")

    # 12 wanted rows, alternating between the branch node and its child,
    # interleaved with 24 rows that must never appear.
    page_size = 5
    wanted = set()
    for i in range(12):
        name = f"part-{i:02d}-wanted"
        create_part(c, name=name, category_id=parent if i % 2 else child)
        wanted.add(name)
        create_part(c, name=f"part-{i:02d}-decoy", category_id=decoy_category)
        create_part(c, name=f"part-{i:02d}-none")

    seen: list[str] = []
    page_sizes: list[int] = []
    cursor = None
    while True:
        url = f"/api/parts?paged=true&limit={page_size}&category_id={parent}"
        if cursor:
            url += f"&cursor={cursor}"
        r = c.get(url)
        assert r.status_code == 200, r.text
        page = r.json()["data"]
        page_sizes.append(len(page["items"]))
        seen.extend(p["name"] for p in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert page["items"], (
            "an empty page with a non-null next_cursor — the filter is "
            "running after paginate(), so the page is whatever survived "
            "rather than a full page of matches"
        )
        assert len(page_sizes) < 20, "pagination did not terminate"

    assert len(page_sizes) >= 3, "the walk must actually cross page boundaries"
    # Every page but the last is full. This is the assertion that fails the
    # moment the filter moves after paginate().
    assert page_sizes[:-1] == [page_size] * (len(page_sizes) - 1), (
        f"short page(s) in the middle of the walk: {page_sizes}"
    )
    assert len(page_sizes) == -(-len(wanted) // page_size), (
        f"expected ceil({len(wanted)}/{page_size}) pages, got {page_sizes}"
    )

    assert len(seen) == len(set(seen)), f"a row was served twice: {seen}"
    assert set(seen) == wanted
    # Sorted by name — the cursor's seek order must survive the filter.
    assert seen == sorted(seen)


def test_exact_match_paginates_too(authed_client):
    c = authed_client
    parent = _category(c, "Passives")
    child = _category(c, "Resistors", parent)
    for i in range(8):
        create_part(c, name=f"exact-{i:02d}", category_id=parent)
        create_part(c, name=f"nested-{i:02d}", category_id=child)

    seen: list[str] = []
    cursor = None
    while True:
        url = (
            f"/api/parts?paged=true&limit=3&category_id={parent}"
            "&include_descendants=false"
        )
        if cursor:
            url += f"&cursor={cursor}"
        page = c.get(url).json()["data"]
        seen.extend(p["name"] for p in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert set(seen) == {f"exact-{i:02d}" for i in range(8)}


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


def test_foreign_category_id_is_404_not_an_empty_list(authed_client, other_client):
    """An empty list would be an existence oracle by omission and a
    confusing dead-end in the UI. 404, same as every other foreign id."""
    foreign = _category(other_client, "Their category")
    r = authed_client.get(f"/api/parts?category_id={foreign}")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "category.not_found"


def test_unknown_category_id_is_404(authed_client):
    r = authed_client.get(f"/api/parts?category_id={uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["code"] == "category.not_found"


def test_descendant_expansion_cannot_reach_another_workspace(
    authed_client, other_client
):
    """The parent map is loaded workspace-scoped, so no descendant set can
    ever contain another workspace's category — and therefore no part
    filed under one can leak into the results."""
    mine = _category(authed_client, "Passives")
    create_part(authed_client, name="mine", category_id=mine)

    theirs = _category(other_client, "Passives")
    create_part(other_client, name="theirs", category_id=theirs)

    assert _names(authed_client, f"category_id={mine}") == {"mine"}
    assert _names(other_client, f"category_id={theirs}") == {"theirs"}


def test_archived_category_still_filters(authed_client):
    """Archiving hides a category from pickers; it does not unfile the
    parts already pointing at it, so the filter must keep working (the
    parts list column renders archived category names for the same
    reason)."""
    c = authed_client
    cid = _category(c, "Passives")
    create_part(c, name="filed", category_id=cid)
    assert c.post(f"/api/categories/{cid}/archive").status_code == 200

    assert _names(c, f"category_id={cid}") == {"filed"}
