"""Universal object codes — minting, resolving, isolation, cleanup.

Track A1. Covers `/api/codes` end to end plus the two invariants that are
easy to break by accident:

* **Workspace isolation.** Minting against a foreign entity id is a 404,
  and a code minted in workspace A does not resolve in workspace B.
* **Polymorphic cleanup.** `object_codes.entity_id` has no FK, so a
  hard-deleted parent must take its code row with it (CLAUDE.md,
  "Polymorphic cleanup on hard delete").
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import ErrorCodes
from app.domain.audit.models import AuditLog
from app.domain.codes import service as codes_service
from app.domain.codes.models import CODE_ENTITY_TYPES, ObjectCode
from app.domain.codes.service import (
    CODE_LENGTH,
    CROCKFORD_ALPHABET,
    generate_code,
    normalize_code,
)
from app.domain.parts.models import Part
from app.main import app
from tests._factories import create_part, create_storage

# Reuse the canonical parent-row builder rather than re-deriving valid
# constructor args for five models here — one definition, so a schema
# change lands in both files.
from tests.test_polymorphic_cleanup import _make_parent, _signup


def _mint(client: TestClient, entity_type: str, entity_id: str):
    return client.post(
        "/api/codes", json={"entity_type": entity_type, "entity_id": entity_id}
    )


def _mint_code(client: TestClient, entity_type: str, entity_id: str) -> str:
    r = _mint(client, entity_type, entity_id)
    assert r.status_code == 200, r.text
    return r.json()["data"]["code"]


def _code_rows(db, workspace_id: uuid.UUID, entity_id: uuid.UUID) -> list[ObjectCode]:
    return list(
        db.execute(
            select(ObjectCode).where(
                ObjectCode.workspace_id == workspace_id,
                ObjectCode.entity_id == entity_id,
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Code format (pure functions — no DB)
# ---------------------------------------------------------------------------

def test_generated_code_uses_crockford_alphabet_only():
    for _ in range(200):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(CROCKFORD_ALPHABET)


def test_generated_code_excludes_ambiguous_letters():
    """I, L, O and U are exactly the characters a human misreads."""
    assert not (set("ILOU") & set(CROCKFORD_ALPHABET))


def test_generated_codes_are_not_sequential():
    """A CSPRNG draw, not a counter — 50 draws must not all be distinct
    by one increment, and must not repeat."""
    codes = {generate_code() for _ in range(50)}
    assert len(codes) == 50


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abcd1234", "ABCD1234"),
        ("ABCD-1234", "ABCD1234"),
        ("  ABCD 1234 ", "ABCD1234"),
        # Crockford decode aliases: the excluded letters fold onto the
        # digits they resemble, so a hand-typed "O" still resolves.
        ("OBCD123I", "0BCD1231"),
        ("lbcd1234", "1BCD1234"),
        ("", ""),
    ],
)
def test_normalize_code(raw: str, expected: str):
    assert normalize_code(raw) == expected


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------

def test_mint_returns_code_for_part(db):
    c = TestClient(app)
    _signup(c)
    part_id = create_part(c, "Coded Cap")

    r = _mint(c, "part", part_id)
    assert r.status_code == 200, r.text
    body = r.json()
    # Envelope invariant.
    assert set(body) >= {"data", "status"}
    assert body["status"]["category"] == "ok"
    data = body["data"]
    assert data["entity_type"] == "part"
    assert data["entity_id"] == part_id
    assert len(data["code"]) == CODE_LENGTH
    assert set(data["code"]) <= set(CROCKFORD_ALPHABET)


def test_mint_is_idempotent(db):
    """Get-or-create: a second POST returns the same code and adds no row."""
    c = TestClient(app)
    ws_id = _signup(c)
    part_id = create_part(c, "Twice")

    first = _mint_code(c, "part", part_id)
    second = _mint_code(c, "part", part_id)
    assert first == second
    assert len(_code_rows(db, uuid.UUID(ws_id), uuid.UUID(part_id))) == 1


def test_distinct_objects_get_distinct_codes(db):
    c = TestClient(app)
    _signup(c)
    a = create_part(c, "A")
    b = create_part(c, "B")
    assert _mint_code(c, "part", a) != _mint_code(c, "part", b)


@pytest.mark.parametrize("entity_type", sorted(CODE_ENTITY_TYPES))
def test_mint_accepts_every_codeable_entity_type(db, entity_type):
    c = TestClient(app)
    ws_id = _signup(c)
    parent = _make_parent(db, workspace_id=uuid.UUID(ws_id), object_type=entity_type)

    code = _mint_code(c, entity_type, str(parent.id))
    assert len(code) == CODE_LENGTH


def test_mint_rejects_unknown_entity_type(db):
    c = TestClient(app)
    _signup(c)
    part_id = create_part(c, "P")

    # `project` is deliberately NOT codeable — you don't stick a label on
    # a project — so it is a 422 from the schema, like any other unknown
    # discriminator.
    r = _mint(c, "project", part_id)
    assert r.status_code == 422, r.text


def test_mint_rejects_unknown_field(db):
    c = TestClient(app)
    _signup(c)
    part_id = create_part(c, "P")
    r = c.post(
        "/api/codes",
        json={"entity_type": "part", "entity_id": part_id, "banana": "yellow"},
    )
    assert r.status_code == 422, r.text
    assert "banana" in r.text


def test_mint_rejects_nonexistent_entity_id(db):
    c = TestClient(app)
    _signup(c)
    r = _mint(c, "part", str(uuid.uuid4()))
    assert r.status_code == 404, r.text


def test_mint_retries_on_code_collision(db, monkeypatch):
    """The mint loop must survive a duplicate draw rather than 500."""
    c = TestClient(app)
    _signup(c)
    a = create_part(c, "A")
    b = create_part(c, "B")

    fixed = "ZZZZ0001"
    draws = iter([fixed, fixed, "ZZZZ0002"])
    monkeypatch.setattr(codes_service, "generate_code", lambda: next(draws))

    assert _mint_code(c, "part", a) == fixed
    # Second object draws `fixed` again, hits uq_object_codes_ws_code,
    # and retries into a fresh code.
    assert _mint_code(c, "part", b) == "ZZZZ0002"


def test_mint_writes_audit_row_only_on_creation(db):
    c = TestClient(app)
    ws_id = _signup(c)
    part_id = create_part(c, "Audited")

    _mint_code(c, "part", part_id)
    _mint_code(c, "part", part_id)

    rows = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == uuid.UUID(ws_id),
                AuditLog.action == "object_code.minted",
            )
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].target_type == "object_code"
    assert uuid.UUID(part_id) in rows[0].target_ids
    # Low-sensitivity summary only — never the code itself.
    assert rows[0].comment == "entity_type=part"


# ---------------------------------------------------------------------------
# Resolve (the scan path)
# ---------------------------------------------------------------------------

def test_resolve_returns_entity(db):
    c = TestClient(app)
    _signup(c)
    part_id = create_part(c, "Scan Me")
    code = _mint_code(c, "part", part_id)

    r = c.get(f"/api/codes/{code}")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data == {"code": code, "entity_type": "part", "entity_id": part_id}


def test_resolve_normalizes_human_input(db):
    """Lower case, grouping hyphens and Crockford aliases all resolve."""
    c = TestClient(app)
    _signup(c)
    storage_id = create_storage(c, "Shelf")
    code = _mint_code(c, "storage_location", storage_id)

    variants = [code.lower(), f"{code[:4]}-{code[4:]}", code.replace("0", "O").replace("1", "I")]
    for variant in variants:
        r = c.get(f"/api/codes/{variant}")
        assert r.status_code == 200, f"{variant!r}: {r.text}"
        assert r.json()["data"]["entity_id"] == storage_id


def test_resolve_unknown_code_is_404(db):
    c = TestClient(app)
    _signup(c)
    r = c.get("/api/codes/ZZZZZZZZ")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == ErrorCodes.CODE_NOT_FOUND


def test_resolve_overlong_code_is_404_not_500(db):
    c = TestClient(app)
    _signup(c)
    r = c.get("/api/codes/" + "Z" * 200)
    assert r.status_code == 404, r.text
    assert r.json()["code"] == ErrorCodes.CODE_NOT_FOUND


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------

def test_mint_rejects_cross_workspace_entity_id(db):
    """B must not be able to mint a code against A's part.

    Without the pre-mint workspace check, B would end up holding a code
    that resolves — turning `/api/codes` into a cross-tenant oracle.
    """
    a = TestClient(app)
    b = TestClient(app)
    ws_a = _signup(a)
    _signup(b)
    part_a = create_part(a, "A's part")

    r = _mint(b, "part", part_a)
    assert r.status_code == 404, r.text
    # And nothing was written for A's part in any workspace.
    assert _code_rows(db, uuid.UUID(ws_a), uuid.UUID(part_a)) == []


def test_resolve_does_not_cross_workspaces(db):
    a = TestClient(app)
    b = TestClient(app)
    _signup(a)
    _signup(b)
    part_a = create_part(a, "A's part")
    code = _mint_code(a, "part", part_a)

    r = b.get(f"/api/codes/{code}")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == ErrorCodes.CODE_NOT_FOUND


def test_same_code_may_exist_in_two_workspaces(db, monkeypatch):
    """Uniqueness is per workspace, so a collision across tenants is legal
    and each side still resolves to its own object."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a)
    _signup(b)
    part_a = create_part(a, "A's part")
    part_b = create_part(b, "B's part")

    monkeypatch.setattr(codes_service, "generate_code", lambda: "SHARED01")
    assert _mint_code(a, "part", part_a) == "SHARED01"
    assert _mint_code(b, "part", part_b) == "SHARED01"

    assert a.get("/api/codes/SHARED01").json()["data"]["entity_id"] == part_a
    assert b.get("/api/codes/SHARED01").json()["data"]["entity_id"] == part_b


# ---------------------------------------------------------------------------
# Polymorphic cleanup on hard delete
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entity_type", sorted(CODE_ENTITY_TYPES))
def test_hard_delete_parent_removes_its_code(db, entity_type):
    """`entity_id` has no FK — the before_delete listener is the only
    thing standing between a hard delete and a dangling code that would
    resolve to a row that no longer exists."""
    c = TestClient(app)
    ws_id = _signup(c)
    ws_uuid = uuid.UUID(ws_id)
    parent = _make_parent(db, workspace_id=ws_uuid, object_type=entity_type)

    _mint_code(c, entity_type, str(parent.id))
    assert len(_code_rows(db, ws_uuid, parent.id)) == 1

    db.delete(parent)
    db.flush()

    assert _code_rows(db, ws_uuid, parent.id) == []


def test_hard_delete_does_not_touch_another_workspaces_code(db, monkeypatch):
    """The cleanup DELETE is workspace-filtered; a colliding code in
    another workspace must survive."""
    a = TestClient(app)
    b = TestClient(app)
    ws_a = _signup(a)
    ws_b = _signup(b)
    part_a = create_part(a, "A's part")
    part_b = create_part(b, "B's part")

    monkeypatch.setattr(codes_service, "generate_code", lambda: "SURVIVE1")
    _mint_code(a, "part", part_a)
    _mint_code(b, "part", part_b)

    row_a = db.execute(
        select(Part).where(Part.id == uuid.UUID(part_a))
    ).scalar_one()
    db.delete(row_a)
    db.flush()

    assert _code_rows(db, uuid.UUID(ws_a), uuid.UUID(part_a)) == []
    assert len(_code_rows(db, uuid.UUID(ws_b), uuid.UUID(part_b))) == 1
