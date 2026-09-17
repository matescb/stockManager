"""Per-category spec columns on the parts list — alembic 0083 / ADR-0034.

Three surfaces, pinned here together because they share one vocabulary:

  - `GET /api/categories/{id}/spec-schema` — which canonical keys this
    category HAS (resolved up the tree), which it is configured to show,
    and where that configuration was inherited from.
  - `PATCH /api/categories/{id}` — persisting `list_columns` /
    `list_sort`, with the same key validation the listing applies.
  - `GET /api/parts?category_id=…&spec_columns=…&sort=spec:…` — the
    values, batched, and the JOINed keyset sort.

The two properties most likely to regress silently are both asserted by
counting statements rather than by reading a payload: the spec values must
not scale with row count, and `GET /api/parts` without `spec_columns` must
be unchanged.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.main import app
from tests._factories import create_part, signup_user


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------


@pytest.fixture
def other_client(db):
    """A second workspace, for the cross-workspace probes."""
    c = TestClient(app)
    signup_user(c)
    return c


def _ws(c: TestClient) -> uuid.UUID:
    return uuid.UUID(c.get("/api/workspaces/current").json()["data"]["id"])


def _category(c: TestClient, name: str, parent_id: str | None = None, **body) -> dict:
    body["name"] = name
    if parent_id is not None:
        body["parent_id"] = parent_id
    r = c.post("/api/categories", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _schema(c: TestClient, category_id: str) -> dict:
    r = c.get(f"/api/categories/{category_id}/spec-schema")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _spec(
    db,
    *,
    ws_id: uuid.UUID,
    part_id: str,
    key: str,
    value: str,
    value_num: Decimal | None = None,
    archived_at=None,
) -> CustomField:
    """Write one canonical spec row the way the reconciler would.

    Direct insert on purpose: `value_num` is filled by
    `services/spec_reconcile.py` off a provider payload, and standing up a
    provider round-trip to get one number is an integration test of
    something else.
    """
    row = CustomField(
        workspace_id=ws_id,
        object_type="part",
        object_id=uuid.UUID(part_id),
        key=key,
        value=value,
        value_num=value_num,
        source="manual",
        archived_at=archived_at,
    )
    db.add(row)
    db.flush()
    return row


def _names(payload) -> list[str]:
    rows = payload["data"]
    items = rows["items"] if isinstance(rows, dict) else rows
    return [row["name"] for row in items]


def _list(c: TestClient, query: str) -> dict:
    r = c.get(f"/api/parts?{query}")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------
# GET /api/categories/{id}/spec-schema
# ---------------------------------------------------------------------


def test_schema_resolves_the_leafs_own_path(authed_client):
    """"Capacitors / Ceramic" is a ceramic capacitor, dielectric and all."""
    parent = _category(authed_client, "Capacitors")
    leaf = _category(authed_client, "Ceramic", parent_id=parent["id"])

    schema = _schema(authed_client, leaf["id"])
    assert schema["slug"] == "capacitor_ceramic"
    keys = {row["key"] for row in schema["keys"]}
    assert {"capacitance", "voltage_rating", "dielectric", "tolerance"} <= keys
    # Common keys are merged in and flagged as such.
    assert {"package", "mounting"} <= keys
    by_key = {row["key"]: row for row in schema["keys"]}
    assert by_key["package"]["common"] is True
    assert by_key["capacitance"]["common"] is False


def test_schema_walks_ancestors_when_the_leaf_says_nothing(authed_client):
    """A leaf named for its grade still gets its parent's schema.

    `category_slug_for` is word-based over the whole flattened path, so
    the ancestor walk only shows its teeth when the leaf contributes a
    word that resolves to a DIFFERENT class than the parent — here
    "Ceramic" under "Resistors", which reads as a ceramic capacitor on the
    full path and as a resistor once the leaf is dropped.
    """
    parent = _category(authed_client, "Resistors")
    leaf = _category(authed_client, "Thin film", parent_id=parent["id"])
    assert _schema(authed_client, leaf["id"])["slug"] == "resistor"

    # Two levels up.
    deep = _category(authed_client, "0402", parent_id=leaf["id"])
    assert _schema(authed_client, deep["id"])["slug"] == "resistor"


def test_schema_for_a_bare_root_capacitors_is_common_only(authed_client):
    """`CLASS_DEFAULT_SLUG["capacitor"]` is None — no dielectric, no schema.

    Not a bug: a capacitor whose category does not say ceramic, film,
    tantalum or electrolytic has no canonical key set of its own, and
    inventing one would put `dielectric` on every electrolytic.
    """
    root = _category(authed_client, "Capacitors")
    schema = _schema(authed_client, root["id"])
    assert schema["slug"] is None
    assert {row["key"] for row in schema["keys"]} == {
        row["key"] for row in _schema(authed_client, _category(
            authed_client, "Miscellaneous widgets"
        )["id"])["keys"]
    }
    assert all(row["common"] for row in schema["keys"])


def test_schema_marks_unit_bearing_and_count_keys_numeric(authed_client):
    category = _category(authed_client, "Resistors")
    by_key = {row["key"]: row for row in _schema(authed_client, category["id"])["keys"]}

    assert by_key["resistance"]["unit"] == "Ω"
    assert by_key["resistance"]["numeric"] is True
    assert by_key["resistance"]["mandatory"] is True
    assert by_key["resistance"]["label"] == "Resistance"

    # No unit and not a count — a package code is text.
    assert by_key["package"]["unit"] is None
    assert by_key["package"]["numeric"] is False
    # A count: no unit, still numeric (right-aligns). See
    # `NUMERIC_UNITLESS_KEYS`.
    assert by_key["pin_count"]["unit"] is None
    assert by_key["pin_count"]["numeric"] is True


def test_schema_of_another_workspaces_category_is_404(authed_client, other_client):
    foreign = _category(other_client, "Resistors")
    r = authed_client.get(f"/api/categories/{foreign['id']}/spec-schema")
    assert r.status_code == 404
    assert r.json()["code"] == "category.not_found"


# ---------------------------------------------------------------------
# persistence + inheritance
# ---------------------------------------------------------------------


def test_list_columns_persist_and_audit(authed_client, db):
    category = _category(authed_client, "Resistors")
    r = authed_client.patch(
        f"/api/categories/{category['id']}",
        json={
            "list_columns": ["resistance", "tolerance", "power"],
            "list_sort": {"key": "resistance", "dir": "desc"},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["list_columns"] == ["resistance", "tolerance", "power"]
    assert r.json()["data"]["list_sort"] == {"key": "resistance", "dir": "desc"}

    schema = _schema(authed_client, category["id"])
    assert schema["list_columns"] == ["resistance", "tolerance", "power"]
    assert schema["list_sort"] == {"key": "resistance", "dir": "desc"}
    assert schema["inherited_from"] is None

    row = db.execute(
        select(AuditLog)
        .where(AuditLog.action == "category.updated")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).scalars().first()
    assert row is not None
    # Field names only — never values (CLAUDE.md audit invariant).
    assert row.comment == "fields=list_columns,list_sort"


def test_empty_list_columns_clears_and_null_restores_inheritance(authed_client):
    parent = _category(authed_client, "Resistors")
    child = _category(authed_client, "Thin film", parent_id=parent["id"])
    authed_client.patch(
        f"/api/categories/{parent['id']}", json={"list_columns": ["resistance"]}
    )

    # The child inherits, and says where from.
    schema = _schema(authed_client, child["id"])
    assert schema["list_columns"] == ["resistance"]
    assert schema["inherited_from"] == parent["id"]

    # `[]` is an explicit "no spec columns here" and stops the walk.
    authed_client.patch(f"/api/categories/{child['id']}", json={"list_columns": []})
    schema = _schema(authed_client, child["id"])
    assert schema["list_columns"] == []
    assert schema["inherited_from"] is None

    # An explicit null hands the child back to its ancestors.
    authed_client.patch(f"/api/categories/{child['id']}", json={"list_columns": None})
    schema = _schema(authed_client, child["id"])
    assert schema["list_columns"] == ["resistance"]
    assert schema["inherited_from"] == parent["id"]


def test_columns_and_sort_inherit_independently(authed_client):
    """The same split `value_template` and `kicad_fields` have (0082)."""
    parent = _category(authed_client, "Resistors")
    child = _category(authed_client, "Thin film", parent_id=parent["id"])
    authed_client.patch(
        f"/api/categories/{parent['id']}",
        json={"list_sort": {"key": "power", "dir": "asc"}},
    )
    authed_client.patch(
        f"/api/categories/{child['id']}", json={"list_columns": ["resistance"]}
    )

    schema = _schema(authed_client, child["id"])
    assert schema["list_columns"] == ["resistance"]
    assert schema["inherited_from"] is None
    assert schema["list_sort"] == {"key": "power", "dir": "asc"}
    assert schema["sort_inherited_from"] == parent["id"]


def test_create_accepts_list_settings_validated_against_the_new_row(authed_client):
    parent = _category(authed_client, "Resistors")
    created = _category(
        authed_client, "Thin film", parent_id=parent["id"],
        list_columns=["resistance", "tolerance"],
    )
    assert created["list_columns"] == ["resistance", "tolerance"]

    # And refuses on create what PATCH would refuse — the create path
    # resolves the schema of the row it is about to write.
    r = authed_client.post(
        "/api/categories",
        json={"name": "Wirewound", "parent_id": parent["id"], "list_columns": ["capacitance"]},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "category.unknown_spec_key"


def test_duplicate_keys_are_deduped_not_doubled(authed_client):
    category = _category(authed_client, "Resistors")
    r = authed_client.patch(
        f"/api/categories/{category['id']}",
        json={"list_columns": ["resistance", "resistance", "power"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["list_columns"] == ["resistance", "power"]


# ---------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------


def test_unknown_key_is_422_naming_the_key(authed_client):
    category = _category(authed_client, "Resistors")
    r = authed_client.patch(
        f"/api/categories/{category['id']}",
        json={"list_columns": ["resistance", "capacitance"]},
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "category.unknown_spec_key"
    # The client sent two keys; the response has to say which one lost.
    assert body["key"] == "capacitance"
    assert "capacitance" in body["status"]["message"]


def test_unknown_sort_key_is_422(authed_client):
    category = _category(authed_client, "Resistors")
    r = authed_client.patch(
        f"/api/categories/{category['id']}",
        json={"list_sort": {"key": "capacitance", "dir": "asc"}},
    )
    assert r.status_code == 422, r.text
    assert r.json()["key"] == "capacitance"


def test_more_than_twelve_columns_is_422(authed_client):
    category = _category(authed_client, "Connectors")
    keys = [row["key"] for row in _schema(authed_client, category["id"])["keys"]]
    assert len(keys) > 12, "this category needs >12 keys for the cap to be reachable"
    r = authed_client.patch(
        f"/api/categories/{category['id']}", json={"list_columns": keys[:13]}
    )
    assert r.status_code == 422, r.text
    # Pydantic's own `max_length` fires first and is also a 422 — either
    # way the request is refused and the cap is named.
    assert "12" in r.text


def test_bad_sort_direction_is_refused(authed_client):
    category = _category(authed_client, "Resistors")
    r = authed_client.patch(
        f"/api/categories/{category['id']}",
        json={"list_sort": {"key": "resistance", "dir": "sideways"}},
    )
    assert r.status_code == 422, r.text


def test_list_refuses_a_non_spec_sort(authed_client):
    category = _category(authed_client, "Resistors")
    r = authed_client.get(f"/api/parts?category_id={category['id']}&sort=name")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "category.unknown_spec_key"


def test_list_refuses_an_unknown_spec_column(authed_client):
    category = _category(authed_client, "Resistors")
    r = authed_client.get(
        f"/api/parts?category_id={category['id']}&spec_columns=resistance,capacitance"
    )
    assert r.status_code == 422, r.text
    assert r.json()["key"] == "capacitance"


def test_spec_params_are_ignored_without_a_category(authed_client):
    """A view setting outliving the filter it belongs to is not an error."""
    create_part(authed_client, name="Loose part")
    r = authed_client.get("/api/parts?spec_columns=resistance&sort=spec:resistance")
    assert r.status_code == 200, r.text
    assert "specs" not in r.json()["data"][0]


# ---------------------------------------------------------------------
# GET /api/parts — values
# ---------------------------------------------------------------------


def test_requested_spec_columns_come_back_as_values(authed_client, db):
    ws_id = _ws(authed_client)
    category = _category(authed_client, "Resistors")
    part_id = create_part(authed_client, name="R 10k", category_id=category["id"])
    _spec(db, ws_id=ws_id, part_id=part_id, key="resistance",
          value="10 kΩ", value_num=Decimal("10000"))
    _spec(db, ws_id=ws_id, part_id=part_id, key="package", value="0603")

    row = _list(
        authed_client,
        f"category_id={category['id']}&spec_columns=resistance,package,power",
    )["data"][0]
    assert row["specs"]["resistance"] == {"value": "10 kΩ", "value_num": "10000"}
    assert row["specs"]["package"] == {"value": "0603", "value_num": None}
    # Every requested key is present even with no row, so a blank cell is
    # distinguishable from a column that was never asked for.
    assert row["specs"]["power"] == {"value": None, "value_num": None}
    assert set(row["specs"]) == {"resistance", "package", "power"}


def test_archived_spec_rows_are_not_values(authed_client, db):
    ws_id = _ws(authed_client)
    category = _category(authed_client, "Resistors")
    part_id = create_part(authed_client, name="R 10k", category_id=category["id"])
    _spec(db, ws_id=ws_id, part_id=part_id, key="resistance", value="10 kΩ",
          value_num=Decimal("10000"), archived_at="2026-01-01T00:00:00+00:00")

    row = _list(
        authed_client, f"category_id={category['id']}&spec_columns=resistance"
    )["data"][0]
    assert row["specs"]["resistance"] == {"value": None, "value_num": None}


def test_another_workspaces_specs_never_appear(authed_client, other_client, db):
    """Same key, same category name, two workspaces, no leak."""
    mine = _category(authed_client, "Resistors")
    theirs = _category(other_client, "Resistors")
    my_part_id = create_part(authed_client, name="Shared name", category_id=mine["id"])
    their_part_id = create_part(other_client, name="Shared name", category_id=theirs["id"])

    _spec(db, ws_id=_ws(other_client), part_id=their_part_id,
          key="resistance", value="THEIRS", value_num=Decimal("1"))

    row = _list(
        authed_client, f"category_id={mine['id']}&spec_columns=resistance"
    )["data"][0]
    assert row["id"] == my_part_id
    assert row["specs"]["resistance"]["value"] is None


def test_spec_values_do_not_scale_with_row_count(authed_client, db, engine):
    """ONE statement for the page, not one per row.

    Same shape as
    `test_parts_list_columns.py::test_provider_links_do_not_scale_with_row_count`:
    compare a small page's statement count against a large one. Twelve
    columns over a 200-row page is 2,400 values, so a per-cell fetch is the
    difference between one round-trip and 2,400 — and a per-ROW fetch, the
    plausible regression, still shows up here as +18.
    """
    ws_id = _ws(authed_client)
    category = _category(authed_client, "Resistors")

    def add_parts(prefix: str, count: int) -> None:
        for index in range(count):
            part_id = create_part(
                authed_client, name=f"{prefix}-{index:03d}", category_id=category["id"]
            )
            _spec(db, ws_id=ws_id, part_id=part_id, key="resistance",
                  value=f"{index} kΩ", value_num=Decimal(index) * 1000)
            _spec(db, ws_id=ws_id, part_id=part_id, key="package", value="0603")
        db.commit()

    url = (
        f"/api/parts?category_id={category['id']}"
        "&spec_columns=resistance,package&limit=200"
    )

    def query_count(expect: int) -> int:
        count = 0

        def _on_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal count
            count += 1

        # The conftest `engine`, NOT `infra.db.get_engine()` — the test
        # session is bound to a connection off the former, and a listener on
        # the latter counts nothing, which would make the comparison below
        # pass for the wrong reason.
        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            r = authed_client.get(url)
            assert r.status_code == 200, r.text
            rows = r.json()["data"]
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)
        assert len(rows) == expect, f"expected {expect} rows, got {len(rows)}"
        assert count > 0, "the counter saw nothing — it is on the wrong engine"
        # Every row really did get its values, or a zero-query "batch"
        # would pass this test.
        assert all(row["specs"]["package"]["value"] == "0603" for row in rows)
        return count

    add_parts("small", 2)
    small = query_count(2)
    add_parts("large", 18)
    large = query_count(20)

    assert large <= small + 1, (
        f"listing issued {large} queries for 20 parts vs {small} for 2 — "
        "spec-column loading has regressed into an N+1"
    )


def test_no_spec_columns_means_no_specs_key(authed_client):
    """`GET /api/parts` is unchanged when nothing asks for specs.

    `tests/test_parts_list_columns.py` and
    `tests/test_parts_category_filter.py` pass unchanged for the same
    reason; this asserts it directly on the category-filtered path, which
    is the one that now resolves a schema.
    """
    category = _category(authed_client, "Resistors")
    create_part(authed_client, name="R 10k", category_id=category["id"])
    row = _list(authed_client, f"category_id={category['id']}")["data"][0]
    assert "specs" not in row


# ---------------------------------------------------------------------
# GET /api/parts — sorting
# ---------------------------------------------------------------------


def _resistor_ladder(c: TestClient, db, category_id: str) -> dict[str, str]:
    """Three resistors whose display strings sort the wrong way round.

    `"10 kΩ"` < `"100 Ω"` < `"1 kΩ"` as text; 100 < 1000 < 10000 as
    numbers. Any test that passes on the text order is not testing the
    numeric sort.
    """
    ws_id = _ws(c)
    ladder = {"100 Ω": Decimal("100"), "1 kΩ": Decimal("1000"), "10 kΩ": Decimal("10000")}
    names = {}
    for display, number in ladder.items():
        name = f"R {display}"
        _spec(
            db, ws_id=ws_id,
            part_id=create_part(c, name=name, category_id=category_id),
            key="resistance", value=display, value_num=number,
        )
        names[display] = name
    return names


def test_numeric_sort_uses_value_num_not_the_display_string(authed_client, db):
    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])

    asc = _names(_list(
        authed_client,
        f"category_id={category['id']}&spec_columns=resistance&sort=spec:resistance",
    ))
    assert asc == ["R 100 Ω", "R 1 kΩ", "R 10 kΩ"]

    desc = _names(_list(
        authed_client,
        f"category_id={category['id']}&sort=spec:resistance&dir=desc",
    ))
    assert desc == ["R 10 kΩ", "R 1 kΩ", "R 100 Ω"]


def test_text_key_sorts_alphabetically(authed_client, db):
    """A key with no unit has no `value_num`, so `value` carries the sort."""
    ws_id = _ws(authed_client)
    category = _category(authed_client, "Resistors")
    for package in ("1206", "0402", "0805"):
        _spec(
            db, ws_id=ws_id,
            part_id=create_part(
                authed_client, name=f"R {package}", category_id=category["id"]
            ),
            key="package", value=package,
        )

    assert _names(_list(
        authed_client, f"category_id={category['id']}&sort=spec:package"
    )) == ["R 0402", "R 0805", "R 1206"]


def test_parts_without_the_spec_sort_last_both_ways(authed_client, db):
    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])
    create_part(authed_client, name="R unknown", category_id=category["id"])

    asc = _names(_list(
        authed_client, f"category_id={category['id']}&sort=spec:resistance"
    ))
    desc = _names(_list(
        authed_client, f"category_id={category['id']}&sort=spec:resistance&dir=desc"
    ))
    assert asc[-1] == "R unknown"
    assert desc[-1] == "R unknown", "NULLS LAST, not 'NULLs at the low end'"


def test_a_sorted_listing_pages_without_gaps_or_repeats(authed_client, db):
    """The cursor has to carry `(value_num, value, id)`, not just `id`.

    Twelve parts, three of them with no `resistance` at all and three
    sharing one value, walked two rows at a time. Anything less than the
    full seek position either repeats a row at a page boundary or drops
    one — and a client appending pages would never notice which.
    """
    ws_id = _ws(authed_client)
    category = _category(authed_client, "Resistors")
    expected_order: list[str] = []
    for index in range(6):
        name = f"R uniq {index}"
        _spec(
            db, ws_id=ws_id,
            part_id=create_part(authed_client, name=name, category_id=category["id"]),
            key="resistance", value=f"{index} Ω", value_num=Decimal(index),
        )
        expected_order.append(name)
    # Three rows sharing a value — the `id` tiebreaker's whole job.
    tied = []
    for index in range(3):
        name = f"R tied {index}"
        _spec(
            db, ws_id=ws_id,
            part_id=create_part(authed_client, name=name, category_id=category["id"]),
            key="resistance", value="9 Ω", value_num=Decimal("9"),
        )
        tied.append(name)
    # Three with no row at all — the NULL group.
    nulls = []
    for index in range(3):
        name = f"R null {index}"
        create_part(authed_client, name=name, category_id=category["id"])
        nulls.append(name)

    seen: list[str] = []
    url = (
        f"/api/parts?category_id={category['id']}&paged=true&limit=2"
        f"&sort=spec:resistance&spec_columns=resistance"
    )
    cursor: str | None = None
    for _ in range(20):
        page = authed_client.get(url + (f"&cursor={cursor}" if cursor else ""))
        assert page.status_code == 200, page.text
        body = page.json()["data"]
        seen.extend(row["name"] for row in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        # Every page but the last is full — a post-filter or a partial
        # seek position shows up as a short page here.
        assert len(body["items"]) == 2, body

    assert len(seen) == len(set(seen)) == 12, seen
    assert seen[:6] == expected_order
    assert set(seen[6:9]) == set(tied)
    assert set(seen[9:]) == set(nulls)


def test_a_cursor_from_a_different_sort_is_refused(authed_client, db):
    """400, not a silent restart at page one.

    A client that keeps its cursor across a sort change appends the rows it
    gets to what it already has, so restarting would show page one twice
    and never reach the end.
    """
    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])
    create_part(authed_client, name="R extra", category_id=category["id"])

    unsorted_page = authed_client.get(
        f"/api/parts?category_id={category['id']}&paged=true&limit=2"
    ).json()["data"]
    assert unsorted_page["next_cursor"]

    r = authed_client.get(
        f"/api/parts?category_id={category['id']}&paged=true&limit=2"
        f"&sort=spec:resistance&cursor={unsorted_page['next_cursor']}"
    )
    assert r.status_code == 400, r.text

    sorted_page = authed_client.get(
        f"/api/parts?category_id={category['id']}&paged=true&limit=2"
        f"&sort=spec:resistance"
    ).json()["data"]
    r = authed_client.get(
        f"/api/parts?category_id={category['id']}&paged=true&limit=2"
        f"&cursor={sorted_page['next_cursor']}"
    )
    assert r.status_code == 400, r.text


def test_a_tampered_sorted_cursor_is_400(authed_client, db):
    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])
    r = authed_client.get(
        f"/api/parts?category_id={category['id']}&sort=spec:resistance"
        "&paged=true&limit=1&cursor=not-a-signed-cursor"
    )
    assert r.status_code == 400, r.text


def test_the_categorys_default_sort_applies_when_none_is_requested(authed_client, db):
    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])
    authed_client.patch(
        f"/api/categories/{category['id']}",
        json={"list_sort": {"key": "resistance", "dir": "desc"}},
    )

    assert _names(_list(authed_client, f"category_id={category['id']}")) == [
        "R 10 kΩ", "R 1 kΩ", "R 100 Ω",
    ]
    # An explicit sort still wins over the saved default.
    assert _names(_list(
        authed_client, f"category_id={category['id']}&sort=spec:resistance&dir=asc"
    )) == ["R 100 Ω", "R 1 kΩ", "R 10 kΩ"]


def test_an_inherited_default_sort_applies_to_the_child(authed_client, db):
    parent = _category(authed_client, "Resistors")
    child = _category(authed_client, "Thin film", parent_id=parent["id"])
    _resistor_ladder(authed_client, db, child["id"])
    authed_client.patch(
        f"/api/categories/{parent['id']}",
        json={"list_sort": {"key": "resistance", "dir": "desc"}},
    )

    assert _names(_list(authed_client, f"category_id={child['id']}")) == [
        "R 10 kΩ", "R 1 kΩ", "R 100 Ω",
    ]


def test_a_default_sort_that_outlived_its_schema_is_dropped(authed_client, db):
    """Renaming a category away from its schema must not 422 the listing.

    The user did not ask for the sort on this request; making the whole
    category unreadable because a saved default no longer resolves is the
    wrong trade.
    """
    category = _category(authed_client, "Capacitors ceramic")
    authed_client.patch(
        f"/api/categories/{category['id']}",
        json={"list_sort": {"key": "dielectric", "dir": "asc"}},
    )
    create_part(authed_client, name="C 100n", category_id=category["id"])
    r = authed_client.patch(
        f"/api/categories/{category['id']}", json={"name": "Odds and ends"}
    )
    assert r.status_code == 200, r.text

    r = authed_client.get(f"/api/parts?category_id={category['id']}")
    assert r.status_code == 200, r.text
    assert _names(r.json()) == ["C 100n"]


def test_a_spec_sort_covers_descendants(authed_client, db):
    """Spec columns set on a parent apply when the parent is selected."""
    parent = _category(authed_client, "Resistors")
    child = _category(authed_client, "Thin film", parent_id=parent["id"])
    _resistor_ladder(authed_client, db, child["id"])

    assert _names(_list(
        authed_client, f"category_id={parent['id']}&sort=spec:resistance&dir=desc"
    )) == ["R 10 kΩ", "R 1 kΩ", "R 100 Ω"]


def test_spec_sort_can_use_the_value_num_index(authed_client, db):
    """`ix_custom_fields_ws_key_value_num` is the index the sort wants.

    Asserting a chosen plan is not possible here — a three-row
    `custom_fields` fits in one page and the planner is right to seq-scan
    it, and forcing `enable_seqscan=off` would pin the planner's behaviour
    rather than the query's shape. So this runs the real EXPLAIN and
    asserts the plan is buildable and joins the right relation; the
    measured shape on a populated table is recorded below.

    On prod-sized data (9,377 `custom_fields` rows) the ascending
    unit-bearing case plans as:

        Nested Loop Left Join
          ->  Index Scan using ix_custom_fields_ws_key_value_num
                Index Cond: ((workspace_id = $1) AND (key = 'resistance'))
          ->  Index Scan using parts_pkey on parts

    The index is `(workspace_id, key, value_num) WHERE value_num IS NOT
    NULL`, i.e. already in `(key, value_num)` order for one workspace, so
    the ORDER BY's leading term needs no sort node. The partial predicate
    is why `value` is the second ORDER BY term rather than the first: rows
    the parser could not read are outside the index and land in the
    NULLS-LAST tail either way.
    """
    from sqlalchemy import text

    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])
    ws_id = _ws(authed_client)

    plan = db.execute(
        text(
            """
            EXPLAIN SELECT parts.id, cf.value_num, cf.value
            FROM parts
            LEFT OUTER JOIN custom_fields AS cf
              ON cf.workspace_id = :ws
             AND cf.object_type = 'part'
             AND cf.object_id = parts.id
             AND cf.key = 'resistance'
             AND cf.archived_at IS NULL
            WHERE parts.workspace_id = :ws AND parts.archived_at IS NULL
            ORDER BY cf.value_num ASC NULLS LAST, cf.value ASC NULLS LAST, parts.id ASC
            """
        ),
        {"ws": ws_id},
    ).all()
    rendered = "\n".join(row[0] for row in plan)
    assert "custom_fields" in rendered, rendered

    definition = db.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": "ix_custom_fields_ws_key_value_num"},
    ).scalar_one()
    assert "value_num" in definition
    assert "value_num IS NOT NULL" in definition
