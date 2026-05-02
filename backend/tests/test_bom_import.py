from __future__ import annotations

import base64
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.domain.parts.models import Part
from app.domain.projects import bom_import as bom
from app.domain.projects.models import Project, ProjectEntry
from app.domain.projects.schemas import (
    BomImportCommitIn,
    BomImportPreviewIn,
    BomMappingField,
)
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember


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


def test_bom_preview_detects_separator_and_header(db):
    ws, user = _setup_ws(db)
    csv_text = "qty,mpn,designators,dnp\n10,RC0402JR-070R,R1 R2 R3 R4 R5 R6 R7 R8 R9 R10,no\n2,UNKNOWN,U1 U2,no\n"
    payload = BomImportPreviewIn(text_b64=base64.b64encode(csv_text.encode()).decode())
    out = bom.preview(payload)
    assert out.detected_separator == ","
    assert out.has_header is True
    assert out.headers == ["qty", "mpn", "designators", "dnp"]
    assert len(out.rows) == 2


def test_bom_commit_matches_by_mpn_and_keeps_unmatched(db):
    ws, user = _setup_ws(db)
    p = Part(workspace_id=ws.id, name="0R 0402", mpn="RC0402JR-070R", manufacturer="Yageo", created_by=user.id, updated_by=user.id)
    db.add(p)
    proj = Project(workspace_id=ws.id, name="Widget", created_by=user.id, updated_by=user.id)
    db.add(proj)
    db.commit()

    csv_text = "qty;mpn;designators;dnp\n10;RC0402JR-070R;R1,R2,R3,R4,R5,R6,R7,R8,R9,R10;no\n2;UNKNOWN-MPN;U1,U2;no\n"
    text_b64 = base64.b64encode(csv_text.encode()).decode()
    payload = BomImportCommitIn(
        text_b64=text_b64,
        separator=";",
        encoding="utf-8",
        has_header=True,
        mapping=[
            BomMappingField(column_index=0, target="quantity"),
            BomMappingField(column_index=1, target="mpn"),
            BomMappingField(column_index=2, target="designators"),
            BomMappingField(column_index=3, target="dnp"),
        ],
        designator_separator=",",
    )
    res = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=proj, payload=payload)
    db.commit()
    assert res.inserted == 2
    assert res.matched == 1
    assert res.unmatched == 1

    entries = db.query(ProjectEntry).filter(ProjectEntry.project_id == proj.id).order_by(ProjectEntry.order_index).all()
    assert entries[0].entry_type == "part"
    assert entries[0].part_id == p.id
    assert entries[0].quantity == 10
    assert "R1" in (entries[0].designators or [])
    assert entries[1].entry_type == "unmatched"
    assert entries[1].part_id is None


# ---------------------------------------------------------------------------
# SEC2-007 / BE2-006 — size + row caps on the BOM importer
# ---------------------------------------------------------------------------


def test_bom_preview_b64_schema_caps_input():
    """The pydantic schema's max_length=6_000_000 is the first line of
    defence — a 7 MB base64 string must 422 before hitting the decoder.
    The 6 MB ceiling is deliberate: it must allow a payload large enough
    for the runtime 4 MB raw cap to fire (see the next test); a 5 MB
    Field cap would foreclose the runtime path."""
    too_big_b64 = "A" * 6_000_001
    with pytest.raises(ValidationError):
        BomImportPreviewIn(text_b64=too_big_b64)


def test_bom_preview_decoded_size_caps_input():
    """A payload that fits under the schema cap but decodes to > 4 MB
    must raise 413 from the helper."""
    raw = b"x" * 4_000_001
    b64 = base64.b64encode(raw).decode()
    payload = BomImportPreviewIn(text_b64=b64)
    with pytest.raises(HTTPException) as exc:
        bom.preview(payload)
    assert exc.value.status_code == 413


def test_bom_preview_row_count_caps_input():
    """A pathological CSV with > 10k rows must 422."""
    rows = "\n".join(f"{i}," for i in range(10_001))
    b64 = base64.b64encode(rows.encode()).decode()
    payload = BomImportPreviewIn(text_b64=b64, separator=",")
    with pytest.raises(HTTPException) as exc:
        bom.preview(payload)
    assert exc.value.status_code == 422
