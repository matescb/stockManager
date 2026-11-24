"""`POST /api/projects/{id}/bom/sync` — BOM synchronisation in place (SM-011).

The acceptance criteria, one test each:

1. the same BOM twice — the second run changes nothing, writes no audit row
2. one changed value — one line updated, one audit row
3. a removed designator — the line is archived with a timestamp, not
   deleted, and a build stage referencing it keeps its stage lines
4. `id_code` as the KiCad `StockManager` URL matches; a foreign uuid does not
5. dry run — the same diff, nothing written

plus the fixture built from the shape `netsch parts sync` exports, and
the readers (shortage, BOM listing) ignoring retired lines.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.audit.models import AuditLog
from app.domain.builds.models import BuildStageLine
from app.domain.builds.service import shortage_analysis
from app.domain.projects.bom_import import parse_part_id_code
from app.domain.projects.models import Project, ProjectEntry
from app.main import app
from tests._factories import create_part, signup_user

BASE = "https://parts.example"


def _project(c: TestClient, name: str = "Sync board") -> str:
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _part(c: TestClient, name: str, mpn: str) -> str:
    return create_part(c, name, mpn=mpn)


def _sync(c: TestClient, project_id: str, rows: list[dict], *, dry_run: bool = False) -> dict:
    r = c.post(f"/api/projects/{project_id}/bom/sync", json={"rows": rows, "dry_run": dry_run})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"data", "status"}  # the envelope
    return body["data"]


def _sync_audit_count(db) -> int:
    return db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action.like("project_entry.sync_%"))
    ).scalar_one()


def _entries(db, project_id: str, *, active_only: bool = False) -> list[ProjectEntry]:
    q = select(ProjectEntry).where(ProjectEntry.project_id == UUID(project_id))
    if active_only:
        q = q.where(ProjectEntry.archived_at.is_(None))
    db.expire_all()
    return list(db.execute(q.order_by(ProjectEntry.order_index)).scalars())


@pytest.fixture
def board(authed_client):
    """A project and three parts, and the BOM rows that reference them."""
    c = authed_client
    r100k = _part(c, "100k 0805", "RC0805FR-07100KL")
    c100n = _part(c, "100n 0805", "GRM21BR71H104KA01L")
    tvs = _part(c, "SMCJ30CA", "SMCJ30CA")
    rows = [
        {
            "designators": ["R1", "R2"],
            "value": "100k",
            "footprint": "R_0805",
            "id_code": f"{BASE}/parts/{r100k}",
        },
        {
            "designators": "C1,C2,C3",
            "value": "100n",
            "footprint": "C_0805",
            "mpn": "GRM21BR71H104KA01L",
        },
        {"designators": ["D1"], "value": "SMCJ30CA", "footprint": "D_SMC", "mpn": "SMCJ30CA"},
    ]
    return {"project": _project(c), "rows": rows, "parts": (r100k, c100n, tvs)}


# 1 ---------------------------------------------------------------------------


def test_same_bom_twice_changes_nothing_and_writes_no_audit_row(authed_client, db, board):
    c, project = authed_client, board["project"]
    first = _sync(c, project, board["rows"])
    assert (first["added"], first["updated"], first["removed"]) == (3, 0, 0)
    assert first["unmatched"] == 0
    before = {e.id: e.updated_at for e in _entries(db, project)}
    audit_before = _sync_audit_count(db)
    assert audit_before == 3  # one per added line

    second = _sync(c, project, board["rows"])

    assert (second["added"], second["updated"], second["removed"]) == (0, 0, 0)
    assert second["unchanged"] == 3
    assert _sync_audit_count(db) == audit_before
    assert {e.id: e.updated_at for e in _entries(db, project)} == before


def test_designator_order_and_case_do_not_make_a_new_line(authed_client, db, board):
    c, project = authed_client, board["project"]
    _sync(c, project, board["rows"])
    rows = [dict(r) for r in board["rows"]]
    rows[1]["designators"] = ["c3", "C1", "c2"]
    out = _sync(c, project, rows)
    assert out["unchanged"] == 3 and out["added"] == 0


# 2 ---------------------------------------------------------------------------


def test_changing_one_value_updates_one_line_and_logs_one_audit_row(authed_client, db, board):
    c, project = authed_client, board["project"]
    _sync(c, project, board["rows"])
    ids_before = [e.id for e in _entries(db, project)]
    audit_before = _sync_audit_count(db)

    rows = [dict(r) for r in board["rows"]]
    rows[0]["value"] = "100k 1%"
    out = _sync(c, project, rows)

    assert (out["added"], out["updated"], out["removed"], out["unchanged"]) == (0, 1, 0, 2)
    assert out["lines"]["updated"][0]["changed"] == ["name"]
    assert out["lines"]["updated"][0]["designators"] == ["R1", "R2"]
    assert [e.id for e in _entries(db, project)] == ids_before  # in place
    assert _sync_audit_count(db) == audit_before + 1
    row = db.execute(
        select(AuditLog).where(AuditLog.action == "project_entry.sync_updated")
    ).scalar_one()
    assert row.target_ids == [ids_before[0]]
    assert "fields: name" in row.comment
    assert "100k 1%" not in row.comment  # field names, never values


def test_unresolvable_row_keeps_a_hand_made_part_match(authed_client, db):
    """A sync never clears a link someone set with `.../match`."""
    c = authed_client
    project = _project(c)
    part = _part(c, "Mystery", "MYS-1")
    out = _sync(c, project, [{"designators": ["U1"], "value": "mystery", "mpn": "NOPE-1"}])
    assert out["unmatched"] == 1
    entry_id = out["lines"]["added"][0]["entry_id"]
    r = c.post(f"/api/projects/{project}/entries/{entry_id}/match", json={"part_id": part})
    assert r.status_code == 200, r.text

    again = _sync(c, project, [{"designators": ["U1"], "value": "mystery", "mpn": "NOPE-1"}])

    assert again["unchanged"] == 1 and again["unmatched"] == 0
    assert str(_entries(db, project)[0].part_id) == part


# 3 ---------------------------------------------------------------------------


def test_removed_designator_archives_the_line_and_the_build_keeps_its_lines(
    authed_client, db, board
):
    c, project = authed_client, board["project"]
    first = _sync(c, project, board["rows"])
    d1 = next(line for line in first["lines"]["added"] if line["designators"] == ["D1"])
    r = c.post("/api/builds", json={"name": "B1", "project_id": project, "quantity": 2})
    assert r.status_code == 201, r.text
    build = r.json()["data"]["id"]
    r = c.post(
        f"/api/builds/{build}/stages",
        json={"name": "SMT", "lines": [{"project_entry_id": d1["entry_id"], "portion_pct": 100}]},
    )
    assert r.status_code == 201, r.text

    out = _sync(c, project, board["rows"][:2])

    assert (out["removed"], out["unchanged"]) == (1, 2)
    assert out["lines"]["removed"][0]["entry_id"] == d1["entry_id"]
    gone = db.get(ProjectEntry, UUID(d1["entry_id"]))
    db.refresh(gone)
    assert gone.archived_at is not None  # timestamped, not deleted
    stage_lines = db.execute(
        select(func.count())
        .select_from(BuildStageLine)
        .where(BuildStageLine.project_entry_id == gone.id)
    ).scalar_one()
    assert stage_lines == 1
    assert c.get(f"/api/builds/{build}/stages").status_code == 200
    assert c.get(f"/api/builds/{build}").status_code == 200

    # The readers no longer see it.
    listed = c.get(f"/api/projects/{project}/entries").json()["data"]
    assert d1["entry_id"] not in {e["id"] for e in listed}
    listed_all = c.get(f"/api/projects/{project}/entries?include_archived=true").json()["data"]
    assert d1["entry_id"] in {e["id"] for e in listed_all}
    proj = db.get(Project, UUID(project))
    shortage = shortage_analysis(db, workspace_id=proj.workspace_id, project=proj, build_quantity=1)
    assert d1["entry_id"] not in {row["project_entry_id"] for row in shortage}
    assert len(shortage) == 2

    # Coming back restores the same line rather than inserting a new one.
    back = _sync(c, project, board["rows"])
    assert back["added"] == 1 and back["lines"]["added"][0]["restored"] is True
    assert back["lines"]["added"][0]["entry_id"] == d1["entry_id"]
    db.refresh(gone)
    assert gone.archived_at is None


def test_splitting_a_group_is_a_removal_plus_additions(authed_client, db, board):
    c, project = authed_client, board["project"]
    _sync(c, project, board["rows"])
    rows = [dict(r) for r in board["rows"]]
    rows[0] = {**rows[0], "designators": ["R1"]}
    rows.append({**board["rows"][0], "designators": ["R2"]})
    out = _sync(c, project, rows)
    assert (out["added"], out["removed"], out["unchanged"]) == (2, 1, 2)


def test_leftover_duplicate_lines_from_an_append_import_are_retired(authed_client, db, board):
    c, project = authed_client, board["project"]
    for _ in range(2):  # what the old delete-and-reimport flow left behind on a failure
        r = c.post(
            f"/api/projects/{project}/entries",
            json={"part_id": board["parts"][2], "quantity": 1, "designators": ["D1"]},
        )
        assert r.status_code in (200, 201), r.text
    out = _sync(c, project, [{"designators": ["D1"], "mpn": "SMCJ30CA"}])
    assert (out["unchanged"], out["removed"]) == (1, 1)
    assert len(_entries(db, project, active_only=True)) == 1


# 4 ---------------------------------------------------------------------------


def test_stockmanager_url_matches_and_a_foreign_uuid_does_not(authed_client, db):
    c = authed_client
    mine = _part(c, "Mine", "MINE-1")
    other = TestClient(app)
    signup_user(other, email=f"other-{uuid.uuid4().hex[:6]}@x.com")
    foreign = create_part(other, "Theirs", mpn="THEIRS-1")
    project = _project(c)

    out = _sync(
        c,
        project,
        [
            {"designators": ["U1"], "id_code": f"{BASE}/parts/{mine}"},
            {"designators": ["U2"], "id_code": f"{BASE}/parts/{foreign}"},
        ],
    )

    by_ref = {line["designators"][0]: line for line in out["lines"]["added"]}
    assert by_ref["U1"]["part_id"] == mine
    assert by_ref["U2"]["part_id"] is None
    assert out["unmatched"] == 1
    assert {str(e.part_id) for e in _entries(db, project)} == {mine, "None"}


@pytest.mark.parametrize(
    ("id_code", "expected"),
    [
        (f"https://parts.example/parts/{'1' * 8}-1111-1111-1111-{'1' * 12}", True),
        (f"http://localhost:5173/parts/{'1' * 8}-1111-1111-1111-{'1' * 12}/", True),
        (f"https://parts.example/parts/{'1' * 8}-1111-1111-1111-{'1' * 12}?tab=eda", True),
        (f"  {'1' * 8}-1111-1111-1111-{'1' * 12}  ", True),
        (f"https://parts.example/projects/{'1' * 8}-1111-1111-1111-{'1' * 12}", False),
        (f"https://parts.example/parts/{'1' * 8}-1111-1111-1111-{'1' * 12}/eda", False),
        ("https://parts.example/parts/not-a-uuid", False),
        ("R_0805", False),
    ],
)
def test_parse_part_id_code(id_code, expected):
    parsed = parse_part_id_code(id_code)
    assert (parsed == UUID(f"{'1' * 8}-1111-1111-1111-{'1' * 12}")) is expected


def test_designator_strings_and_kicad_ranges_are_split():
    from app.domain.projects.schemas import BomSyncRowIn

    row = BomSyncRowIn(designators=["C3,C4,C6-C8", "R1 R2"])
    assert row.designators == ["C3", "C4", "C6", "C7", "C8", "R1", "R2"]
    # Not a range: different prefixes, descending, or absurdly wide.
    kept = BomSyncRowIn(designators="R1-C3,R5-R2,TP-1,R1-R5000")
    assert kept.designators == ["R1-C3", "R5-R2", "TP-1", "R1-R5000"]


@pytest.mark.parametrize(
    "designators",
    [
        "R1-R999999999",  # one huge range: kept as written, never expanded
        ",".join(["R1-R999"] * 50),  # many in-cap ranges: refused on the running count
        " ".join(["R1-R999"] * 2499),  # 5 000-ish tokens' worth, under the char caps
        ",".join(["R1-R999"] * 5000),  # 5 000 tokens: over the per-string cap
        ["R1"] * 2001,  # too many raw items
        "R" * 40_001,  # too many raw characters
    ],
    ids=["huge-range", "amplified-ranges", "budget", "5000-tokens", "too-many-items", "too-long"],
)
def test_designator_expansion_is_bounded(authed_client, designators):
    import time

    project = _project(authed_client)
    started = time.monotonic()
    r = authed_client.post(
        f"/api/projects/{project}/bom/sync",
        json={"rows": [{"designators": designators}], "dry_run": True},
    )
    assert time.monotonic() - started < 2
    if designators == "R1-R999999999":
        assert r.status_code == 200, r.text
        assert r.json()["data"]["lines"]["added"][0]["designators"] == ["R1-R999999999"]
    else:
        assert r.status_code == 422, r.text


def test_mpn_matches_whatever_the_manufacturer_spelling(authed_client, db):
    """MPN is unique per workspace; the manufacturer is not a gate."""
    c = authed_client
    rf = create_part(c, "SMA jack", mpn="132289", manufacturer="Amphenol RF")
    bare = create_part(c, "Header", mpn="61300311121")  # no manufacturer at all
    project = _project(c)

    out = _sync(
        c,
        project,
        [
            {"designators": ["J1"], "mpn": "132289", "manufacturer": "Amphenol"},
            {"designators": ["J2"], "mpn": "61300311121", "manufacturer": "Wurth Elektronik"},
        ],
    )

    by_ref = {line["designators"][0]: line["part_id"] for line in out["lines"]["added"]}
    assert by_ref == {"J1": rf, "J2": bare}
    assert out["unmatched"] == 0


def test_mpn_prefers_the_active_part_then_the_manufacturer(authed_client, db):
    """The tie-breaker for the archived duplicates `uq_parts_ws_mpn` allows."""
    c = authed_client
    old = create_part(c, "Old", mpn="DUP-1", manufacturer="Acme")
    assert c.post(f"/api/parts/{old}/archive").status_code == 200
    live = create_part(c, "Live", mpn="DUP-1", manufacturer="Other")
    project = _project(c)

    out = _sync(c, project, [{"designators": ["U1"], "mpn": "DUP-1", "manufacturer": "Acme"}])

    assert out["lines"]["added"][0]["part_id"] == live


# 5 ---------------------------------------------------------------------------


def test_dry_run_returns_the_diff_and_writes_nothing(authed_client, db, board):
    c, project = authed_client, board["project"]
    preview = _sync(c, project, board["rows"], dry_run=True)
    assert preview["dry_run"] is True
    assert preview["added"] == 3
    assert all(line["entry_id"] is None for line in preview["lines"]["added"])
    assert _entries(db, project) == []
    assert _sync_audit_count(db) == 0

    _sync(c, project, board["rows"])
    before = [(e.id, e.name, e.updated_at, e.archived_at) for e in _entries(db, project)]
    audit_before = _sync_audit_count(db)
    rows = [dict(board["rows"][0], quantity=5), board["rows"][1]]

    diff = _sync(c, project, rows, dry_run=True)

    assert (diff["updated"], diff["removed"], diff["unchanged"]) == (1, 1, 1)
    assert diff["lines"]["updated"][0]["changed"] == ["quantity"]
    after = [(e.id, e.name, e.updated_at, e.archived_at) for e in _entries(db, project)]
    assert after == before
    assert _sync_audit_count(db) == audit_before


# Refusals --------------------------------------------------------------------


def test_duplicate_and_unkeyed_rows_are_refused(authed_client, db, board):
    c, project = authed_client, board["project"]
    rows = [board["rows"][0], {**board["rows"][0], "designators": ["r2", "R1"]}]
    r = c.post(f"/api/projects/{project}/bom/sync", json={"rows": rows})
    assert r.status_code == 422
    assert r.json()["code"] == "bom.sync_duplicate_line"

    r = c.post(f"/api/projects/{project}/bom/sync", json={"rows": [{"quantity": 2}]})
    assert r.status_code == 422
    assert r.json()["code"] == "bom.sync_unkeyed_row"
    assert _entries(db, project) == []


def test_unknown_project_is_404(authed_client):
    r = authed_client.post(f"/api/projects/{uuid.uuid4()}/bom/sync", json={"rows": []})
    assert r.status_code == 404
    assert r.json()["code"] == "project.not_found"


# The netsch export -----------------------------------------------------------

# What `netsch parts sync` (NeuronSW2/netsch/stock/sync.py::export_bom)
# produces from `kicad-cli sch export bom --group-by Value,Footprint,MPN`
# for the 24 V input filter: one dict per group. kicad-cli joins the
# references with "," and collapses runs into ranges by default, and netsch
# splits on whitespace, so a group's `refs` is one comma-joined string
# (`C6-C8` included). `part_url` is the `StockManager` field when the sheet
# is HTTP-bound and empty when it is not.
NETSCH_EXPORT = [
    {"refs": ["C1,C2,C10,C11"], "qty": 4, "value": "C_2n2_1812_2kV",
     "footprint": "Capacitor_SMD:C_1812_4532Metric", "mpn": "C1812C222KGRACTU",
     "manufacturer": "KEMET", "part_url": "{url:C1812C222KGRACTU}"},
    {"refs": ["C3,C4,C6-C8"], "qty": 5, "value": "C_10u_1206_50V_X7R",
     "footprint": "Capacitor_SMD:C_1206_3216Metric", "mpn": "C3216X7R1H106K160AC",
     "manufacturer": "TDK", "part_url": "{url:C3216X7R1H106K160AC}"},
    {"refs": ["C5"], "qty": 1, "value": "EEE-FK1H102AM",
     "footprint": "NSW:CAP_EEVFK1V102M", "mpn": "EEE-FK1H102AM",
     "manufacturer": "Panasonic", "part_url": ""},
    {"refs": ["R1"], "qty": 1, "value": "100k",
     "footprint": "Resistor_SMD:R_0805_2012Metric", "mpn": "RC0805FR-07100KL",
     "manufacturer": "Yageo", "part_url": "{url:RC0805FR-07100KL}"},
    {"refs": ["J1"], "qty": 1, "value": "1757255",
     "footprint": "Connector_Phoenix_MSTB:"
                  "PhoenixContact_MSTBA_2,5_3-G-5,08_1x03_P5.08mm_Horizontal",
     "mpn": "1757255", "manufacturer": "Phoenix Contact", "part_url": ""},
]


def netsch_row_to_sync_row(row: dict) -> dict:
    """The mapping `docs/notes/edaflow-bom-sync-migration.md` documents."""
    return {
        "designators": row["refs"],
        "quantity": row["qty"],
        "value": row["value"] or None,
        "footprint": row["footprint"] or None,
        "mpn": row["mpn"] or None,
        "manufacturer": row["manufacturer"] or None,
        "id_code": row["part_url"] or None,
    }


def test_netsch_export_fixture_syncs_and_resyncs_cleanly(authed_client, db):
    c = authed_client
    # The MPN branch of `_match_part` also compares the manufacturer when
    # the row carries one, so the parts carry theirs as the real ones do.
    ids = {
        row["mpn"]: create_part(c, row["mpn"], mpn=row["mpn"], manufacturer=row["manufacturer"])
        for row in NETSCH_EXPORT[:4]
    }
    export = [
        {**row, "part_url": row["part_url"].replace(
            f"{{url:{row['mpn']}}}", f"{BASE}/parts/{ids.get(row['mpn'], '')}"
        )}
        for row in NETSCH_EXPORT
    ]
    rows = [netsch_row_to_sync_row(row) for row in export]
    project = _project(c, "24V Input Filter")

    out = _sync(c, project, rows)

    assert (out["added"], out["unmatched"]) == (5, 1)  # J1's part does not exist yet
    assert out["lines"]["unmatched"][0]["designators"] == ["J1"]
    by_ref = {tuple(e.designators): e for e in _entries(db, project)}
    assert str(by_ref[("C5",)].part_id) == ids["EEE-FK1H102AM"]  # matched by MPN
    assert by_ref[("C3", "C4", "C6", "C7", "C8")].quantity == 5
    assert by_ref[("C1", "C2", "C10", "C11")].cad_footprint == "Capacitor_SMD:C_1812_4532Metric"

    # The owner creates the connector, binds the sheet, exports again.
    ids["1757255"] = _part(c, "MSTBA 3-way", "1757255")
    export[4]["part_url"] = f"{BASE}/parts/{ids['1757255']}"
    again = _sync(c, project, [netsch_row_to_sync_row(row) for row in export])

    assert (again["updated"], again["unchanged"], again["unmatched"]) == (1, 4, 0)
    assert again["lines"]["updated"][0]["changed"] == ["entry_type", "part_id"]
