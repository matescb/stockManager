"""`/api/categories` CRUD, uniqueness, audit trail, and `parts.category_id`.

Isolation coverage mirrors `test_bom_presets.py`: a second signup gets a
second workspace, and every cross-workspace reference must come back 404
rather than 403 (workspace-isolation invariant, ADR-0002).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.domain.categories.service import slugify
from app.main import app
from tests._factories import signup_user


@pytest.fixture
def other_client(db):
    """A second workspace, for the cross-workspace probes."""
    c = TestClient(app)
    signup_user(c)
    return c


def _create(client: TestClient, **body) -> dict:
    body.setdefault("name", "Resistors")
    r = client.post("/api/categories", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _audit_rows(db, action: str) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == action)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).scalars()
    )


# ---------------------------------------------------------------------
# CRUD walk
# ---------------------------------------------------------------------


def test_category_crud_walk(authed_client):
    c = authed_client
    assert c.get("/api/categories").json()["data"] == []

    created = _create(
        c,
        name="Resistors",
        description="Fixed-value resistors",
        sort_order=10,
        refdes_prefix="R",
        default_symbol_ref="Device:R",
        default_footprint_ref="Resistor_SMD:R_0402_1005Metric",
        footprint_filters=["R_*", "*_0402_*"],
    )
    assert created["library_slug"] == "resistors"
    assert created["refdes_prefix"] == "R"
    assert created["footprint_filters"] == ["R_*", "*_0402_*"]
    assert created["archived_at"] is None

    rows = c.get("/api/categories").json()["data"]
    assert [r["id"] for r in rows] == [created["id"]]

    r = c.patch(
        f"/api/categories/{created['id']}",
        json={"description": "SMD only", "sort_order": 20},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["description"] == "SMD only"
    assert r.json()["data"]["sort_order"] == 20
    # Untouched fields survive the patch.
    assert r.json()["data"]["library_slug"] == "resistors"

    r = c.post(f"/api/categories/{created['id']}/archive")
    assert r.status_code == 200, r.text
    assert c.get("/api/categories").json()["data"] == []

    archived = c.get("/api/categories?include_archived=true").json()["data"]
    assert [row["id"] for row in archived] == [created["id"]]
    assert archived[0]["archived_at"] is not None

    r = c.post(f"/api/categories/{created['id']}/restore")
    assert r.status_code == 200, r.text
    rows = c.get("/api/categories").json()["data"]
    assert [row["id"] for row in rows] == [created["id"]]
    assert rows[0]["archived_at"] is None


def test_list_orders_by_sort_order_then_name(authed_client):
    c = authed_client
    _create(c, name="Zeners", sort_order=1)
    _create(c, name="Amplifiers", sort_order=5)
    _create(c, name="Buffers", sort_order=5)

    names = [row["name"] for row in c.get("/api/categories").json()["data"]]
    assert names == ["Zeners", "Amplifiers", "Buffers"]


# ---------------------------------------------------------------------
# Slug derivation + uniqueness
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Resistors", "resistors"),
        ("  Power   MOSFETs  ", "power-mosfets"),
        ("RF / Microwave", "rf-microwave"),
        ("Caps---&---Inductors", "caps-inductors"),
        ("///", "category"),
        ("Ω", "category"),
        ("A" * 80, "a" * 60),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slug_derived_from_name_and_explicit_slug_wins(authed_client):
    c = authed_client
    assert _create(c, name="Power MOSFETs")["library_slug"] == "power-mosfets"
    assert (
        _create(c, name="Bipolar Junction Transistors", library_slug="bjt")["library_slug"]
        == "bjt"
    )


def test_invalid_explicit_slug_is_422(authed_client):
    r = authed_client.post(
        "/api/categories", json={"name": "Diodes", "library_slug": "Not A Slug"}
    )
    assert r.status_code == 422, r.text


def test_duplicate_name_conflicts_with_existing_id(authed_client):
    c = authed_client
    first = _create(c, name="Capacitors")

    r = c.post("/api/categories", json={"name": "Capacitors"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["existing_id"] == first["id"]
    assert body["existing_name"] == "Capacitors"
    assert body["code"] == "category.name_conflict"
    assert body["status"]["category"] == "conflict"


def test_duplicate_slug_conflicts_even_when_name_differs(authed_client):
    c = authed_client
    first = _create(c, name="Resistors")

    # Case-insensitive name uniqueness is NOT enforced (matching tags), so
    # this collides on the derived slug instead.
    r = c.post("/api/categories", json={"name": "resistors"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "category.slug_conflict"
    assert r.json()["existing_id"] == first["id"]


def test_archived_name_and_slug_are_free_for_reuse(authed_client):
    c = authed_client
    first = _create(c, name="Inductors")
    assert c.post(f"/api/categories/{first['id']}/archive").status_code == 200

    second = _create(c, name="Inductors")
    assert second["id"] != first["id"]
    assert second["library_slug"] == "inductors"


def test_restore_conflicts_when_name_was_taken(authed_client):
    c = authed_client
    first = _create(c, name="Connectors")
    assert c.post(f"/api/categories/{first['id']}/archive").status_code == 200
    second = _create(c, name="Connectors")

    r = c.post(f"/api/categories/{first['id']}/restore")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "category.name_conflict"
    assert r.json()["existing_id"] == second["id"]


def test_patch_onto_another_active_name_conflicts(authed_client):
    c = authed_client
    first = _create(c, name="Crystals")
    second = _create(c, name="Oscillators")

    r = c.patch(f"/api/categories/{second['id']}", json={"name": "Crystals"})
    assert r.status_code == 409, r.text
    assert r.json()["existing_id"] == first["id"]


def test_patch_to_own_name_is_a_noop_not_a_conflict(authed_client):
    c = authed_client
    created = _create(c, name="Relays")
    r = authed_client.patch(f"/api/categories/{created['id']}", json={"name": "Relays"})
    assert r.status_code == 200, r.text


def test_rename_does_not_move_library_slug(authed_client):
    c = authed_client
    created = _create(c, name="Fuses")
    r = c.patch(f"/api/categories/{created['id']}", json={"name": "Circuit Protection"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["library_slug"] == "fuses"


def test_patch_rejects_unknown_field(authed_client):
    created = _create(authed_client)
    r = authed_client.patch(f"/api/categories/{created['id']}", json={"banana": "yellow"})
    assert r.status_code == 422, r.text
    assert "banana" in r.text


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


def test_categories_isolated_per_workspace(authed_client, other_client):
    mine = _create(authed_client, name="Sensors")

    assert other_client.get("/api/categories").json()["data"] == []
    for path, method in (
        (f"/api/categories/{mine['id']}", "patch"),
        (f"/api/categories/{mine['id']}/archive", "post"),
        (f"/api/categories/{mine['id']}/restore", "post"),
    ):
        kwargs = {"json": {"name": "Hijacked"}} if method == "patch" else {}
        r = getattr(other_client, method)(path, **kwargs)
        assert r.status_code == 404, f"{method} {path}: {r.text}"
        assert r.json()["code"] == "category.not_found"


def test_conflict_precheck_is_workspace_scoped(authed_client, other_client):
    """The 409 pre-check must only ever look inside the caller's workspace.

    This is the one line in the feature that fails open: drop the
    `workspace_id` filter from `service._active_by` and every other test
    still passes, because the isolation test above 404s at `get_category`
    before any conflict probe runs. Create would silently become a
    cross-workspace name oracle — a 409 carrying another tenant's
    `existing_id` and `existing_name`.
    """
    mine = _create(authed_client, name="Sensors")

    # The same name in a second workspace is not a collision at all.
    theirs = _create(other_client, name="Sensors")
    assert theirs["id"] != mine["id"]
    assert theirs["library_slug"] == "sensors"

    # And a patch that does collide names the *local* row, never mine.
    second = _create(other_client, name="Relays")
    r = other_client.patch(f"/api/categories/{second['id']}", json={"name": "Sensors"})
    assert r.status_code == 409, r.text
    assert r.json()["existing_id"] == theirs["id"]
    assert r.json()["existing_id"] != mine["id"]


def test_restore_precheck_is_workspace_scoped(authed_client, other_client):
    """Same guard on the restore path: a name held by another workspace
    must not block un-archiving ours."""
    mine = _create(authed_client, name="Actuators")
    assert authed_client.post(f"/api/categories/{mine['id']}/archive").status_code == 200
    _create(other_client, name="Actuators")

    r = authed_client.post(f"/api/categories/{mine['id']}/restore")
    assert r.status_code == 200, r.text


def test_unknown_category_id_is_404(authed_client):
    r = authed_client.post(f"/api/categories/{uuid.uuid4()}/archive")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "category.not_found"


# ---------------------------------------------------------------------
# parts.category_id
# ---------------------------------------------------------------------


def test_part_create_and_patch_with_category(authed_client):
    c = authed_client
    category = _create(c, name="Microcontrollers")

    r = c.post("/api/parts", json={"name": "STM32F103", "category_id": category["id"]})
    assert r.status_code == 201, r.text
    part = r.json()["data"]
    assert part["category_id"] == category["id"]

    # Round-trips through GET.
    assert c.get(f"/api/parts/{part['id']}").json()["data"]["category_id"] == category["id"]

    # Explicit null clears it.
    r = c.patch(f"/api/parts/{part['id']}", json={"category_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["category_id"] is None

    # And a PATCH can set it back.
    r = c.patch(f"/api/parts/{part['id']}", json={"category_id": category["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["category_id"] == category["id"]


def test_part_created_without_category_has_null(authed_client):
    r = authed_client.post("/api/parts", json={"name": "Uncategorised"})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["category_id"] is None


def test_part_rejects_foreign_workspace_category(authed_client, other_client):
    foreign = _create(other_client, name="Their Category")

    r = authed_client.post("/api/parts", json={"name": "Probe", "category_id": foreign["id"]})
    assert r.status_code == 404, r.text

    mine = authed_client.post("/api/parts", json={"name": "Mine"}).json()["data"]
    r = authed_client.patch(f"/api/parts/{mine['id']}", json={"category_id": foreign["id"]})
    assert r.status_code == 404, r.text
    # The write was refused wholesale, not partially applied.
    assert authed_client.get(f"/api/parts/{mine['id']}").json()["data"]["category_id"] is None


def test_part_rejects_unknown_category(authed_client):
    r = authed_client.post(
        "/api/parts", json={"name": "Probe", "category_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------


def test_each_mutation_writes_one_audit_row(authed_client, db):
    c = authed_client
    created = _create(c, name="Transformers", refdes_prefix="T")

    rows = _audit_rows(db, "category.created")
    assert len(rows) == 1
    assert rows[0].target_type == "part_category"
    assert rows[0].target_ids == [uuid.UUID(created["id"])]
    assert rows[0].comment == "fields=name,refdes_prefix"
    assert rows[0].workspace_id is not None
    assert rows[0].user_id is not None

    c.patch(f"/api/categories/{created['id']}", json={"sort_order": 3, "description": "x"})
    rows = _audit_rows(db, "category.updated")
    assert len(rows) == 1
    assert rows[0].comment == "fields=description,sort_order"
    assert rows[0].target_ids == [uuid.UUID(created["id"])]

    c.post(f"/api/categories/{created['id']}/archive")
    rows = _audit_rows(db, "category.archived")
    assert len(rows) == 1
    assert rows[0].target_ids == [uuid.UUID(created["id"])]

    c.post(f"/api/categories/{created['id']}/restore")
    rows = _audit_rows(db, "category.restored")
    assert len(rows) == 1
    assert rows[0].target_ids == [uuid.UUID(created["id"])]


def test_failed_mutation_writes_no_audit_row(authed_client, db):
    c = authed_client
    _create(c, name="Switches")
    before = len(_audit_rows(db, "category.created"))

    assert c.post("/api/categories", json={"name": "Switches"}).status_code == 409
    assert len(_audit_rows(db, "category.created")) == before


# ---------------------------------------------------------------------
# Archived categories are not assignable — but only a CHANGE is rejected
# ---------------------------------------------------------------------


def test_part_rejects_archived_category(authed_client):
    c = authed_client
    category = _create(c, name="Obsolete")
    assert c.post(f"/api/categories/{category['id']}/archive").status_code == 200

    r = c.post("/api/parts", json={"name": "Probe", "category_id": category["id"]})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "category.archived"

    part = c.post("/api/parts", json={"name": "Later"}).json()["data"]
    r = c.patch(f"/api/parts/{part['id']}", json={"category_id": category["id"]})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "category.archived"


def test_part_keeps_since_archived_category_patchable(authed_client):
    """A part already pointing at a category archived AFTERWARDS must stay
    patchable — the settings form round-trips the current category_id with
    every save, so rejecting the unchanged value would brick the form."""
    c = authed_client
    category = _create(c, name="Sunsetting")
    part = c.post(
        "/api/parts", json={"name": "Holder", "category_id": category["id"]}
    ).json()["data"]
    assert c.post(f"/api/categories/{category['id']}/archive").status_code == 200

    r = c.patch(
        f"/api/parts/{part['id']}",
        json={"category_id": category["id"], "description": "still fine"},
    )
    assert r.status_code == 200, r.text
    assert (
        c.get(f"/api/parts/{part['id']}").json()["data"]["category_id"]
        == category["id"]
    )


def test_list_limit_caps_results(authed_client):
    c = authed_client
    for i in range(3):
        _create(c, name=f"Cat {i}")
    data = c.get("/api/categories", params={"limit": 2}).json()["data"]
    assert len(data) == 2


def test_sort_order_out_of_range_is_422(authed_client):
    r = authed_client.post(
        "/api/categories", json={"name": "Huge", "sort_order": 2**40}
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------
# DB trigger (migration 0067): raw SQL cannot smuggle a foreign category
# ---------------------------------------------------------------------


@pytest.mark.real_db
def test_parts_category_trigger_blocks_cross_workspace():
    """The application layer 404s a foreign category_id long before SQL;
    this probes the BEFORE trigger directly so raw SQL (migrations, admin
    queries) can't produce a cross-workspace row. SQLSTATE must be WS001.

    real_db: the UPDATE runs in a separate session, so the API-created
    rows must be genuinely committed to be visible to it (a savepoint-
    scoped row would make the UPDATE a silent 0-row no-op)."""
    from sqlalchemy import text as sa_text
    from sqlalchemy.exc import DBAPIError, IntegrityError

    from app.infra.db import SessionLocal

    a = TestClient(app)
    b = TestClient(app)
    signup_user(a)
    signup_user(b)
    foreign = _create(b, name="Their category")
    part = a.post("/api/parts", json={"name": "Trigger probe"}).json()["data"]

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
            s.execute(
                sa_text("UPDATE parts SET category_id = :cid WHERE id = :pid"),
                {"cid": foreign["id"], "pid": part["id"]},
            )
            s.commit()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "WS001"


def test_create_rejects_unknown_field(authed_client):
    """extra="forbid" on POST — the schemas.py docstring points here; the
    repo-wide test_extra_forbid.py is a hand-maintained list that does not
    cover this router."""
    r = authed_client.post(
        "/api/categories", json={"name": "Strict", "bogus_field": 1}
    )
    assert r.status_code == 422


def test_patch_null_on_non_nullable_field_is_422(authed_client):
    created = _create(authed_client, name="NotNullable")
    for field in ("name", "sort_order", "library_slug"):
        r = authed_client.patch(
            f"/api/categories/{created['id']}", json={field: None}
        )
        assert r.status_code == 422, (field, r.text)
        assert r.json()["code"] == "category.field_not_nullable"
