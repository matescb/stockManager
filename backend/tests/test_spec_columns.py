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


def test_schema_for_a_bare_root_capacitors_is_the_class_union(authed_client):
    """A bare "Capacitors" offers every dielectric's keys, not just the common ones.

    `CLASS_DEFAULT_SLUG["capacitor"]` is still None — there is no single
    honest schema for a capacitor whose dielectric nobody named. But the
    parts filed under the root DO carry `capacitance`, `dielectric` and
    `esr`, written by whichever slug their own provider import resolved,
    and a columns menu that offers none of them is the bug this fixes.
    So the root answers with the UNION of its class's slugs.
    """
    root = _category(authed_client, "Capacitors")
    schema = _schema(authed_client, root["id"])

    assert schema["slug"] is None
    assert schema["class"] == "capacitor"
    by_key = {row["key"]: row for row in schema["keys"]}
    assert {"capacitance", "voltage_rating", "dielectric", "esr"} <= set(by_key)

    # Each key says which of the class's slugs define it, so the picker can
    # badge "ESR" as an electrolytic/tantalum key.
    assert by_key["capacitance"]["slugs"] == [
        "capacitor_ceramic",
        "capacitor_electrolytic",
        "capacitor_tantalum",
        "capacitor_film",
    ]
    assert by_key["esr"]["slugs"] == ["capacitor_electrolytic", "capacitor_tantalum"]
    assert by_key["dielectric"]["slugs"] == ["capacitor_ceramic", "capacitor_film"]
    # A common key is still common, and still flagged as such.
    assert by_key["package"]["common"] is True


def test_the_union_keeps_mandatory_only_when_every_slug_agrees(authed_client):
    """"Required" on the root has to mean required for every capacitor.

    `esr` is mandatory for an electrolytic and optional for a tantalum, so
    it cannot be mandatory for a category that holds both — the
    completeness badge would fail every ceramic in the branch.
    """
    root = _category(authed_client, "Capacitors")
    by_key = {row["key"]: row for row in _schema(authed_client, root["id"])["keys"]}

    assert by_key["capacitance"]["mandatory"] is True
    assert by_key["voltage_rating"]["mandatory"] is True
    assert by_key["esr"]["mandatory"] is False
    assert by_key["dielectric"]["mandatory"] is False
    # Not in the electrolytic slug at all, so not mandatory for the class.
    assert by_key["tolerance"]["mandatory"] is False


def test_the_union_orders_common_then_shared_then_the_rest(authed_client):
    """Stable order: common keys, keys every slug has, then first-seen."""
    root = _category(authed_client, "Capacitors")
    keys = [row["key"] for row in _schema(authed_client, root["id"])["keys"]]

    common = [row["key"] for row in _schema(
        authed_client, _category(authed_client, "Miscellaneous widgets")["id"]
    )["keys"]]
    assert keys[: len(common)] == common
    # `capacitance` and `voltage_rating` are on all four dielectrics;
    # `tolerance` is not (an electrolytic has none), so it falls back to
    # schema order with the rest.
    assert keys[len(common):] == [
        "capacitance",
        "voltage_rating",
        "dielectric",
        "tolerance",
        "esr",
        "ripple_current",
        "lifetime",
    ]


def test_schema_for_a_bare_root_transistors_unions_bjt_and_mosfet(authed_client):
    root = _category(authed_client, "Transistors")
    schema = _schema(authed_client, root["id"])

    assert schema["slug"] is None
    assert schema["class"] == "transistor"
    by_key = {row["key"]: row for row in schema["keys"]}
    assert {"transistor_type", "vceo", "fet_type", "vds", "rds_on"} <= set(by_key)
    assert by_key["vceo"]["slugs"] == ["transistor_bjt"]
    assert by_key["vds"]["slugs"] == ["transistor_mosfet"]
    # The two share no parametric key, so nothing is mandatory class-wide.
    assert by_key["vceo"]["mandatory"] is False


def test_a_root_whose_class_already_has_a_slug_is_unchanged(authed_client):
    """"Resistors" resolves to `resistor`; there is nothing to union."""
    schema = _schema(authed_client, _category(authed_client, "Resistors")["id"])

    assert schema["slug"] == "resistor"
    assert schema["class"] == "resistor"
    by_key = {row["key"]: row for row in schema["keys"]}
    assert {"resistance", "tolerance", "power"} <= set(by_key)
    assert by_key["resistance"]["slugs"] == ["resistor"]
    assert by_key["resistance"]["mandatory"] is True


def test_a_root_with_no_class_at_all_is_common_only(authed_client):
    """No component noun in the name, no class, no union — common keys."""
    schema = _schema(authed_client, _category(authed_client, "Miscellaneous widgets")["id"])

    assert schema["slug"] is None
    assert schema["class"] is None
    assert all(row["common"] for row in schema["keys"])
    assert all(row["slugs"] == [] for row in schema["keys"])


def test_a_child_under_capacitors_keeps_its_own_slug(authed_client):
    """The union is the ROOT's answer. A named dielectric still narrows."""
    root = _category(authed_client, "Capacitors")
    leaf = _category(authed_client, "Ceramic", parent_id=root["id"])

    schema = _schema(authed_client, leaf["id"])
    assert schema["slug"] == "capacitor_ceramic"
    assert schema["class"] == "capacitor"
    keys = {row["key"] for row in schema["keys"]}
    assert "dielectric" in keys
    # The electrolytic-only keys are NOT offered on a ceramic.
    assert {"esr", "ripple_current", "lifetime"} & keys == set()


def test_patch_accepts_a_union_key_on_the_root(authed_client):
    """The PATCH vocabulary is the same union the schema payload offers."""
    root = _category(authed_client, "Capacitors")
    r = authed_client.patch(
        f"/api/categories/{root['id']}",
        json={"list_columns": ["capacitance", "dielectric", "esr"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["list_columns"] == ["capacitance", "dielectric", "esr"]

    # And a key no capacitor slug defines is still a 422.
    bad = authed_client.patch(
        f"/api/categories/{root['id']}", json={"list_columns": ["resistance"]}
    )
    assert bad.status_code == 422
    assert bad.json()["key"] == "resistance"


def test_root_sort_by_a_union_key_is_numeric_across_dielectrics(authed_client, db):
    """Sorting the root by `capacitance` orders ceramics and electrolytics together.

    The JOIN matches on the KEY, not on the slug, so this already worked —
    what did not was being allowed to ask for it. Pinned so a future
    narrowing of the union cannot silently take the sort away with it.
    """
    ws_id = _ws(authed_client)
    root = _category(authed_client, "Capacitors")
    ceramic = _category(authed_client, "Ceramic", parent_id=root["id"])
    electrolytic = _category(authed_client, "Electrolytic", parent_id=root["id"])

    big = create_part(authed_client, name="C 100 uF", category_id=electrolytic["id"])
    small = create_part(authed_client, name="C 10 nF", category_id=ceramic["id"])
    mid = create_part(authed_client, name="C 1 uF", category_id=ceramic["id"])
    # No `capacitance` row at all — a part the union offers the column for
    # and that has no value under it.
    create_part(authed_client, name="C unknown", category_id=ceramic["id"])
    _spec(db, ws_id=ws_id, part_id=big, key="capacitance",
          value="100 µF", value_num=Decimal("0.0001"))
    _spec(db, ws_id=ws_id, part_id=small, key="capacitance",
          value="10 nF", value_num=Decimal("0.00000001"))
    _spec(db, ws_id=ws_id, part_id=mid, key="capacitance",
          value="1 µF", value_num=Decimal("0.000001"))
    db.commit()

    query = (
        f"category_id={root['id']}&paged=true&spec_columns=capacitance"
        "&sort=spec:capacitance"
    )
    assert _names(_list(authed_client, f"{query}&dir=asc")) == [
        "C 10 nF", "C 1 uF", "C 100 uF", "C unknown",
    ]
    # NULLS LAST both ways: a part with no value belongs at the end of the
    # list whichever direction it is read in, not interleaved at one end.
    assert _names(_list(authed_client, f"{query}&dir=desc")) == [
        "C 100 uF", "C 1 uF", "C 10 nF", "C unknown",
    ]


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
    """400, not a silent restart at page one, and not a wrong walk.

    Four ways a client can hand back a cursor that does not describe the
    ordering it is now asking for. The two SHAPE mismatches (sorted cursor
    on an unsorted request and vice versa) are visible in the payload; the
    two IDENTITY mismatches are not, and are the dangerous pair — the seek
    values are structurally valid under any spec key, and `(10000, "10
    kΩ")` read as a descending seek is a legal descending seek. Before the
    cursor carried its sort's identity, flipping `dir` mid-walk served one
    row of six and then reported end-of-list.

    Restarting at page one instead would be no better: the client appends
    what it gets to what it has, so it would show page one twice and never
    reach the end.
    """
    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])
    create_part(authed_client, name="R extra", category_id=category["id"])
    base = f"/api/parts?category_id={category['id']}&paged=true&limit=2"

    def cursor_for(query: str) -> str:
        page = authed_client.get(base + query).json()["data"]
        assert page["next_cursor"], f"no next page for {query!r}"
        return page["next_cursor"]

    unsorted = cursor_for("")
    ascending = cursor_for("&sort=spec:resistance")

    for label, query, taken in (
        # Shape: a `(name, id)` seek handed to the spec paginator.
        ("unsorted cursor, sorted request", "&sort=spec:resistance", unsorted),
        # Shape: a `(value_num, value, id)` seek handed to the name sort.
        ("sorted cursor, unsorted request", "", ascending),
        # Identity: same shape, different key. `10000` is a legal
        # `tolerance` too, so nothing about the payload says no.
        ("same shape, different key", "&sort=spec:tolerance", ascending),
        # Identity: same shape, same key, reversed. This is the one that
        # silently truncated the walk.
        ("same key, flipped direction", "&sort=spec:resistance&dir=desc", ascending),
    ):
        r = authed_client.get(f"{base}{query}&cursor={taken}")
        assert r.status_code == 400, f"{label}: {r.status_code} {r.text}"

    # And the cursor that DOES match still works.
    r = authed_client.get(f"{base}&sort=spec:resistance&cursor={ascending}")
    assert r.status_code == 200, r.text


def test_the_category_filter_is_part_of_a_sorted_cursors_identity(authed_client, db):
    """A narrowed filter would make a forward seek skip rows silently.

    Same trap `category_filter_ids` documents for the unsorted path, and
    the reason the scope carries the category id and the descendants flag
    rather than only the sort.
    """
    parent = _category(authed_client, "Resistors")
    child = _category(authed_client, "Thin film", parent_id=parent["id"])
    _resistor_ladder(authed_client, db, child["id"])
    create_part(authed_client, name="R loose", category_id=parent["id"])

    page = authed_client.get(
        f"/api/parts?category_id={parent['id']}&paged=true&limit=2"
        "&sort=spec:resistance"
    ).json()["data"]
    assert page["next_cursor"]

    # Same sort, narrower filter.
    r = authed_client.get(
        f"/api/parts?category_id={child['id']}&paged=true&limit=2"
        f"&sort=spec:resistance&cursor={page['next_cursor']}"
    )
    assert r.status_code == 400, r.text
    # Same sort, same category, descendants switched off.
    r = authed_client.get(
        f"/api/parts?category_id={parent['id']}&paged=true&limit=2"
        f"&include_descendants=false&sort=spec:resistance"
        f"&cursor={page['next_cursor']}"
    )
    assert r.status_code == 400, r.text


def test_a_descending_walk_reaches_every_row(authed_client, db):
    """The regression the cursor's sort identity was added for.

    Paging a `dir=desc` listing from its own cursors must return all six
    rows in reverse order. Before the fix this test would still pass — the
    bug was a cursor from the *ascending* walk being honoured here — so it
    is the companion to `test_a_cursor_from_a_different_sort_is_refused`,
    not a replacement: this one pins that the descending walk itself is
    complete.
    """
    category = _category(authed_client, "Resistors")
    ws_id = _ws(authed_client)
    for index in range(6):
        _spec(
            db, ws_id=ws_id,
            part_id=create_part(
                authed_client, name=f"R {index}", category_id=category["id"]
            ),
            key="resistance", value=f"{index} Ω", value_num=Decimal(index),
        )

    seen: list[str] = []
    cursor: str | None = None
    url = (
        f"/api/parts?category_id={category['id']}&paged=true&limit=2"
        "&sort=spec:resistance&dir=desc"
    )
    for _ in range(10):
        body = authed_client.get(
            url + (f"&cursor={cursor}" if cursor else "")
        ).json()["data"]
        seen.extend(row["name"] for row in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"R {index}" for index in reversed(range(6))], seen


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


def test_the_spec_sort_plan_is_a_scan_and_a_sort(authed_client, db):
    """The ordering is NOT delivered by an index, and cannot be.

    `ix_custom_fields_ws_key_value_num` exists and this query does not use
    it. Measured on a populated database, at a typical workspace size and
    at fifty times one, the plan is the same both times:

        Limit -> Sort (top-N heapsort)
                   -> Hash Right Join
                        -> Seq Scan on custom_fields
                        -> Seq Scan on parts

    | workspace | exec |
    |---|---|
    | 400 parts, 400 spec rows | 0.35 ms |
    | 20,400 parts, 20,400 spec rows | 18.5 ms |

    The reason is the OUTER join, not the index's shape: a part with no
    `custom_fields` row for the key has no row to index and still has to
    sort into the NULLS-LAST tail, so the full ordering only exists after
    the join — which is where the sort node is. An index could at best
    order the non-NULL prefix, and this one cannot even serve the lookup
    without a heap fetch per row, carrying neither `object_id` nor
    `archived_at`.

    So this asserts the honest thing — a `Sort` node is in the plan — and
    records the numbers. It is a **deliberate** cost at our scale, not an
    oversight. If someone later adds a composite `(workspace_id, key,
    value_num, object_id) WHERE archived_at IS NULL` and reshapes the
    query so the sort disappears, this test fails, and the three places
    that describe the cost (`spec_columns.sorted_page`, `docs/api/
    parts.md`, ADR-0034) have to move with it. That is the point.
    """
    from sqlalchemy import text

    category = _category(authed_client, "Resistors")
    _resistor_ladder(authed_client, db, category["id"])
    ws_id = _ws(authed_client)

    plan = "\n".join(
        row[0]
        for row in db.execute(
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
                ORDER BY cf.value_num ASC NULLS LAST,
                         cf.value ASC NULLS LAST,
                         parts.id ASC
                LIMIT 51
                """
            ),
            {"ws": ws_id},
        ).all()
    )
    assert "Sort" in plan, plan
    assert "ix_custom_fields_ws_key_value_num" not in plan, plan

    # The index is still the right one to reach for when the time comes —
    # assert it is the shape the docs say it is.
    definition = db.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": "ix_custom_fields_ws_key_value_num"},
    ).scalar_one()
    assert "value_num" in definition
    assert "value_num IS NOT NULL" in definition
    assert "object_id" not in definition, (
        "the index grew object_id — it can now serve the join, so the "
        "'cannot be index-ordered' claim in sorted_page() needs re-measuring"
    )


def test_filtering_by_category_costs_no_extra_statement(authed_client, db, engine):
    """Resolving the spec schema must not load the category tree again.

    A category-filtered listing needs this workspace's tree three times —
    to expand the filter to its descendants, to resolve the category's
    spec schema, and for the `missing_specs` badge — and an unfiltered one
    needs it once, for the badge. Loading it per question made the
    filtered path four statements where two used to do.

    Comparing filtered against unfiltered rather than asserting a constant
    keeps this from breaking on an unrelated extra lookup while still
    catching a re-load: every part here has a category, so the unfiltered
    page loads the tree too and the two counts are directly comparable.
    """
    category = _category(authed_client, "Resistors")
    for index in range(5):
        create_part(authed_client, name=f"R {index}", category_id=category["id"])

    def query_count(url: str, expect: int) -> int:
        count = 0

        def _on_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal count
            count += 1

        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            r = authed_client.get(url)
            assert r.status_code == 200, r.text
            rows = r.json()["data"]
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)
        assert len(rows) == expect, f"{url}: expected {expect} rows, got {len(rows)}"
        assert count > 0, "the counter saw nothing — it is on the wrong engine"
        return count

    unfiltered = query_count("/api/parts", 5)
    filtered = query_count(f"/api/parts?category_id={category['id']}", 5)
    # Measured: 16 and 16. Without the shared index the filtered path was
    # 19 — one extra `get_category` and two extra loads of the same tree.
    assert filtered <= unfiltered, (
        f"the category filter cost {filtered} statements vs {unfiltered} "
        "unfiltered — the category tree is being loaded more than once"
    )
