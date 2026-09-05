"""Workspace-isolation pins for `label_templates`.

Isolation in this codebase is enforced in code, not the DB (CLAUDE.md), so a
new router has to prove it re-implements the contract rather than inheriting
it. Mirrors `tests/test_workspace_isolation.py`: create in workspace A, prove
workspace B sees nothing, reaches nothing, and cannot write through it.

The interesting cases here are the ones a naive implementation gets wrong:

* `GET /{id}` on A's template from B — must be 404, never 403, because a 403
  confirms the row exists.
* The one-default index is per WORKSPACE, so both workspaces may hold their
  own default for the same entity type.
* `test-print` takes an `entity_id` from the request body — the classic
  cross-tenant write vector. Minting must refuse a foreign id.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.printing.models import LabelTemplate
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_label_templates import BASE, _create, _template_body
from tests.test_print_service import stub_printer as _stub_printer

# See the note in `test_label_templates.py` — pytest looks fixtures up by
# module-level name, so the imported one is re-bound here.
stub_printer = _stub_printer


def _two_workspaces() -> tuple[TestClient, TestClient]:
    """Two clients, each the owner of its own, unrelated workspace."""
    a = TestClient(app)
    signup_user(a)
    b = TestClient(app)
    signup_user(b)
    return a, b


def _ws_id(client: TestClient) -> str:
    """The client's only workspace id, read back through the API."""
    return client.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]


def test_list_never_shows_another_workspaces_templates():
    a, b = _two_workspaces()
    _create(a, name="A only")
    _create(b, name="B only")

    assert [row["name"] for row in a.get(BASE).json()["data"]] == ["A only"]
    assert [row["name"] for row in b.get(BASE).json()["data"]] == ["B only"]


def test_fetching_a_foreign_template_is_404_not_403():
    """403 would confirm the row exists — an existence oracle across tenants."""
    a, b = _two_workspaces()
    a_template = _create(a, name="A only")

    r = b.get(f"{BASE}/{a_template['id']}")
    assert r.status_code == 404, r.text


def test_patching_a_foreign_template_is_404():
    a, b = _two_workspaces()
    a_template = _create(a, name="A only", is_default=True)

    r = b.patch(f"{BASE}/{a_template['id']}", json={"name": "hijacked"})
    assert r.status_code == 404, r.text
    # And A's row is untouched.
    assert a.get(f"{BASE}/{a_template['id']}").json()["data"]["name"] == "A only"


def test_deleting_a_foreign_template_is_404_and_leaves_it_alive():
    a, b = _two_workspaces()
    a_template = _create(a, name="A only")

    assert b.delete(f"{BASE}/{a_template['id']}").status_code == 404
    assert a.get(f"{BASE}/{a_template['id']}").status_code == 200


def test_rendering_a_foreign_template_is_404():
    a, b = _two_workspaces()
    a_template = _create(a)
    assert b.get(f"{BASE}/{a_template['id']}/jscript").status_code == 404


def test_test_printing_a_foreign_template_is_404(stub_printer):
    a, b = _two_workspaces()
    a_template = _create(a)
    r = b.post(f"{BASE}/{a_template['id']}/test-print", json={})
    assert r.status_code == 404, r.text
    assert not stub_printer.received


def test_test_print_refuses_a_foreign_entity_id(stub_printer):
    """The cross-tenant WRITE vector: B owns the template but names A's part.
    Minting must validate `entity_id` against B's workspace, or B ends up with
    a code — and a printed label — for A's row."""
    a, b = _two_workspaces()
    a_part = create_part(a, name="A's secret part")
    b_template = _create(b, entity_type="part")

    r = b.post(f"{BASE}/{b_template['id']}/test-print", json={"entity_id": a_part})
    assert r.status_code == 404, r.text
    assert not stub_printer.received


def test_both_workspaces_may_hold_a_default_for_the_same_entity_type(db):
    """The partial unique index is on (workspace_id, entity_type) — scoping it
    to the workspace is what lets each tenant have its own default."""
    a, b = _two_workspaces()
    a_default = _create(a, name="A default", entity_type="part", is_default=True)
    b_default = _create(b, name="B default", entity_type="part", is_default=True)

    assert a.get(f"{BASE}/{a_default['id']}").json()["data"]["is_default"] is True
    assert b.get(f"{BASE}/{b_default['id']}").json()["data"]["is_default"] is True

    rows = list(db.execute(select(LabelTemplate)).scalars())
    part_defaults = [r for r in rows if r.entity_type == "part" and r.is_default]
    assert len(part_defaults) == 2
    assert len({r.workspace_id for r in part_defaults}) == 2


def test_a_non_admin_gets_404_not_403_for_a_foreign_template(stub_printer):
    """BE2-009: resource existence resolves BEFORE the role check.

    With a route-level `Depends(require_role("admin"))` the role check fires
    first and a member probing another workspace's id gets 403 — an oracle
    saying "this id exists somewhere, you just lack the role". The
    `/{template_id}` mutations use `require_resource_access` instead.
    """
    from tests.test_label_templates import _member_client

    a, b = _two_workspaces()
    a_template = _create(a, name="A only")
    b_member = _member_client(b, "member")  # a non-admin in workspace B

    assert b_member.patch(f"{BASE}/{a_template['id']}", json={"name": "x"}).status_code == 404
    assert b_member.delete(f"{BASE}/{a_template['id']}").status_code == 404
    assert b_member.post(f"{BASE}/{a_template['id']}/test-print", json={}).status_code == 404
    assert not stub_printer.received
    # A's template is untouched.
    assert a.get(f"{BASE}/{a_template['id']}").json()["data"]["name"] == "A only"


def test_creating_a_default_does_not_demote_another_workspaces(db):
    """The CREATE path calls `clear_existing_default` too, not just PATCH."""
    a, b = _two_workspaces()
    a_default = _create(a, name="A default", entity_type="part", is_default=True)

    _create(b, name="B default", entity_type="part", is_default=True)

    assert a.get(f"{BASE}/{a_default['id']}").json()["data"]["is_default"] is True


def test_promoting_a_default_does_not_demote_another_workspaces(db):
    """`clear_existing_default` must filter by workspace; without the filter
    B's promotion silently unsets A's default."""
    a, b = _two_workspaces()
    a_default = _create(a, name="A default", entity_type="part", is_default=True)
    b_second = _create(b, name="B second", entity_type="part")
    _create(b, name="B first", entity_type="part", is_default=True)

    r = b.patch(f"{BASE}/{b_second['id']}", json={"is_default": True})
    assert r.status_code == 200, r.text
    assert a.get(f"{BASE}/{a_default['id']}").json()["data"]["is_default"] is True


def test_seeding_defaults_only_touches_the_calling_workspace(db):
    a, b = _two_workspaces()
    a.post(f"{BASE}/defaults")

    assert b.get(BASE).json()["data"] == []
    rows = list(db.execute(select(LabelTemplate)).scalars())
    assert len({row.workspace_id for row in rows}) == 1


def test_created_templates_carry_the_callers_workspace_id(db):
    a, _b = _two_workspaces()
    created = _create(a)
    row = db.execute(
        select(LabelTemplate).where(LabelTemplate.id == uuid.UUID(created["id"]))
    ).scalar_one()

    assert str(row.workspace_id) == _ws_id(a)


def test_a_template_body_cannot_smuggle_a_workspace_id(db):
    """`workspace_id` is not in the schema; an extra key must not become one."""
    a, b = _two_workspaces()
    b_ws = _ws_id(b)

    body = _template_body()
    body["workspace_id"] = b_ws
    r = a.post(BASE, json=body)
    assert r.status_code == 201, r.text

    row = db.execute(
        select(LabelTemplate).where(LabelTemplate.id == uuid.UUID(r.json()["data"]["id"]))
    ).scalar_one()
    assert str(row.workspace_id) != b_ws
