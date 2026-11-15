"""The `model-placement-fit` operator job: backfill link placements."""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import settings
from app.domain.audit.models import AuditLog
from app.domain.eda.model_placement_job import AUDIT_ACTION, run_model_placement_fit
from app.domain.eda.models import EdaFootprintModel
from app.main import app
from tests._factories import signup_user

pytest.importorskip("cascadio")

_CUBE_STEP = (
    pathlib.Path(__file__).parent / "fixtures" / "eda" / "preview3d" / "cube.step"
).read_bytes()
_WRL_BYTES = b"#VRML V2.0 utf8\nShape { geometry Box { size 1 1 1 } }\n"


def _footprint_text(name: str, model: str | None = None) -> str:
    node = f'  (model "{model}" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)))\n' if model else ""
    return (
        f'(footprint "{name}" (layer "F.Cu") (attr smd)\n'
        f'  (pad "1" smd rect (at -2 0) (size 1 2) (layers "F.Cu"))\n'
        f'  (pad "2" smd rect (at 2 0) (size 1 2) (layers "F.Cu"))\n'
        f"{node})\n"
    )


class Tenant:
    def __init__(self, db) -> None:
        self.db = db
        self.session = TestClient(app)
        signed_up = signup_user(self.session, email=f"u-{uuid.uuid4().hex[:8]}@example.com")
        self.workspace_id = uuid.UUID(signed_up.json()["data"]["workspace_id"])

    def _post(self, path: str, filename: str, raw: bytes, data: dict | None = None) -> dict:
        r = self.session.post(
            path, files={"file": (filename, raw, "application/octet-stream")}, data=data or {}
        )
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    def footprint(self, name: str, model: str | None = None) -> dict:
        return self._post(
            "/api/eda/footprints", f"{name}.kicad_mod", _footprint_text(name, model).encode()
        )

    def datafile(self, name: str, raw: bytes) -> dict:
        return self._post("/api/eda/datafiles", name, raw, {"name": name})

    def link(self, footprint_id: str, datafile_id: str, position: int = 0) -> None:
        r = self.session.post(
            f"/api/eda/footprints/{footprint_id}/models",
            json={"datafile_id": datafile_id, "position": position},
        )
        assert r.status_code == 200, r.text

    def forget_placements(self) -> None:
        """What a link made before migration 0084 looks like."""
        self.db.execute(
            update(EdaFootprintModel)
            .where(EdaFootprintModel.workspace_id == self.workspace_id)
            .values(placement=None)
        )
        self.db.flush()

    def placements(self) -> list:
        return list(
            self.db.execute(
                select(EdaFootprintModel.placement)
                .where(EdaFootprintModel.workspace_id == self.workspace_id)
                .order_by(EdaFootprintModel.position)
            ).scalars()
        )


@pytest.fixture
def tenant(db) -> Tenant:
    t = Tenant(db)
    fp = t.footprint("CUBE_FP")
    step = t.datafile("cube.step", _CUBE_STEP)
    wrl = t.datafile("cube.wrl", _WRL_BYTES)
    t.link(fp["id"], step["id"], 0)
    t.link(fp["id"], wrl["id"], 1)
    t.forget_placements()
    return t


def _run(t: Tenant, *, apply: bool) -> tuple[int, str]:
    out = io.StringIO()
    affected = run_model_placement_fit(
        t.db, apply=apply, workspace_id=t.workspace_id, stream=out
    )
    return affected, out.getvalue()


def _clear_preview_cache(t: Tenant) -> str:
    cache = os.path.join(settings().UPLOAD_DIR, "eda", str(t.workspace_id), "preview")
    shutil.rmtree(cache, ignore_errors=True)
    return cache


def test_dry_run_reports_the_fit_and_writes_no_cache_file(tenant: Tenant):
    cache = _clear_preview_cache(tenant)

    affected, report = _run(tenant, apply=False)

    assert affected == 2
    rows = report.strip().splitlines()
    assert rows[0].startswith("workspace_id,footprint_id")
    assert sum(",would_set," in row for row in rows) == 2
    assert "offset=0.0,0.0,5.0" in report
    assert not os.path.exists(cache) or not os.listdir(cache)


def test_apply_sets_both_formats_and_audits(tenant: Tenant):
    affected, _ = _run(tenant, apply=True)

    assert affected == 2
    step, wrl = tenant.placements()
    assert step["offset"] == [0.0, 0.0, 5.0]
    assert wrl == step  # the WRL copies its STEP sibling
    audit = tenant.db.execute(
        select(AuditLog)
        .where(AuditLog.workspace_id == tenant.workspace_id)
        .where(AuditLog.action == AUDIT_ACTION)
    ).scalars().all()
    assert len(audit) == 1


def test_a_second_run_proposes_nothing(tenant: Tenant):
    _run(tenant, apply=True)
    affected, report = _run(tenant, apply=True)
    assert affected == 0
    assert report.strip().count("\n") == 0  # header only


def test_a_model_the_footprint_already_places_is_skipped(db):
    t = Tenant(db)
    fp = t.footprint("PLACED_FP", model="${STOCKMGR_3D}/cube.step")
    step = t.datafile("cube.step", _CUBE_STEP)
    t.link(fp["id"], step["id"])
    t.forget_placements()

    affected, report = _run(t, apply=True)

    assert affected == 0
    assert "footprint places it" in report
    assert t.placements() == [None]


def test_an_unknown_workspace_is_refused(db):
    with pytest.raises(LookupError):
        run_model_placement_fit(db, workspace_id=uuid.uuid4(), stream=io.StringIO())


def test_another_workspaces_links_are_not_touched(db, tenant: Tenant):
    other = Tenant(db)
    affected, _ = _run(other, apply=True)
    assert affected == 0
    assert tenant.placements() == [None, None]


def test_a_wrl_linked_before_its_step_gets_the_steps_placement(db):
    """Review MEDIUM-1: linked WRL-first, the WRL found no sibling to
    copy; the package then appended it at the identity and skipped the
    correctly placed STEP as the same model."""
    t = Tenant(db)
    fp = t.footprint("WRL_FIRST")
    wrl = t.datafile("cube.wrl", _WRL_BYTES)
    step = t.datafile("cube.step", _CUBE_STEP)
    t.link(fp["id"], wrl["id"], 0)
    t.link(fp["id"], step["id"], 1)

    wrl_placement, step_placement = t.placements()
    assert step_placement is not None
    assert wrl_placement == step_placement


def test_apply_touches_the_footprint_so_installed_copies_update(tenant: Tenant):
    """Review HIGH-1: the package version comes from footprint
    timestamps; a link row has none of its own."""
    from app.domain.eda.models import EdaFootprint

    fp = tenant.db.execute(
        select(EdaFootprint).where(EdaFootprint.workspace_id == tenant.workspace_id)
    ).scalar_one()
    before = fp.updated_at

    _run(tenant, apply=True)

    tenant.db.refresh(fp)
    assert fp.updated_at > before


def test_a_malformed_glb_degrades_to_no_placement(db, monkeypatch):
    """Review MEDIUM-2: anything the fit can't read must not fail a link."""
    from app.domain.eda import model_placement, preview3d

    monkeypatch.setattr(
        preview3d, "read_or_convert", lambda *a, **k: b"glTF" + b"\x00" * 30
    )
    t = Tenant(db)
    fp = t.footprint("ODD")
    step = t.datafile("odd.step", _CUBE_STEP)
    t.link(fp["id"], step["id"])
    assert t.placements() == [None]
    assert model_placement.model_stem("A/B/Odd.STEP") == "odd"
