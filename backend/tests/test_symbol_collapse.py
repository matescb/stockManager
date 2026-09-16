"""The `symbol-collapse` job — plan B4.

A vendor zip mints one schematic symbol per part, and
`kicad_library.py::_symbol_id_str` prefers a hosted symbol over the
category default — so seeding `Device:R` onto *Resistors* changes
nothing for the eighty resistors that already carry one. This job clears
`part_eda.symbol_id` on exactly those rows.

What is pinned here is mostly what the job refuses to touch: a
hand-uploaded symbol, an external ref the user typed, a part whose
category has no default to fall back on, and another workspace's rows.
The happy path is checked end to end through the KiCad document, because
"the column is NULL now" is not the claim — "KiCad draws `Device:R`" is.
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.domain.eda.models import EdaSymbol, PartEda
from app.domain.eda.symbol_collapse import (
    AUDIT_ACTION,
    CSV_COLUMNS,
    collapse_candidates,
    run_symbol_collapse,
)
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import create_part, signup_user


def _symbol_text(name: str) -> str:
    return (
        f'(symbol "{name}" (in_bom yes) (on_board yes)\n'
        f'  (property "Reference" "R" (at 0 0 0))\n'
        f'  (property "Value" "{name}" (at 0 0 0))\n'
        f")\n"
    )


class Tenant:
    """One signed-up workspace, plus the KiCad token client."""

    def __init__(self, db) -> None:
        self.db = db
        self.session = TestClient(app)
        signed_up = signup_user(self.session, email=f"u-{uuid.uuid4().hex[:8]}@example.com")
        self.workspace_id = uuid.UUID(signed_up.json()["data"]["workspace_id"])
        self.ws = db.get(Workspace, self.workspace_id)
        token = self.session.post(
            "/api/tokens", json={"label": f"kicad {uuid.uuid4().hex[:6]}", "read_only": True}
        ).json()["data"]["token"]
        self.kicad = TestClient(app)
        self.kicad.headers["Authorization"] = f"Token {token}"

    def category(self, name: str, **extra) -> dict:
        r = self.session.post("/api/categories", json={"name": name, **extra})
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    def symbol(self, entry: str, *, source: str = "snapeda", category_id=None) -> dict:
        """Upload a symbol, then stamp its provenance.

        `source` is server-controlled on the way in — the upload route
        always writes `manual` — so a vendor-zip row can only be made by
        writing the column, which is what the phase-3 importer does.
        """
        data = {"category_id": category_id} if category_id else {}
        r = self.session.post(
            "/api/eda/symbols",
            files={
                "file": (
                    f"{entry}.kicad_sym",
                    _symbol_text(entry).encode(),
                    "application/octet-stream",
                )
            },
            data=data,
        )
        assert r.status_code in (200, 201), r.text
        row = r.json()["data"]
        if source != "manual":
            symbol = self.db.get(EdaSymbol, uuid.UUID(row["id"]))
            symbol.source = source
            self.db.flush()
        return row

    def configure(self, part_id: str, **body) -> dict:
        r = self.session.put(f"/api/parts/{part_id}/eda", json=body)
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def symbol_id_str(self, part_id: str) -> str | None:
        r = self.kicad.get(f"/kicad-api/v1/parts/{part_id}.json")
        assert r.status_code == 200, r.text
        return r.json().get("symbolIdStr")

    def config(self, part_id: str) -> PartEda:
        return self.db.execute(
            select(PartEda)
            .where(PartEda.part_id == uuid.UUID(part_id))
            .where(PartEda.workspace_id == self.workspace_id)
        ).scalar_one()


@pytest.fixture
def ws(db) -> Tenant:
    return Tenant(db)


@pytest.fixture
def other(db) -> Tenant:
    return Tenant(db)


def _vendor_part(ws: Tenant, *, name: str = "R 10k", entry: str = "R_VENDOR") -> str:
    """A part in a category with a `Device:R` default, wearing a vendor
    symbol — the exact shape the job exists for."""
    category = ws.category("Resistors", default_symbol_ref="Device:R")
    part_id = create_part(ws.session, name=name, category_id=category["id"])
    symbol = ws.symbol(entry, category_id=category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])
    return part_id


def _audit_rows(db) -> list[AuditLog]:
    return list(
        db.execute(select(AuditLog).where(AuditLog.action == AUDIT_ACTION)).scalars()
    )


# ---------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------


def test_the_job_is_not_a_no_op_before_it_runs(ws: Tenant):
    """The hosted symbol outranks the category default, which is the
    whole reason this job exists. If this ever stops being true, B4 is
    dead code."""
    part_id = _vendor_part(ws)

    assert ws.symbol_id_str(part_id) == "PCM_SM_resistors:R_VENDOR"


def test_apply_collapses_the_part_onto_the_category_default(ws: Tenant, db):
    part_id = _vendor_part(ws)

    collapsed = run_symbol_collapse(db, apply=True, stream=io.StringIO())

    assert collapsed == 1
    assert ws.config(part_id).symbol_id is None
    assert ws.symbol_id_str(part_id) == "Device:R"


def test_the_vendor_symbol_row_survives(ws: Tenant, db):
    """Other parts may still point at it, and it is the record of what
    was imported. The job clears a reference, it does not delete
    library content."""
    _vendor_part(ws)

    run_symbol_collapse(db, apply=True, stream=io.StringIO())

    rows = list(
        db.execute(
            select(EdaSymbol).where(EdaSymbol.workspace_id == ws.workspace_id)
        ).scalars()
    )
    assert [row.name for row in rows] == ["R_VENDOR"]
    assert rows[0].archived_at is None


def test_a_second_run_finds_nothing(ws: Tenant, db):
    _vendor_part(ws)

    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 1
    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 0


def test_dry_run_reports_but_writes_nothing(ws: Tenant, db):
    part_id = _vendor_part(ws)
    stream = io.StringIO()

    would_collapse = run_symbol_collapse(db, apply=False, stream=stream)

    assert would_collapse == 1
    db.flush()
    assert ws.config(part_id).symbol_id is not None
    assert ws.symbol_id_str(part_id) == "PCM_SM_resistors:R_VENDOR"
    assert _audit_rows(db) == []

    lines = stream.getvalue().splitlines()
    assert lines[0] == ",".join(CSV_COLUMNS)
    assert len(lines) == 2
    assert "PCM_SM_resistors:R_VENDOR" in lines[1]
    assert "Device:R" in lines[1]


# ---------------------------------------------------------------------
# What it refuses to touch
# ---------------------------------------------------------------------


def test_a_manual_symbol_is_left_alone(ws: Tenant, db):
    category = ws.category("Resistors", default_symbol_ref="Device:R")
    part_id = create_part(ws.session, name="R", category_id=category["id"])
    symbol = ws.symbol("R_MINE", source="manual", category_id=category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])

    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 0
    assert ws.config(part_id).symbol_id == uuid.UUID(symbol["id"])


def test_an_external_ref_is_left_alone(ws: Tenant, db):
    category = ws.category("Resistors", default_symbol_ref="Device:R")
    part_id = create_part(ws.session, name="R", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="MyLib:R_Special")

    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 0
    assert ws.config(part_id).symbol_ref_external == "MyLib:R_Special"


def test_a_category_without_a_default_is_left_alone(ws: Tenant, db):
    """Clearing here would leave the part with no symbol at all — worse
    than a redundant one."""
    category = ws.category("Connectors")
    part_id = create_part(ws.session, name="USB-C", category_id=category["id"])
    symbol = ws.symbol("USB_C_VENDOR", category_id=category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])

    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 0
    assert ws.config(part_id).symbol_id == uuid.UUID(symbol["id"])


def test_a_part_with_no_category_is_left_alone(ws: Tenant, db):
    part_id = create_part(ws.session, name="Odd")
    symbol = ws.symbol("ODD_VENDOR")
    ws.configure(part_id, symbol_id=symbol["id"])

    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 0
    assert ws.config(part_id).symbol_id == uuid.UUID(symbol["id"])


def test_an_archived_part_is_left_alone(ws: Tenant, db):
    part_id = _vendor_part(ws)
    archived = ws.session.post(f"/api/parts/{part_id}/archive")
    assert archived.status_code == 200, archived.text

    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 0


def test_an_archived_symbol_is_not_reported(ws: Tenant, db):
    """`_symbol_id_str` already skips it, so the part is *already*
    rendering the category default — there is nothing to collapse."""
    part_id = _vendor_part(ws)
    symbol_id = ws.config(part_id).symbol_id
    assert ws.session.post(f"/api/eda/symbols/{symbol_id}/archive").status_code == 200

    assert run_symbol_collapse(db, apply=True, stream=io.StringIO()) == 0
    assert ws.symbol_id_str(part_id) == "Device:R"


# ---------------------------------------------------------------------
# Audit + isolation
# ---------------------------------------------------------------------


def test_apply_writes_one_audit_row_per_workspace(ws: Tenant, db):
    part_id = _vendor_part(ws)

    run_symbol_collapse(db, apply=True, stream=io.StringIO())

    rows = _audit_rows(db)
    assert len(rows) == 1
    assert rows[0].workspace_id == ws.workspace_id
    assert rows[0].target_type == "part"
    assert rows[0].target_ids == [uuid.UUID(part_id)]


def test_the_workspace_flag_narrows_to_one_workspace(ws: Tenant, other: Tenant, db):
    mine = _vendor_part(ws)
    theirs = _vendor_part(other)

    collapsed = run_symbol_collapse(
        db, apply=True, workspace_id=ws.workspace_id, stream=io.StringIO()
    )

    assert collapsed == 1
    assert ws.config(mine).symbol_id is None
    assert other.config(theirs).symbol_id is not None
    assert [row.workspace_id for row in _audit_rows(db)] == [ws.workspace_id]


def test_candidates_never_cross_workspaces(ws: Tenant, other: Tenant, db):
    _vendor_part(ws)
    _vendor_part(other)

    candidates = collapse_candidates(db)

    assert {c.workspace_id for c in candidates} == {ws.workspace_id, other.workspace_id}
    for candidate in candidates:
        config = db.execute(
            select(PartEda)
            .where(PartEda.part_id == candidate.part_id)
            .where(PartEda.workspace_id == candidate.workspace_id)
        ).scalar_one()
        symbol = db.get(EdaSymbol, config.symbol_id)
        assert symbol.workspace_id == candidate.workspace_id
