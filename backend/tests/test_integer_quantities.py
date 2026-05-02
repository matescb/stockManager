"""DB-005 / migration 0030 — integer quantity constraints.

Tests:
1. test_project_entry_quantity_rejects_fraction — POST with quantity=0.5 → 422
2. test_bom_import_rejects_fractional_row — CSV with "0.5" → 422
3. test_check_constraint_active — direct DB insert with quantity=-1 raises
   IntegrityError
4. test_project_entry_quantity_accepts_zero_and_positive — smoke for valid values
5. test_order_entry_check_constraints_active — quantity_ordered=-1 raises
   IntegrityError
"""
from __future__ import annotations

import base64
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.domain.parts.models import Part
from app.domain.projects import bom_import as bom
from app.domain.projects.models import Project, ProjectEntry
from app.domain.orders.models import Order, OrderEntry
from app.domain.projects.schemas import (
    BomImportCommitIn,
    BomMappingField,
)
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember
from app.main import app
from tests._factories import signup_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_ws(db):
    user = User(email=f"u-{uuid.uuid4().hex[:6]}@x.com", name="t", password_hash="x")
    db.add(user)
    db.flush()
    ws = Workspace(name="W", kind="organization", owner_user_id=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, status="active"))
    db.commit()
    return ws, user


# ---------------------------------------------------------------------------
# 1. API-level rejection of fractional quantity in BomEntryIn
# ---------------------------------------------------------------------------

def test_project_entry_quantity_rejects_fraction():
    """POST /api/projects/{id}/entries with quantity=0.5 must return 422."""
    c = TestClient(app)
    signup_user(c)
    r = c.post("/api/projects", json={"name": "FracTest"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]

    r2 = c.post(f"/api/projects/{pid}/entries", json={
        "entry_type": "non_part",
        "name": "resistor",
        "quantity": 0.5,
    })
    assert r2.status_code == 422, r2.text


# ---------------------------------------------------------------------------
# 2. BOM importer rejects fractional quantity rows
# ---------------------------------------------------------------------------

def test_bom_import_rejects_fractional_row(db):
    ws, user = _setup_ws(db)
    proj = Project(workspace_id=ws.id, name="FracBOM", created_by=user.id, updated_by=user.id)
    db.add(proj)
    db.commit()

    csv_text = "qty,mpn\n0.5,RC0402\n"
    text_b64 = base64.b64encode(csv_text.encode()).decode()
    payload = BomImportCommitIn(
        text_b64=text_b64,
        separator=",",
        encoding="utf-8",
        has_header=True,
        mapping=[
            BomMappingField(column_index=0, target="quantity"),
            BomMappingField(column_index=1, target="mpn"),
        ],
        designator_separator=",",
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        bom.commit(db, workspace_id=ws.id, user_id=user.id, project=proj, payload=payload)
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["code"] == "bom.fractional_quantity"
    assert 1 in detail["fractional_rows"]


# ---------------------------------------------------------------------------
# 3. CHECK constraint active — direct DB insert with quantity=-1
# ---------------------------------------------------------------------------

def test_check_constraint_active(db):
    """A direct DB insert with quantity=-1 must raise IntegrityError."""
    ws, user = _setup_ws(db)
    proj = Project(workspace_id=ws.id, name="CKTest", created_by=user.id, updated_by=user.id)
    db.add(proj)
    db.flush()

    entry = ProjectEntry(
        workspace_id=ws.id,
        project_id=proj.id,
        entry_type="non_part",
        name="widget",
        quantity=-1,  # violates ck_project_entries_quantity_nonneg
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(entry)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------------------
# 4. Smoke — valid zero and positive integer quantities are accepted
# ---------------------------------------------------------------------------

def test_project_entry_quantity_accepts_zero_and_positive():
    """quantity=0 and quantity=10 should both be accepted (API level)."""
    c = TestClient(app)
    signup_user(c)
    r = c.post("/api/projects", json={"name": "ValidQtyTest"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]

    for qty in (0, 1, 10):
        r2 = c.post(f"/api/projects/{pid}/entries", json={
            "entry_type": "non_part",
            "name": f"item-{qty}",
            "quantity": qty,
        })
        assert r2.status_code in (200, 201), f"qty={qty}: {r2.text}"


# ---------------------------------------------------------------------------
# 5. order_entries CHECK constraints active
# ---------------------------------------------------------------------------

def test_order_entry_check_constraints_active(db):
    """A direct DB insert with quantity_ordered=-1 must raise IntegrityError."""
    ws, user = _setup_ws(db)
    order = Order(
        workspace_id=ws.id,
        name="PO-TEST",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(order)
    db.flush()

    entry = OrderEntry(
        workspace_id=ws.id,
        order_id=order.id,
        name="widget",
        quantity_ordered=-1,  # violates ck_order_entries_qty_ordered_nonneg
        quantity_received=0,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(entry)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
