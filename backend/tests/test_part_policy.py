"""Workspace part policy over REST: `GET`/`PUT /api/part-policy`,
`POST /api/parts/validate`, the create enforcement on `POST /api/parts`,
and `GET /api/parts/{id}/policy-status`.

What is pinned, and why:

* **an empty policy changes nothing.** Every existing workspace has a
  NULL policy; a create that worked before must work identically, and
  validate must say `ok` with nothing missing.
* **the 422 is the contract agents code against** — code
  `part.policy_violation` and the `missing` list at the TOP level of the
  body (the `HTTPException(detail=…)` spread), and nothing written.
* **validate and create agree.** A payload validate calls `ok` creates;
  one it refuses 422s with the same `create` items.
* **workspace isolation** — one workspace's policy never gates another's
  creates, a foreign category cannot be put in a rule's scope, and a
  foreign part's policy-status is a 404.
* **PUT is admin+**, GET is open to every member and to read-only tokens.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.parts.models import Part
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_mcp import _join_workspace, _mint

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _category(client: TestClient, name: str, **extra) -> dict:
    body = {"name": name, **extra}
    r = client.post("/api/categories", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _put_policy(client: TestClient, rules: list[dict]):
    return client.put("/api/part-policy", json={"rules": rules})


def _set_policy(client: TestClient, rules: list[dict]) -> list[dict]:
    r = _put_policy(client, rules)
    assert r.status_code == 200, r.text
    return r.json()["data"]["rules"]


# ---------------------------------------------------------------------------
# Default: empty policy, no behaviour change
# ---------------------------------------------------------------------------


def test_default_policy_is_empty_with_a_field_catalogue(authed_client):
    r = authed_client.get("/api/part-policy")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["rules"] == []
    fields = {f["field"]: f for f in data["fields"]}
    assert fields["manufacturer"]["create_allowed"] is True
    assert fields["eda.symbol"]["kind"] == "asset"
    assert fields["eda.symbol"]["create_allowed"] is False
    assert {"datasheet", "eda.footprint", "eda.spice", "eda.model3d"} <= set(fields)


def test_empty_policy_create_and_validate_behave_as_before(authed_client, db):
    r = authed_client.post("/api/parts", json={"mpn": "EMPTY-1"})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["name"] == "EMPTY-1"

    v = authed_client.post("/api/parts/validate", json={"mpn": "NEW-1"})
    assert v.status_code == 200, v.text
    assert v.json()["data"] == {"ok": True, "missing": [], "existing_part": None}


def test_validate_reports_the_builtin_name_or_mpn_rule(authed_client):
    v = authed_client.post("/api/parts/validate", json={"manufacturer": "TI"})
    data = v.json()["data"]
    assert data["ok"] is False
    assert data["missing"][0]["rule_id"] == "builtin:name_or_mpn"


def test_validate_names_an_existing_mpn(authed_client):
    part_id = create_part(authed_client, "Held", mpn="HELD-1")
    data = authed_client.post("/api/parts/validate", json={"mpn": " HELD-1 "}).json()["data"]
    assert data["existing_part"] == {"id": part_id, "name": "Held"}


# ---------------------------------------------------------------------------
# PUT validation + roles
# ---------------------------------------------------------------------------


def test_put_assigns_ids_defaults_and_is_audited(authed_client, db):
    from app.domain.audit.models import AuditLog

    rules = _set_policy(
        authed_client,
        [
            {"field": "manufacturer", "when": "create", "hint": "as on the datasheet"},
            {"id": "ds", "field": "datasheet"},
        ],
    )
    assert rules[0]["id"].startswith("r_")
    assert rules[0]["hint"] == "as on the datasheet"
    assert rules[1] == {
        "id": "ds",
        "field": "datasheet",
        "when": "complete",
        "scope": {"category_ids": [], "include_descendants": True, "part_types": []},
        "message": None,
        "hint": None,
    }
    assert authed_client.get("/api/part-policy").json()["data"]["rules"] == rules
    row = db.query(AuditLog).filter(AuditLog.action == "part_policy.updated").one()
    assert row.workspace_id is not None
    assert [str(t) for t in row.target_ids] == [str(row.workspace_id)]
    assert row.comment == "rules=2"

    # Replace, not merge.
    assert _set_policy(authed_client, []) == []


def test_put_refuses_an_asset_field_at_create(authed_client):
    r = _put_policy(authed_client, [{"field": "eda.symbol", "when": "create"}])
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "part_policy.invalid_rule"
    assert body["rule_index"] == 0


def test_put_refuses_duplicate_ids_and_unknown_fields(authed_client):
    r = _put_policy(
        authed_client, [{"id": "x", "field": "mpn"}, {"id": "x", "field": "description"}]
    )
    assert r.status_code == 422
    assert r.json()["code"] == "part_policy.invalid_rule"

    r = _put_policy(authed_client, [{"field": "colour"}])
    assert r.status_code == 422


def test_put_requires_admin(authed_client):
    member = _join_workspace(authed_client, "member")
    r = _put_policy(member, [{"field": "mpn", "when": "create"}])
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "resource.insufficient_role"
    # Members still read it.
    assert member.get("/api/part-policy").status_code == 200


def test_viewer_and_read_only_token_can_read_the_policy(authed_client):
    _set_policy(authed_client, [{"field": "mpn", "when": "create"}])
    viewer = _join_workspace(authed_client, "viewer")
    assert viewer.get("/api/part-policy").json()["data"]["rules"][0]["field"] == "mpn"

    token = _mint(authed_client, read_only=True)["token"]
    anon = TestClient(app)
    r = anon.get("/api/part-policy", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["rules"][0]["field"] == "mpn"


# ---------------------------------------------------------------------------
# Create enforcement
# ---------------------------------------------------------------------------


def test_create_rule_blocks_create_with_structured_422(authed_client, db):
    cat = _category(authed_client, "Resistors")
    _set_policy(
        authed_client,
        [
            {"id": "mfr", "field": "manufacturer", "when": "create",
             "hint": "manufacturer name as printed on the datasheet"},
            {"id": "cat", "field": "category_id", "when": "create"},
            {"id": "ds", "field": "datasheet"},
        ],
    )

    r = authed_client.post("/api/parts", json={"mpn": "BLOCKED-1"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "part.policy_violation"
    assert body["data"] is None
    assert body["status"]["category"] == "validation_error"
    missing = {m["rule_id"]: m for m in body["missing"]}
    # Only create rules block; the datasheet rule is not in the 422.
    assert set(missing) == {"mfr", "cat"}
    assert missing["mfr"] == {
        "field": "manufacturer",
        "rule_id": "mfr",
        "when": "create",
        "message": "Manufacturer is required",
        "hint": "manufacturer name as printed on the datasheet",
    }
    assert {"id": cat["id"], "name": "Resistors", "path": "Resistors"} in missing["cat"]["allowed"]
    assert db.query(Part).filter(Part.mpn == "BLOCKED-1").count() == 0

    ok = authed_client.post(
        "/api/parts",
        json={"mpn": "BLOCKED-1", "manufacturer": "Yageo", "category_id": cat["id"]},
    )
    assert ok.status_code == 201, ok.text


def test_whitespace_does_not_satisfy_a_rule(authed_client):
    _set_policy(authed_client, [{"field": "description", "when": "create"}])
    r = authed_client.post("/api/parts", json={"mpn": "WS-1", "description": "   "})
    assert r.status_code == 422


def test_validate_and_create_agree(authed_client):
    _set_policy(
        authed_client,
        [{"field": "manufacturer", "when": "create"}, {"field": "description"}],
    )
    payload = {"mpn": "AGREE-1"}
    v = authed_client.post("/api/parts/validate", json=payload).json()["data"]
    assert v["ok"] is False
    assert [(m["field"], m["when"]) for m in v["missing"]] == [
        ("manufacturer", "create"),
        ("description", "complete"),
    ]
    c = authed_client.post("/api/parts", json=payload)
    assert [m["field"] for m in c.json()["missing"]] == ["manufacturer"]

    payload["manufacturer"] = "ACME"
    v = authed_client.post("/api/parts/validate", json=payload).json()["data"]
    assert v["ok"] is True
    assert [m["field"] for m in v["missing"]] == ["description"]
    assert authed_client.post("/api/parts", json=payload).status_code == 201


def test_mpn_conflict_wins_over_policy(authed_client):
    create_part(authed_client, "Existing", mpn="DUP-1")
    _set_policy(authed_client, [{"field": "manufacturer", "when": "create"}])
    r = authed_client.post("/api/parts", json={"mpn": "DUP-1"})
    assert r.status_code == 409
    assert r.json()["code"] == "part.mpn_conflict"


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_category_scope_includes_descendants(authed_client):
    passives = _category(authed_client, "Passives")
    resistors = _category(authed_client, "Resistors", parent_id=passives["id"])
    ics = _category(authed_client, "ICs")
    _set_policy(
        authed_client,
        [{"field": "footprint", "when": "create", "scope": {"category_ids": [passives["id"]]}}],
    )

    assert authed_client.post(
        "/api/parts", json={"mpn": "SC-1", "category_id": resistors["id"]}
    ).status_code == 422
    assert authed_client.post(
        "/api/parts", json={"mpn": "SC-2", "category_id": ics["id"]}
    ).status_code == 201
    assert authed_client.post("/api/parts", json={"mpn": "SC-3"}).status_code == 201


def test_category_scope_without_descendants(authed_client):
    passives = _category(authed_client, "Passives")
    resistors = _category(authed_client, "Resistors", parent_id=passives["id"])
    _set_policy(
        authed_client,
        [{"field": "footprint", "when": "create",
          "scope": {"category_ids": [passives["id"]], "include_descendants": False}}],
    )
    assert authed_client.post(
        "/api/parts", json={"mpn": "SD-1", "category_id": resistors["id"]}
    ).status_code == 201
    assert authed_client.post(
        "/api/parts", json={"mpn": "SD-2", "category_id": passives["id"]}
    ).status_code == 422


def test_part_type_scope(authed_client):
    _set_policy(
        authed_client,
        [{"field": "manufacturer", "when": "create", "scope": {"part_types": ["linked"]}}],
    )
    assert authed_client.post(
        "/api/parts", json={"mpn": "PT-1", "part_type": "local"}
    ).status_code == 201
    assert authed_client.post(
        "/api/parts", json={"mpn": "PT-2", "part_type": "linked"}
    ).status_code == 422


# ---------------------------------------------------------------------------
# policy-status (complete rules, assets)
# ---------------------------------------------------------------------------


def test_policy_status_reports_assets_until_attached(authed_client):
    part_id = create_part(authed_client, "Status part", mpn="ST-1")
    _set_policy(
        authed_client,
        [
            {"id": "ds", "field": "datasheet"},
            {"id": "sym", "field": "eda.symbol"},
            {"id": "spice", "field": "eda.spice"},
            {"id": "mfr", "field": "manufacturer", "when": "create"},
        ],
    )

    status = authed_client.get(f"/api/parts/{part_id}/policy-status").json()["data"]
    assert status["part_id"] == part_id
    assert status["complete"] is False
    # Create rules are reported too: this part predates the rule.
    assert {m["rule_id"] for m in status["missing"]} == {"ds", "sym", "spice", "mfr"}

    up = authed_client.post(
        "/api/attachments",
        files={"file": ("ds.png", PNG_MAGIC + b"x", "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "datasheet"},
    )
    assert up.status_code == 201, up.text
    eda = authed_client.put(f"/api/parts/{part_id}/eda", json={"symbol_ref_external": "Device:R"})
    assert eda.status_code == 200, eda.text

    status = authed_client.get(f"/api/parts/{part_id}/policy-status").json()["data"]
    assert {m["rule_id"] for m in status["missing"]} == {"spice", "mfr"}


def test_category_default_symbol_satisfies_eda_symbol(authed_client):
    cat = _category(authed_client, "Resistors", default_symbol_ref="Device:R")
    _set_policy(authed_client, [{"field": "eda.symbol"}, {"field": "eda.footprint"}])

    v = authed_client.post(
        "/api/parts/validate", json={"mpn": "CD-1", "category_id": cat["id"]}
    ).json()["data"]
    assert [m["field"] for m in v["missing"]] == ["eda.footprint"]

    part_id = create_part(authed_client, "CD", mpn="CD-1", category_id=cat["id"])
    status = authed_client.get(f"/api/parts/{part_id}/policy-status").json()["data"]
    assert [m["field"] for m in status["missing"]] == ["eda.footprint"]


def test_policy_status_complete_when_no_rules(authed_client):
    part_id = create_part(authed_client, "Nothing required")
    status = authed_client.get(f"/api/parts/{part_id}/policy-status").json()["data"]
    assert status == {"part_id": part_id, "complete": True, "missing": []}


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def test_policy_of_one_workspace_does_not_gate_another(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    _set_policy(a, [{"field": "manufacturer", "when": "create"}])

    assert b.get("/api/part-policy").json()["data"]["rules"] == []
    assert b.post("/api/parts", json={"mpn": "ISO-B-1"}).status_code == 201
    assert b.post("/api/parts/validate", json={"mpn": "ISO-B-2"}).json()["data"]["ok"] is True
    assert a.post("/api/parts", json={"mpn": "ISO-A-1"}).status_code == 422


def test_rule_scope_refuses_a_foreign_category(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    foreign = _category(a, "Foreign")["id"]
    r = _put_policy(b, [{"field": "mpn", "scope": {"category_ids": [foreign]}}])
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "part_policy.invalid_rule"


def test_validate_refuses_a_foreign_category(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    foreign = _category(a, "Foreign")["id"]
    r = b.post("/api/parts/validate", json={"mpn": "X-1", "category_id": foreign})
    assert r.status_code == 404, r.text


def test_validate_refuses_a_foreign_storage_location(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    r = a.post("/api/storage", json={"name": "A-shelf"})
    assert r.status_code in (200, 201), r.text
    loc = r.json()["data"]["id"]
    body = {"mpn": "X-2", "default_storage_location_id": loc}
    assert b.post("/api/parts/validate", json=body).status_code == 404


def test_policy_status_refuses_a_foreign_part(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    part_id = create_part(a, "A's part")
    r = b.get(f"/api/parts/{part_id}/policy-status")
    assert r.status_code == 404, r.text
    assert b.get(f"/api/parts/{uuid.uuid4()}/policy-status").status_code == 404


def test_allowed_categories_are_this_workspace_only(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    _category(a, "Only in A")
    mine = _category(b, "Only in B")
    _set_policy(b, [{"field": "category_id", "when": "create"}])
    missing = b.post("/api/parts", json={"mpn": "AL-1"}).json()["missing"]
    assert [c["id"] for c in missing[0]["allowed"]] == [mine["id"]]


def test_validate_refuses_what_create_refuses(authed_client):
    """A dry run must never say `ok` for a body the create then rejects."""
    cat = _category(authed_client, "Retired")
    assert authed_client.post(f"/api/categories/{cat['id']}/archive").status_code == 200
    body = {"mpn": "ARCH-1", "category_id": cat["id"]}
    v = authed_client.post("/api/parts/validate", json=body)
    c = authed_client.post("/api/parts", json=body)
    assert v.status_code == c.status_code == 409
    assert v.json()["code"] == c.json()["code"] == "category.archived"

    body = {"mpn": "ARCH-2", "default_storage_location_id": str(uuid.uuid4())}
    assert authed_client.post("/api/parts/validate", json=body).status_code == 404
