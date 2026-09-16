"""The three part-authoring MCP tools: `create_part`, `set_part_category`,
`set_part_specs`.

Split out of `test_mcp.py` rather than appended to it — that file is
already 1,500 lines and covers the *surface* (auth, the write gate, rate
ceilings, the principal contextvar). This one covers one feature, and
reuses that file's harness so there is exactly one MCP client helper in
the suite.

What is pinned here, and why each is behavioural rather than structural:

* **the duplicate-MPN answer is a SUCCESS, not an error.** An agent
  reading a schematic asks for a part it expects to already exist half
  the time; turning the REST 409 into an error would make "the part is
  already there" indistinguishable from "the call was wrong", and the
  model's only recovery would be to guess. So the tool returns
  `found_existing: true` and the existing row — and the test asserts no
  second part was written, because a create that silently succeeded
  twice would also satisfy a payload-only assertion.
* **provider-owned spec rows survive an agent write.** Checked by
  seeding a `source='provider'` row and reading the row back out of the
  database after the call, not by trusting the tool's own report.
* **cross-workspace ids are refused for all three tools.** One test per
  tool, per the CLAUDE.md rule that every new write surface gets its own
  isolation test — a shared helper would pass while one tool quietly
  stopped filtering.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_mcp import _mint, call, call_error, mcp_session


@pytest.fixture
def full_token(authed_client) -> str:
    return _mint(authed_client)["token"]


@pytest.fixture
def readonly_token(authed_client) -> str:
    return _mint(authed_client, read_only=True)["token"]


def _category(client: TestClient, name: str, **extra) -> dict:
    body = {"name": name}
    body.update(extra)
    r = client.post("/api/categories", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _audit_rows(db, action: str) -> list[AuditLog]:
    return list(
        db.query(AuditLog).filter(AuditLog.action == action).order_by(AuditLog.id).all()
    )


def _fields(db, part_id: str) -> dict[str, CustomField]:
    # Expired first: the tool ran on its own session, and SQLAlchemy would
    # otherwise hand back whatever this session last loaded rather than
    # what is on disk — which is the half of the assertion that matters.
    db.expire_all()
    rows = (
        db.query(CustomField)
        .filter(CustomField.object_type == "part")
        .filter(CustomField.object_id == uuid.UUID(part_id))
        .all()
    )
    return {row.key: row for row in rows}


def _seed_provider_field(db, authed_client, part_id: str, key: str, value: str) -> None:
    """A `source='provider'` row, which no route can create.

    Provider rows are only ever written by the refresh path, so the test
    has to write one directly; the tool's whole job here is to leave it
    alone.
    """
    ws_id = uuid.UUID(
        authed_client.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]
    )
    db.add(
        CustomField(
            workspace_id=ws_id,
            object_type="part",
            object_id=uuid.UUID(part_id),
            key=key,
            value=value,
            source="provider",
        )
    )
    db.flush()


# ---------------------------------------------------------------------------
# create_part
# ---------------------------------------------------------------------------


async def test_create_part_writes_the_part_and_reports_it_as_new(
    authed_client, full_token, db
):
    async with mcp_session(full_token) as s:
        out = await call(
            s,
            "create_part",
            name="LM358 op-amp",
            mpn="LM358DR",
            manufacturer="TI",
            description="Dual op-amp",
        )

    assert out["found_existing"] is False
    assert out["part"]["name"] == "LM358 op-amp"
    assert out["part"]["mpn"] == "LM358DR"
    assert out["part"]["manufacturer"] == "TI"
    assert out["part"]["part_url"].endswith(f"/parts/{out['part']['id']}")

    row = db.get(Part, uuid.UUID(out["part"]["id"]))
    assert row is not None
    assert row.part_type == "local"


async def test_create_part_defaults_the_name_to_the_mpn(authed_client, full_token):
    async with mcp_session(full_token) as s:
        out = await call(s, "create_part", mpn="STM32G071CBT6")
    assert out["part"]["name"] == "STM32G071CBT6"


async def test_create_part_needs_a_name_or_an_mpn(authed_client, full_token):
    async with mcp_session(full_token) as s:
        error = await call_error(s, "create_part", manufacturer="TI")
    assert "part.name_or_mpn_required" in error


async def test_duplicate_mpn_reports_the_existing_part_as_a_success(
    authed_client, full_token, db
):
    """The 409 the REST route raises becomes "found it" here.

    Also asserts the part count, because a tool that created a second
    row AND reported the first one would pass a payload-only check.
    """
    existing = create_part(authed_client, "Already here", mpn="DUP-1")

    async with mcp_session(full_token) as s:
        out = await call(s, "create_part", name="Different name", mpn="DUP-1")

    assert out["found_existing"] is True
    assert out["part"]["id"] == existing
    assert out["part"]["name"] == "Already here"
    assert db.query(Part).filter(Part.mpn == "DUP-1").count() == 1


async def test_duplicate_mpn_writes_no_audit_row(authed_client, full_token, db):
    """Nothing happened, so the trail must not claim a part was created."""
    create_part(authed_client, "Already here", mpn="DUP-2")
    before = len(_audit_rows(db, "part.created"))

    async with mcp_session(full_token) as s:
        await call(s, "create_part", mpn="DUP-2")

    assert len(_audit_rows(db, "part.created")) == before


async def test_a_padded_mpn_still_finds_the_existing_part(
    authed_client, full_token, db
):
    """Whitespace around an MPN must not turn "found it" into a failure.

    The create service strips before its uniqueness pre-check, so a
    padded MPN raises the conflict on the stripped value. Looking the
    existing part back up by the RAW argument found nothing and the tool
    re-raised the conflict as a hard error — the one outcome this tool
    exists to avoid. An MPN copied out of a schematic or a BOM cell
    carries trailing space more often than not.
    """
    existing = create_part(authed_client, "Already here", mpn="PAD-1")

    async with mcp_session(full_token) as s:
        out = await call(s, "create_part", mpn="  PAD-1  ")

    assert out["found_existing"] is True
    assert out["part"]["id"] == existing
    assert db.query(Part).filter(Part.mpn == "PAD-1").count() == 1


async def test_create_part_refuses_an_over_long_field(authed_client, full_token, db):
    """A 201-character MPN is a bad argument, not a server error.

    `mpn` is `varchar(200)`. Without a length on the schema this reached
    the database and came back as a DataError — a 500 on the REST side
    and an opaque "Error executing tool" here, neither of which tells
    the caller what to do differently.
    """
    async with mcp_session(full_token) as s:
        error = await call_error(s, "create_part", mpn="X" * 201)

    assert "part.invalid_field" in error
    assert "mpn" in error
    assert db.query(Part).count() == 0


async def test_create_part_files_it_under_a_category(authed_client, full_token):
    category = _category(authed_client, "Amplifiers")
    async with mcp_session(full_token) as s:
        out = await call(
            s, "create_part", name="OPA333", mpn="OPA333AIDBVR", category_id=category["id"]
        )
    assert out["part"]["category_id"] == category["id"]


async def test_create_part_audit_matches_the_route(authed_client, full_token, db):
    owner_id = uuid.UUID(authed_client.get("/api/auth/me").json()["data"]["user"]["id"])
    async with mcp_session(full_token) as s:
        out = await call(s, "create_part", name="Audited", mpn="AUD-1", manufacturer="TI")

    rows = _audit_rows(db, "part.created")
    assert len(rows) == 1
    assert rows[0].user_id == owner_id
    assert rows[0].target_type == "part"
    assert rows[0].target_ids == [uuid.UUID(out["part"]["id"])]
    assert rows[0].comment == "fields=manufacturer,mpn,name"


async def test_create_part_is_refused_to_a_read_only_token(
    authed_client, readonly_token, db
):
    async with mcp_session(readonly_token) as s:
        error = await call_error(s, "create_part", name="Nope", mpn="NOPE-1")
    assert "auth.token_read_only" in error
    assert db.query(Part).filter(Part.mpn == "NOPE-1").count() == 0


async def test_create_part_refuses_a_category_from_another_workspace(db):
    """Workspace isolation: a foreign category id is not found, and no
    part is written by the attempt."""
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    foreign = _category(a, "Foreign cat")["id"]
    token_b = _mint(b)["token"]

    async with mcp_session(token_b) as s:
        error = await call_error(
            s, "create_part", name="Cross ws", mpn="XWS-1", category_id=foreign
        )
    assert "category.not_found" in error
    assert db.query(Part).filter(Part.mpn == "XWS-1").count() == 0


# ---------------------------------------------------------------------------
# set_part_category
# ---------------------------------------------------------------------------


async def test_set_part_category_by_name(authed_client, full_token, db):
    part_id = create_part(authed_client, "Needs filing", mpn="FILE-1")
    category = _category(authed_client, "Resistors")

    async with mcp_session(full_token) as s:
        out = await call(
            s, "set_part_category", part_id_or_mpn="FILE-1", category_id_or_name="Resistors"
        )

    assert out["category"]["name"] == "Resistors"
    assert db.get(Part, uuid.UUID(part_id)).category_id == uuid.UUID(category["id"])


async def test_set_part_category_by_id(authed_client, full_token, db):
    part_id = create_part(authed_client, "Needs filing", mpn="FILE-2")
    category = _category(authed_client, "Capacitors")

    async with mcp_session(full_token) as s:
        await call(
            s,
            "set_part_category",
            part_id_or_mpn=part_id,
            category_id_or_name=category["id"],
        )

    assert db.get(Part, uuid.UUID(part_id)).category_id == uuid.UUID(category["id"])


async def test_set_part_category_matches_a_name_case_insensitively(
    authed_client, full_token, db
):
    part_id = create_part(authed_client, "Needs filing", mpn="FILE-3")
    category = _category(authed_client, "Inductors")

    async with mcp_session(full_token) as s:
        await call(
            s, "set_part_category", part_id_or_mpn=part_id, category_id_or_name="inductors"
        )

    assert db.get(Part, uuid.UUID(part_id)).category_id == uuid.UUID(category["id"])


async def test_set_part_category_miss_lists_the_candidates(authed_client, full_token):
    """The model cannot guess a name it has never been shown, so the
    refusal carries the ones that exist."""
    create_part(authed_client, "Needs filing", mpn="FILE-4")
    _category(authed_client, "Resistors")
    _category(authed_client, "Capacitors")

    async with mcp_session(full_token) as s:
        error = await call_error(
            s, "set_part_category", part_id_or_mpn="FILE-4", category_id_or_name="Resistor"
        )

    assert "category.not_found" in error
    assert "Resistors" in error
    assert "Capacitors" in error


async def test_set_part_category_ambiguous_name_is_refused(authed_client, full_token):
    """Two categories differing only in case: pick neither, say both.

    Reachable because the name index is case-sensitive; only the slug
    has to differ, so the second one is created with an explicit slug.
    """
    create_part(authed_client, "Needs filing", mpn="FILE-5")
    _category(authed_client, "Diodes")
    _category(authed_client, "diodes", library_slug="diodes-lower")

    async with mcp_session(full_token) as s:
        error = await call_error(
            s, "set_part_category", part_id_or_mpn="FILE-5", category_id_or_name="DIODES"
        )

    assert "category.name_conflict" in error
    assert "Diodes" in error


async def test_set_part_category_audit_matches_the_route(authed_client, full_token, db):
    part_id = create_part(authed_client, "Filed", mpn="FILE-6")
    _category(authed_client, "Connectors")

    async with mcp_session(full_token) as s:
        await call(
            s, "set_part_category", part_id_or_mpn=part_id, category_id_or_name="Connectors"
        )

    rows = _audit_rows(db, "part.updated")
    assert len(rows) == 1
    assert rows[0].target_ids == [uuid.UUID(part_id)]
    assert rows[0].comment == "fields=category_id"


async def test_set_part_category_is_refused_to_a_read_only_token(
    authed_client, readonly_token, db
):
    part_id = create_part(authed_client, "Filed", mpn="FILE-7")
    _category(authed_client, "Crystals")
    async with mcp_session(readonly_token) as s:
        error = await call_error(
            s, "set_part_category", part_id_or_mpn=part_id, category_id_or_name="Crystals"
        )
    assert "auth.token_read_only" in error
    assert db.get(Part, uuid.UUID(part_id)).category_id is None


async def test_set_part_category_refuses_a_cross_workspace_category(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    foreign = _category(a, "Foreign cat")["id"]
    part_b = create_part(b, "B part", mpn="BWS-1")
    token_b = _mint(b)["token"]

    async with mcp_session(token_b) as s:
        error = await call_error(
            s, "set_part_category", part_id_or_mpn=part_b, category_id_or_name=foreign
        )

    assert "category.not_found" in error
    assert db.get(Part, uuid.UUID(part_b)).category_id is None


async def test_set_part_category_refuses_a_cross_workspace_part(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    part_a = create_part(a, "A part", mpn="AWS-1")
    _category(b, "B cat")
    token_b = _mint(b)["token"]

    async with mcp_session(token_b) as s:
        error = await call_error(
            s, "set_part_category", part_id_or_mpn=part_a, category_id_or_name="B cat"
        )

    assert "part.not_found" in error
    assert db.get(Part, uuid.UUID(part_a)).category_id is None


# ---------------------------------------------------------------------------
# set_part_specs
# ---------------------------------------------------------------------------


async def test_set_part_specs_writes_manual_rows(authed_client, full_token, db):
    part_id = create_part(authed_client, "Resistor", mpn="SPEC-1")

    async with mcp_session(full_token) as s:
        out = await call(
            s,
            "set_part_specs",
            part_id_or_mpn="SPEC-1",
            specs={"resistance": "10k", "tolerance": "1%"},
        )

    assert sorted(out["created"]) == ["resistance", "tolerance"]
    rows = _fields(db, part_id)
    assert rows["resistance"].value == "10k"
    assert rows["resistance"].source == "manual"
    assert rows["tolerance"].value == "1%"


async def test_set_part_specs_updates_an_existing_manual_row(
    authed_client, full_token, db
):
    part_id = create_part(authed_client, "Resistor", mpn="SPEC-2")
    async with mcp_session(full_token) as s:
        await call(s, "set_part_specs", part_id_or_mpn=part_id, specs={"power": "0.1W"})
        out = await call(
            s, "set_part_specs", part_id_or_mpn=part_id, specs={"power": "0.25W"}
        )

    assert out["updated"] == ["power"]
    assert out.get("created") is None
    assert _fields(db, part_id)["power"].value == "0.25W"
    assert db.query(CustomField).filter(CustomField.key == "power").count() == 1


async def test_set_part_specs_leaves_provider_rows_alone(authed_client, full_token, db):
    """The database, not the tool's own report, decides this one."""
    part_id = create_part(authed_client, "Imported", mpn="SPEC-3")
    _seed_provider_field(db, authed_client, part_id, "Resistance", "10 kOhms")

    async with mcp_session(full_token) as s:
        out = await call(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"Resistance": "1 kOhms", "tolerance": "5%"},
        )

    assert out["skipped_provider_owned"] == ["Resistance"]
    assert out["created"] == ["tolerance"]
    row = _fields(db, part_id)["Resistance"]
    assert row.value == "10 kOhms"
    assert row.source == "provider"


async def test_set_part_specs_updates_an_override_without_moving_its_source(
    authed_client, full_token, db
):
    """An `override` is a row a person already took ownership of.

    Writing to it changes the value and nothing else. Nothing on this
    surface moves a row between sources, so the saved upstream value
    survives and "restore the provider value" in the UI still works
    after an agent has been through.
    """
    part_id = create_part(authed_client, "Overridden", mpn="SPEC-10")
    _seed_provider_field(db, authed_client, part_id, "Tolerance", "1 %")
    row = _fields(db, part_id)["Tolerance"]
    row.source = "override"
    row.original_value = "1 %"
    row.value = "1%"
    db.flush()

    async with mcp_session(full_token) as s:
        out = await call(
            s, "set_part_specs", part_id_or_mpn=part_id, specs={"Tolerance": "5%"}
        )

    assert out["updated"] == ["Tolerance"]
    after = _fields(db, part_id)["Tolerance"]
    assert after.value == "5%"
    assert after.source == "override"
    assert after.original_value == "1 %"


async def test_set_part_specs_refuses_a_key_with_surrounding_whitespace(
    authed_client, full_token, db
):
    """`"Tolerance "` is not a second field, it is a typo.

    Accepting it wrote a new manual row beside the provider's
    `"Tolerance"`, so the part carried two spellings of one
    specification and the tool's own provider-skip never fired.
    """
    part_id = create_part(authed_client, "Padded", mpn="SPEC-11")
    _seed_provider_field(db, authed_client, part_id, "Tolerance", "1 %")

    async with mcp_session(full_token) as s:
        error = await call_error(
            s, "set_part_specs", part_id_or_mpn=part_id, specs={"Tolerance ": "5%"}
        )

    assert "custom_field.key_whitespace" in error
    assert set(_fields(db, part_id)) == {"Tolerance"}
    assert _fields(db, part_id)["Tolerance"].value == "1 %"


async def test_a_padded_reserved_key_is_still_reserved(authed_client, full_token, db):
    """The whitespace check is what closes the reserved-key bypass.

    `is_provider_reserved_custom_field_key` compares the key exactly, so
    `"image_url "` was not reserved and landed as an ordinary spec —
    a provider-owned name, written by an agent, one space away from the
    real one.
    """
    part_id = create_part(authed_client, "Padded reserved", mpn="SPEC-12")

    async with mcp_session(full_token) as s:
        error = await call_error(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"image_url ": "http://evil/x.png"},
        )

    assert "custom_field" in error
    assert _fields(db, part_id) == {}


async def test_set_part_specs_refuses_a_provider_namespaced_key(
    authed_client, full_token, db
):
    """`mouser:` and `digikey:` belong to a secondary provider's refresh.

    A row written there would be deleted by that provider's next
    "remove what is absent from my payload" pass, so writing one is a
    silent data-loss bug rather than a permission question (ADR-0031).
    """
    part_id = create_part(authed_client, "Namespaced", mpn="SPEC-13")

    async with mcp_session(full_token) as s:
        for key in ("mouser:Resistance", "digikey:Resistance"):
            error = await call_error(
                s, "set_part_specs", part_id_or_mpn=part_id, specs={key: "10k"}
            )
            assert "custom_field.reserved_key" in error, key

    assert _fields(db, part_id) == {}


async def test_set_part_specs_caps_the_key_length(authed_client, full_token):
    """`custom_fields.key` is varchar(256); 257 is a refusal, not a DataError."""
    part_id = create_part(authed_client, "Long key", mpn="SPEC-14")

    async with mcp_session(full_token) as s:
        error = await call_error(
            s, "set_part_specs", part_id_or_mpn=part_id, specs={"k" * 257: "v"}
        )

    assert "custom_field.too_long" in error


async def test_replace_missing_never_deletes_an_override(
    authed_client, full_token, db
):
    """An override is a person's decision; `replace_missing` may not undo it.

    The row records that someone looked at a provider value and replaced
    it, and `original_value` is the only copy of what upstream said.
    Deleting it on an agent's say-so throws both away, and the next
    provider refresh would quietly restore the upstream value as if the
    person had never disagreed. Overrides stay updatable — see
    `test_set_part_specs_updates_an_override_without_moving_its_source`
    — they are only undeletable.
    """
    part_id = create_part(authed_client, "Overridden", mpn="SPEC-15")
    _seed_provider_field(db, authed_client, part_id, "Tolerance", "1 %")
    row = _fields(db, part_id)["Tolerance"]
    row.source = "override"
    row.original_value = "1 %"
    row.value = "5%"
    db.flush()

    async with mcp_session(full_token) as s:
        await call(
            s, "set_part_specs", part_id_or_mpn=part_id, specs={"power": "0.1W"}
        )
        out = await call(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"package": "0402"},
            replace_missing=True,
        )

    # The manual row goes; the override does not.
    assert out["removed"] == ["power"]
    after = _fields(db, part_id)
    assert set(after) == {"Tolerance", "package"}
    assert after["Tolerance"].source == "override"
    assert after["Tolerance"].value == "5%"
    assert after["Tolerance"].original_value == "1 %"


async def test_set_part_specs_refuses_a_reserved_key(authed_client, full_token, db):
    part_id = create_part(authed_client, "Imported", mpn="SPEC-4")

    async with mcp_session(full_token) as s:
        error = await call_error(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"tolerance": "5%", "image_url": "http://evil/x.png"},
        )

    assert "custom_field.reserved_key" in error
    assert "image_url" in error
    # The whole call is refused, so the innocent key did not land either.
    assert _fields(db, part_id) == {}


async def test_set_part_specs_caps_the_number_of_keys(authed_client, full_token):
    part_id = create_part(authed_client, "Too much", mpn="SPEC-5")
    specs = {f"k{i}": "v" for i in range(51)}

    async with mcp_session(full_token) as s:
        error = await call_error(
            s, "set_part_specs", part_id_or_mpn=part_id, specs=specs
        )

    assert "custom_field.too_many" in error


async def test_set_part_specs_caps_the_value_length(authed_client, full_token):
    part_id = create_part(authed_client, "Too long", mpn="SPEC-6")

    async with mcp_session(full_token) as s:
        error = await call_error(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"notes": "x" * 1025},
        )

    assert "custom_field.too_long" in error


async def test_set_part_specs_replace_missing_drops_only_manual_rows(
    authed_client, full_token, db
):
    part_id = create_part(authed_client, "Imported", mpn="SPEC-7")
    _seed_provider_field(db, authed_client, part_id, "Resistance", "10 kOhms")

    async with mcp_session(full_token) as s:
        await call(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"tolerance": "5%", "power": "0.1W"},
        )
        out = await call(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"tolerance": "1%"},
            replace_missing=True,
        )

    assert out["removed"] == ["power"]
    rows = _fields(db, part_id)
    assert set(rows) == {"tolerance", "Resistance"}
    assert rows["tolerance"].value == "1%"
    assert rows["Resistance"].source == "provider"


async def test_set_part_specs_audit_names_keys_and_no_values(
    authed_client, full_token, db
):
    """Values can be anything a vendor page contained; the trail records
    which fields moved, not what they said."""
    part_id = create_part(authed_client, "Audited specs", mpn="SPEC-8")

    async with mcp_session(full_token) as s:
        await call(
            s,
            "set_part_specs",
            part_id_or_mpn=part_id,
            specs={"tolerance": "1%", "power": "0.1W"},
        )

    rows = _audit_rows(db, "part.specs_updated")
    assert len(rows) == 1
    assert rows[0].target_type == "part"
    assert rows[0].target_ids == [uuid.UUID(part_id)]
    assert rows[0].comment == "keys=power,tolerance"
    assert "0.1W" not in rows[0].comment


async def test_set_part_specs_is_refused_to_a_read_only_token(
    authed_client, readonly_token, db
):
    part_id = create_part(authed_client, "Locked", mpn="SPEC-9")
    async with mcp_session(readonly_token) as s:
        error = await call_error(
            s, "set_part_specs", part_id_or_mpn=part_id, specs={"tolerance": "1%"}
        )
    assert "auth.token_read_only" in error
    assert _fields(db, part_id) == {}


async def test_set_part_specs_refuses_a_cross_workspace_part(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    part_a = create_part(a, "A part", mpn="ASPEC-1")
    token_b = _mint(b)["token"]

    async with mcp_session(token_b) as s:
        error = await call_error(
            s, "set_part_specs", part_id_or_mpn=part_a, specs={"tolerance": "1%"}
        )

    assert "part.not_found" in error
    assert _fields(db, part_a) == {}


async def test_set_part_specs_restores_a_retired_row(authed_client, full_token, db):
    """`uq_cf_unique` has no partial WHERE, so a key the spec reconcile
    archived (a customs code, a `-` placeholder) still owns its slot. An
    agent writing that key must get its row back, not a success report for
    a row no reader returns (ADR-0034)."""
    part_id = create_part(authed_client, "Imported", mpn="SPEC-ARCHIVED")
    _seed_provider_field(db, authed_client, part_id, "ECCN", "EAR99")
    from app.core.time import utcnow

    row = _fields(db, part_id)["ECCN"]
    row.archived_at = utcnow()
    row.source = "manual"
    db.flush()

    async with mcp_session(full_token) as s:
        out = await call(
            s, "set_part_specs", part_id_or_mpn=part_id, specs={"ECCN": "mine"}
        )

    assert out["updated"] == ["ECCN"]
    back = _fields(db, part_id)["ECCN"]
    assert back.value == "mine"
    assert back.archived_at is None
    assert db.query(CustomField).filter(CustomField.key == "ECCN").count() == 1
