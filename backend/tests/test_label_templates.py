"""`/api/label-templates` — CRUD, defaults, render, test-print.

Covers the contract an operator depends on:

* the one-default-per-(workspace, entity_type) partial unique index, and the
  in-transaction demotion that keeps promoting a template from violating it,
* role gating — reads are member-level, every mutation is admin+,
* the debug render (sample context, no code minted) and the live test-print
  (real entity -> object code from #892 -> JScript -> printer),
* the printer-failure path: a 409 with the `print_jobs` row still there.

Workspace isolation lives in `tests/test_label_template_isolation.py`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import ErrorCodes
from app.domain.audit.models import AuditLog
from app.domain.codes.models import ObjectCode
from app.domain.printing.default_templates import BUILT_IN_TEMPLATES
from app.domain.printing.models import LABEL_ENTITY_TYPES, LabelTemplate
from app.main import app
from tests._factories import create_part, create_storage, signup_user
from tests.test_print_service import dead_printer as _dead_printer
from tests.test_print_service import stub_printer as _stub_printer

# pytest resolves a fixture by module-level NAME, so the stub/dead printer
# fixtures from `test_print_service` are re-bound here under the names the
# tests below request. Aliased on import so the linter sees them used rather
# than shadowed by the parameters of the same name.
stub_printer = _stub_printer
dead_printer = _dead_printer

BASE = "/api/label-templates"


def _template_body(**overrides):
    body = {
        "name": "Bin label",
        "entity_type": "part",
        "width_mm": 50.0,
        "height_mm": 30.0,
        "elements": [
            {"kind": "qr", "x_mm": 2, "y_mm": 2},
            {"kind": "text", "x_mm": 25, "y_mm": 3, "binding": "name"},
        ],
    }
    body.update(overrides)
    return body


def _create(client: TestClient, **overrides) -> dict:
    r = client.post(BASE, json=_template_body(**overrides))
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _audit_actions(db, action_prefix: str) -> list[str]:
    return [
        row.action
        for row in db.execute(select(AuditLog)).scalars()
        if row.action.startswith(action_prefix)
    ]


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------


def test_built_in_catalog_covers_every_labelable_entity_type():
    """A codeable type with no default template is a type nobody can print."""
    assert set(BUILT_IN_TEMPLATES) == set(LABEL_ENTITY_TYPES)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_and_fetch_roundtrip(authed_client: TestClient):
    created = _create(authed_client)
    assert created["entity_type"] == "part"
    assert created["is_default"] is False
    assert len(created["elements"]) == 2

    r = authed_client.get(f"{BASE}/{created['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "Bin label"


def test_responses_use_the_standard_envelope(authed_client: TestClient):
    r = authed_client.get(BASE)
    body = r.json()
    assert set(body) >= {"data", "status"}
    assert body["status"]["category"] == "ok"


def test_list_filters_by_entity_type(authed_client: TestClient):
    _create(authed_client, name="P", entity_type="part")
    _create(authed_client, name="L", entity_type="lot")

    r = authed_client.get(BASE, params={"entity_type": "lot"})
    assert r.status_code == 200, r.text
    names = [row["name"] for row in r.json()["data"]]
    assert names == ["L"]


def test_create_rejects_an_unknown_element_kind(authed_client: TestClient):
    """The renderer skips a kind it cannot draw, so an unvalidated write would
    store a template that silently prints a blank label."""
    r = authed_client.post(
        BASE, json=_template_body(elements=[{"kind": "hologram", "x_mm": 0, "y_mm": 0}])
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == ErrorCodes.LABEL_TEMPLATE_INVALID


def test_create_rejects_an_unknown_entity_type(authed_client: TestClient):
    r = authed_client.post(BASE, json=_template_body(entity_type="project"))
    assert r.status_code == 422, r.text


def test_element_list_is_bounded(authed_client: TestClient):
    r = authed_client.post(
        BASE,
        json=_template_body(
            elements=[{"kind": "text", "text": "x"} for _ in range(101)]
        ),
    )
    assert r.status_code == 422, r.text


def test_patch_updates_only_the_named_fields(authed_client: TestClient):
    created = _create(authed_client)
    r = authed_client.patch(f"{BASE}/{created['id']}", json={"heat": 120})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["heat"] == 120
    assert data["name"] == "Bin label"  # untouched
    assert len(data["elements"]) == 2


def test_empty_patch_is_a_400(authed_client: TestClient):
    created = _create(authed_client)
    r = authed_client.patch(f"{BASE}/{created['id']}", json={})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == ErrorCodes.LABEL_TEMPLATE_INVALID


def test_delete_removes_the_template(authed_client: TestClient):
    created = _create(authed_client)
    r = authed_client.delete(f"{BASE}/{created['id']}")
    assert r.status_code == 200, r.text
    assert authed_client.get(f"{BASE}/{created['id']}").status_code == 404


def test_unknown_template_id_is_404(authed_client: TestClient):
    r = authed_client.get(f"{BASE}/{uuid.uuid4()}")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# One default per (workspace, entity_type)
# ---------------------------------------------------------------------------


def test_creating_a_second_default_demotes_the_first(authed_client: TestClient, db):
    first = _create(authed_client, name="First", is_default=True)
    second = _create(authed_client, name="Second", is_default=True)

    assert second["is_default"] is True
    r = authed_client.get(f"{BASE}/{first['id']}")
    assert r.json()["data"]["is_default"] is False

    # And the DB agrees: exactly one default for this (workspace, type).
    defaults = [
        row
        for row in db.execute(select(LabelTemplate)).scalars()
        if row.entity_type == "part" and row.is_default
    ]
    assert len(defaults) == 1


def test_promoting_via_patch_demotes_the_incumbent(authed_client: TestClient):
    first = _create(authed_client, name="First", is_default=True)
    second = _create(authed_client, name="Second")

    r = authed_client.patch(f"{BASE}/{second['id']}", json={"is_default": True})
    assert r.status_code == 200, r.text
    assert authed_client.get(f"{BASE}/{first['id']}").json()["data"]["is_default"] is False


def test_defaults_are_independent_across_entity_types(authed_client: TestClient):
    part = _create(authed_client, name="P", entity_type="part", is_default=True)
    lot = _create(authed_client, name="L", entity_type="lot", is_default=True)

    assert authed_client.get(f"{BASE}/{part['id']}").json()["data"]["is_default"] is True
    assert authed_client.get(f"{BASE}/{lot['id']}").json()["data"]["is_default"] is True


def test_repromoting_the_current_default_is_a_no_op(authed_client: TestClient):
    """`exclude_id` keeps the demotion from clearing the row being promoted."""
    row = _create(authed_client, is_default=True)
    r = authed_client.patch(f"{BASE}/{row['id']}", json={"is_default": True})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["is_default"] is True


def test_many_non_default_templates_coexist(authed_client: TestClient):
    """The unique index is PARTIAL — only default rows are constrained."""
    for i in range(3):
        _create(authed_client, name=f"T{i}")
    rows = authed_client.get(BASE, params={"entity_type": "part"}).json()["data"]
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# Built-in defaults
# ---------------------------------------------------------------------------


def test_seed_defaults_creates_one_default_per_entity_type(authed_client: TestClient):
    r = authed_client.post(f"{BASE}/defaults")
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert len(rows) == len(LABEL_ENTITY_TYPES)
    assert {row["entity_type"] for row in rows} == set(LABEL_ENTITY_TYPES)
    assert all(row["is_default"] for row in rows)


def test_seed_defaults_is_idempotent(authed_client: TestClient):
    first = authed_client.post(f"{BASE}/defaults").json()["data"]
    second = authed_client.post(f"{BASE}/defaults").json()["data"]
    assert len(second) == len(first)
    assert {row["id"] for row in second} == {row["id"] for row in first}


def test_seed_defaults_leaves_an_edited_default_alone(authed_client: TestClient):
    mine = _create(authed_client, name="My part label", entity_type="part", is_default=True)
    rows = authed_client.post(f"{BASE}/defaults").json()["data"]

    part_defaults = [
        row for row in rows if row["entity_type"] == "part" and row["is_default"]
    ]
    assert len(part_defaults) == 1
    assert part_defaults[0]["id"] == mine["id"]


def test_seed_defaults_audits_only_when_it_creates(authed_client: TestClient, db):
    authed_client.post(f"{BASE}/defaults")
    authed_client.post(f"{BASE}/defaults")
    seeded = _audit_actions(db, "label_template.defaults_seeded")
    assert len(seeded) == 1


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verb", "action"),
    [("create", "label_template.created"), ("patch", "label_template.updated"),
     ("delete", "label_template.deleted")],
)
def test_every_mutation_writes_an_audit_row(
    authed_client: TestClient, db, verb: str, action: str
):
    created = _create(authed_client)
    if verb == "patch":
        authed_client.patch(f"{BASE}/{created['id']}", json={"heat": 110})
    elif verb == "delete":
        authed_client.delete(f"{BASE}/{created['id']}")
    assert action in _audit_actions(db, "label_template.")


# ---------------------------------------------------------------------------
# Role gating
# ---------------------------------------------------------------------------


def _member_client(owner: TestClient, role: str) -> TestClient:
    """A second client whose user was invited into the owner's workspace."""
    email = f"m-{uuid.uuid4().hex[:8]}@example.com"
    invite = owner.post("/api/invitations", json={"email": email, "role": role})
    assert invite.status_code in (200, 201), invite.text
    token = invite.json()["data"]["token"]

    other = TestClient(app)
    signup_user(other, email=email)
    accept = other.post("/api/invitations/accept", json={"token": token})
    assert accept.status_code in (200, 201), accept.text
    switch = other.post(f"/api/workspaces/{invite.json()['data']['workspace_id']}/switch")
    assert switch.status_code == 200, switch.text
    return other


def test_a_member_may_read_but_not_mutate(authed_client: TestClient):
    """Templates are shared infrastructure — one row decides what every label
    in the workspace looks like — so mutating them is admin+."""
    _create(authed_client, is_default=True)
    member = _member_client(authed_client, "member")

    assert member.get(BASE).status_code == 200
    assert member.post(BASE, json=_template_body()).status_code == 403
    assert member.post(f"{BASE}/defaults").status_code == 403


def test_an_admin_may_mutate(authed_client: TestClient):
    admin = _member_client(authed_client, "admin")
    r = admin.post(BASE, json=_template_body(name="Admin made this"))
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Debug render
# ---------------------------------------------------------------------------


def test_jscript_endpoint_renders_a_sample_label(authed_client: TestClient, db):
    created = _create(authed_client)
    r = authed_client.get(f"{BASE}/{created['id']}/jscript")
    assert r.status_code == 200, r.text

    jscript = r.json()["data"]["jscript"]
    assert jscript.startswith("m m\r\nJ\r\n")
    assert jscript.rstrip().endswith("A 1")
    assert "/c/SAMPLE00" in jscript  # the sample scan URL

    # A sample render mints nothing — it works on a workspace that has never
    # labelled anything.
    assert db.execute(select(ObjectCode)).scalars().all() == []


def test_jscript_endpoint_404s_for_an_unknown_template(authed_client: TestClient):
    assert authed_client.get(f"{BASE}/{uuid.uuid4()}/jscript").status_code == 404


# ---------------------------------------------------------------------------
# Test print
# ---------------------------------------------------------------------------


def test_test_print_ships_a_sample_label(authed_client: TestClient, stub_printer):
    created = _create(authed_client)
    r = authed_client.post(f"{BASE}/{created['id']}/test-print", json={})
    assert r.status_code == 200, r.text

    data = r.json()["data"]
    assert data["status"] == "printed"
    assert data["code"] is None  # sample render mints nothing
    assert uuid.UUID(data["print_job_id"])


def test_test_print_for_a_real_entity_mints_its_object_code(
    authed_client: TestClient, db, stub_printer
):
    """`{{code}}` comes from the #892 object-codes service — there is one code
    system in this app and the label uses it."""
    part_id = create_part(authed_client, name="Resistor 10k", mpn="RC0805-10K")
    created = _create(authed_client, entity_type="part")

    r = authed_client.post(
        f"{BASE}/{created['id']}/test-print", json={"entity_id": part_id}
    )
    assert r.status_code == 200, r.text
    code = r.json()["data"]["code"]
    assert code

    # The very same row `GET /api/codes/{code}` resolves.
    resolved = authed_client.get(f"/api/codes/{code}")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["data"]["entity_id"] == part_id


def test_test_print_reuses_an_existing_code(authed_client: TestClient, stub_printer):
    """Get-or-create: printing a second label must not re-label the part."""
    part_id = create_part(authed_client)
    created = _create(authed_client, entity_type="part")
    first = authed_client.post(
        f"{BASE}/{created['id']}/test-print", json={"entity_id": part_id}
    ).json()["data"]["code"]
    second = authed_client.post(
        f"{BASE}/{created['id']}/test-print", json={"entity_id": part_id}
    ).json()["data"]["code"]
    assert first == second


def test_test_print_renders_the_entity_fields_into_the_label(
    authed_client: TestClient, stub_printer
):
    storage_id = create_storage(authed_client, name="Shelf A3")
    created = _create(
        authed_client,
        entity_type="storage_location",
        elements=[{"kind": "text", "x_mm": 2, "y_mm": 2, "binding": "name"}],
    )
    r = authed_client.post(
        f"{BASE}/{created['id']}/test-print", json={"entity_id": storage_id}
    )
    assert r.status_code == 200, r.text
    payload = stub_printer.received.decode("latin-1")
    assert ";Shelf A3" in payload


def test_test_print_of_a_foreign_entity_id_is_404(
    authed_client: TestClient, stub_printer
):
    created = _create(authed_client, entity_type="part")
    r = authed_client.post(
        f"{BASE}/{created['id']}/test-print", json={"entity_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404, r.text


def test_test_print_copies_reach_the_print_count(
    authed_client: TestClient, stub_printer
):
    created = _create(authed_client)
    r = authed_client.post(f"{BASE}/{created['id']}/test-print", json={"copies": 3})
    assert r.status_code == 200, r.text
    assert "A 3" in stub_printer.received.decode("latin-1")


def test_test_print_is_admin_only(authed_client: TestClient):
    created = _create(authed_client)
    member = _member_client(authed_client, "member")
    r = member.post(f"{BASE}/{created['id']}/test-print", json={})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Printer failure — 409, never a 500, and the job row survives
# ---------------------------------------------------------------------------


def test_printer_failure_is_a_409_not_a_500(authed_client: TestClient, dead_printer):
    created = _create(authed_client)
    r = authed_client.post(f"{BASE}/{created['id']}/test-print", json={})

    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == ErrorCodes.PRINTER_UNREACHABLE
    assert body["status"]["category"] == "conflict"
    assert body["data"] is None
    assert body["print_job_id"]


def test_printer_failure_leaves_the_print_job_for_inspection(
    authed_client: TestClient, db, dead_printer
):
    """`get_db` rolls back on a raised exception, so the route RETURNS the 409
    rather than raising — otherwise the failed job the operator is told to
    inspect would be rolled straight back out."""
    created = _create(authed_client)
    r = authed_client.post(f"{BASE}/{created['id']}/test-print", json={})
    job_id = r.json()["print_job_id"]

    from app.domain.printing.models import PrintJob

    job = db.execute(
        select(PrintJob).where(PrintJob.id == uuid.UUID(job_id))
    ).scalar_one()
    assert job.status == "failed"
    assert job.error
    assert job.target_type == "label_template"
    assert job.target_id == created["id"]


def test_printer_failure_writes_a_failure_audit_row(
    authed_client: TestClient, db, dead_printer
):
    created = _create(authed_client)
    authed_client.post(f"{BASE}/{created['id']}/test-print", json={})
    assert "label_template.test_print_failed" in _audit_actions(db, "label_template.")


def test_unconfigured_printer_is_also_a_409(authed_client: TestClient, monkeypatch):
    """PRINT_HOST empty is the dev/CI default — it must fail closed with the
    same clean 409, not a 500."""
    from app.core.config import settings

    monkeypatch.setattr(settings(), "PRINT_HOST", "", raising=False)
    created = _create(authed_client)
    r = authed_client.post(f"{BASE}/{created['id']}/test-print", json={})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == ErrorCodes.PRINTER_UNREACHABLE
