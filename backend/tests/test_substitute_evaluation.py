"""SM-012: substitute links carry a relation and an evaluation state.

Acceptance map (edaflow modules/stockmanager.md SM-012, plus the brief):
* a link carries relation, evaluation, evidence, by, at
  → test_rest_patch_records_relation_evaluation_evidence_by_and_at,
    test_rest_create_accepts_the_evaluation_fields,
    test_mcp_set_and_list_substitute_evaluation
* existing links read not-evaluated
  → test_a_link_created_without_fields_reads_not_evaluated,
    test_column_server_default_reads_not_evaluated
* bom_shortages reports substitute availability split by evaluation
  → test_bom_shortages_splits_substitute_availability_by_evaluation,
    test_rest_shortage_rows_carry_the_split
* existing numbers unchanged (short_by / substitute_available still count
  every substitute) → test_bom_shortages_splits_... (short_by == 0)
* workspace isolation (foreign substitute → not found)
  → test_other_workspace_cannot_read_or_write_a_link
* read-only token refused for writes
  → test_read_only_token_is_refused_for_set_substitute_evaluation
* audit row per change, no free text
  → test_audit_row_names_fields_and_state_but_not_evidence
"""
from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.domain.audit.models import AuditLog
from app.domain.parts.models import PartSubstitute
from app.main import app
from tests._factories import add_stock, create_part, create_project_with_bom, signup_user
from tests.test_mcp import _mint, call, call_error, mcp_session


@pytest.fixture
def full_token(authed_client) -> str:
    return _mint(authed_client)["token"]


def _link(client: TestClient, part_id: str, sub_id: str, **body) -> None:
    r = client.post(
        f"/api/parts/{part_id}/substitutes",
        json={"substitute_part_id": sub_id, **body},
    )
    assert r.status_code == 200, r.text


def _subs(client: TestClient, part_id: str) -> list[dict]:
    r = client.get(f"/api/parts/{part_id}/substitutes")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _patch(client: TestClient, part_id: str, sub_id: str, **body):
    return client.patch(f"/api/parts/{part_id}/substitutes/{sub_id}", json=body)


# ---------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------


def test_a_link_created_without_fields_reads_not_evaluated(authed_client):
    main = create_part(authed_client, "SM012 main")
    alt = create_part(authed_client, "SM012 alt")
    _link(authed_client, main, alt)

    [row] = _subs(authed_client, main)

    assert row == {
        "part_id": alt,
        "direction": "bidirectional",
        "relation": None,
        "evaluation": "not-evaluated",
        "evidence": None,
        "evaluated_by": None,
        "evaluated_at": None,
    }


def test_column_server_default_reads_not_evaluated(authed_client, db):
    """What migration 0089 gives a pre-existing row: raw INSERT without
    the new columns, as the old code wrote them."""
    main = create_part(authed_client, "SM012 legacy main")
    alt = create_part(authed_client, "SM012 legacy alt")
    ws_id = db.execute(
        text("SELECT workspace_id FROM parts WHERE id = :id"), {"id": main}
    ).scalar_one()
    db.execute(
        text(
            "INSERT INTO part_substitutes (id, workspace_id, part_id, substitute_part_id, "
            "direction) VALUES (gen_random_uuid(), :ws, :p, :s, 'one_way')"
        ),
        {"ws": ws_id, "p": main, "s": alt},
    )
    db.commit()

    [row] = _subs(authed_client, main)

    assert row["evaluation"] == "not-evaluated"
    assert row["relation"] is None


def test_rest_create_accepts_the_evaluation_fields(authed_client):
    main = create_part(authed_client, "SM012 create main")
    alt = create_part(authed_client, "SM012 create alt")
    _link(
        authed_client, main, alt,
        relation="footprint-compatible", evaluation="evaluated-ok", evidence="SIM-001",
    )

    [row] = _subs(authed_client, main)

    assert row["relation"] == "footprint-compatible"
    assert row["evaluation"] == "evaluated-ok"
    assert row["evidence"] == "SIM-001"
    assert row["evaluated_by"] is not None
    assert row["evaluated_at"] is not None


def test_rest_patch_records_relation_evaluation_evidence_by_and_at(authed_client):
    main = create_part(authed_client, "SM012 patch main")
    alt = create_part(authed_client, "SM012 patch alt")
    _link(authed_client, main, alt)
    me = authed_client.get("/api/auth/me").json()["data"]

    r = _patch(
        authed_client, main, alt,
        relation="pin-compatible", evaluation="evaluated-ok", evidence="SIM-012",
    )

    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["relation"] == "pin-compatible"
    assert body["evaluation"] == "evaluated-ok"
    assert body["evidence"] == "SIM-012"
    user_id = me.get("id") or me.get("user", {}).get("id")
    assert body["evaluated_by"] == user_id
    assert body["evaluated_at"]
    assert _subs(authed_client, main)[0] == body


def test_rest_patch_merges_and_clears(authed_client):
    main = create_part(authed_client, "SM012 merge main")
    alt = create_part(authed_client, "SM012 merge alt")
    _link(authed_client, main, alt, relation="functional", evidence="TEST-1")

    r = _patch(authed_client, main, alt, evaluation="rejected")
    cleared = _patch(authed_client, main, alt, relation=None, evidence=None)

    assert r.json()["data"]["relation"] == "functional"
    assert r.json()["data"]["evidence"] == "TEST-1"
    assert cleared.json()["data"]["relation"] is None
    assert cleared.json()["data"]["evidence"] is None
    assert cleared.json()["data"]["evaluation"] == "rejected"


@pytest.mark.parametrize(
    "body",
    [
        {"relation": "drop-in"},
        {"evaluation": "maybe"},
        {"evaluation": None},
        {"evidence": "x" * 501},
        {"direction": "one_way"},
    ],
)
def test_rest_patch_refuses_bad_values_and_writes_nothing(authed_client, db, body):
    main = create_part(authed_client, "SM012 bad main")
    alt = create_part(authed_client, "SM012 bad alt")
    _link(authed_client, main, alt)

    r = _patch(authed_client, main, alt, **body)

    assert r.status_code == 422, r.text
    assert _subs(authed_client, main)[0]["evaluation"] == "not-evaluated"
    assert _subs(authed_client, main)[0]["evaluated_at"] is None


def test_rest_patch_on_a_missing_link_is_not_found(authed_client):
    main = create_part(authed_client, "SM012 nolink main")
    alt = create_part(authed_client, "SM012 nolink alt")

    r = _patch(authed_client, main, alt, evaluation="evaluated-ok")

    assert r.status_code == 404
    assert r.json()["code"] == "part.substitute_not_found"


def test_audit_row_names_fields_and_state_but_not_evidence(authed_client, db):
    main = create_part(authed_client, "SM012 audit main")
    alt = create_part(authed_client, "SM012 audit alt")
    _link(authed_client, main, alt)

    _patch(authed_client, main, alt, evaluation="evaluated-ok", evidence="SECRET-ish note")
    noop = _patch(authed_client, main, alt, evaluation="evaluated-ok")

    rows = db.execute(
        select(AuditLog).where(AuditLog.action == "part.substitute_evaluated")
    ).scalars().all()
    assert noop.status_code == 200
    assert len(rows) == 1  # the no-op PATCH changed nothing and logs nothing
    assert rows[0].comment == "fields=evaluation,evidence;evaluation=evaluated-ok"
    assert rows[0].target_ids == [UUID(main), UUID(alt)]


# ---------------------------------------------------------------------
# Shortages
# ---------------------------------------------------------------------


def _shortage_fixture(client: TestClient) -> tuple[str, dict[str, str]]:
    """Three BOM lines needing 10 each, own stock 2, one substitute each
    holding 8 — one line per evaluation state — and a fourth whose
    substitute is evaluated-ok but holds too little."""
    ids: dict[str, str] = {}
    bom = []
    for state in ("evaluated-ok", "not-evaluated", "rejected", "thin"):
        main = create_part(client, f"SM012 short {state}")
        alt = create_part(client, f"SM012 short {state} alt")
        add_stock(client, main, 2)
        add_stock(client, alt, 8 if state != "thin" else 3)
        evaluation = "evaluated-ok" if state == "thin" else state
        _link(client, main, alt, evaluation=evaluation)
        ids[state] = main
        bom.append({"part_id": main, "quantity": 10})
    return create_project_with_bom(client, "SM012 board", bom), ids


async def test_bom_shortages_splits_substitute_availability_by_evaluation(
    authed_client, full_token
):
    project_id, ids = _shortage_fixture(authed_client)

    async with mcp_session(full_token) as s:
        result = await call(s, "bom_shortages", project_id=project_id)

    by_part = {row["part_id"]: row for row in result["shortages"]}
    risky = {row["part_id"]: row for row in result["covered_by_unevaluated_substitutes"]}
    # Existing numbers unchanged: every substitute still counts in short_by,
    # so only the "thin" line (2 + 3 < 10) is short.
    assert set(by_part) == {ids["thin"]}
    thin = by_part[ids["thin"]]
    assert thin["short_by"] == 5
    assert thin["short_by_evaluated_only"] == 5
    assert thin["substitute_available_by_evaluation"] == {
        "evaluated_ok": 3, "not_evaluated": 0, "rejected": 0,
    }
    # Covered only because an unevaluated / rejected substitute counts.
    assert set(risky) == {ids["not-evaluated"], ids["rejected"]}
    assert risky[ids["not-evaluated"]]["short_by"] == 0
    assert risky[ids["not-evaluated"]]["short_by_evaluated_only"] == 8
    assert risky[ids["not-evaluated"]]["substitute_available_by_evaluation"] == {
        "evaluated_ok": 0, "not_evaluated": 8, "rejected": 0,
    }
    assert risky[ids["rejected"]]["substitute_available_by_evaluation"]["rejected"] == 8
    assert risky[ids["rejected"]]["substitute_available"] == 8
    assert result["lines_checked"] == 4


def test_rest_shortage_rows_carry_the_split(authed_client):
    project_id, ids = _shortage_fixture(authed_client)

    r = authed_client.get(
        "/api/reports/bom-shortage", params={"project_id": project_id, "quantity": 1}
    )

    assert r.status_code == 200, r.text
    rows = {row["part_id"]: row for row in r.json()["data"]["rows"]}
    ok_row = rows[ids["evaluated-ok"]]
    assert ok_row["substitute_available"] == 8
    assert ok_row["short_by"] == 0
    assert ok_row["short_by_evaluated_only"] == 0
    assert ok_row["substitute_available_by_evaluation"] == {
        "evaluated_ok": 8, "not_evaluated": 0, "rejected": 0,
    }
    assert rows[ids["rejected"]]["short_by_evaluated_only"] == 8


# ---------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------


async def test_mcp_set_and_list_substitute_evaluation(authed_client, full_token, db):
    main = create_part(authed_client, "SM012 mcp main", mpn="SM012-MAIN")
    alt = create_part(authed_client, "SM012 mcp alt", mpn="SM012-ALT")
    _link(authed_client, main, alt)  # bidirectional

    async with mcp_session(full_token) as s:
        before = await call(s, "list_substitutes", part_id_or_mpn="SM012-MAIN")
        # Reverse order: a bidirectional link matches from either side.
        result = await call(
            s, "set_substitute_evaluation",
            part_id_or_mpn="SM012-ALT", substitute_id_or_mpn="SM012-MAIN",
            relation="pin-compatible", evaluation="evaluated-ok", evidence="SIM-012",
        )
        again = await call(
            s, "set_substitute_evaluation",
            part_id_or_mpn=main, substitute_id_or_mpn=alt, evaluation="evaluated-ok",
        )
        from_other_side = await call(s, "list_substitutes", part_id_or_mpn=alt)

    assert before["substitutes"][0]["evaluation"] == "not-evaluated"
    assert "relation" not in before["substitutes"][0]
    assert result["updated"] == ["relation", "evaluation", "evidence"]
    sub = result["substitute"]
    assert sub["part_id"] == main and sub["mpn"] == "SM012-MAIN"
    assert sub["relation"] == "pin-compatible"
    assert sub["evaluation"] == "evaluated-ok"
    assert sub["evidence"] == "SIM-012"
    assert sub["evaluated_by"] and sub["evaluated_at"]
    assert again["updated"] == []
    assert from_other_side["substitutes"][0]["part_id"] == main
    assert from_other_side["substitutes"][0]["evaluation"] == "evaluated-ok"
    audit = db.execute(
        select(AuditLog).where(AuditLog.action == "part.substitute_evaluated")
    ).scalars().all()
    assert len(audit) == 1
    assert "SIM-012" not in (audit[0].comment or "")


async def test_mcp_one_way_link_does_not_match_backwards(authed_client, full_token):
    main = create_part(authed_client, "SM012 oneway main")
    alt = create_part(authed_client, "SM012 oneway alt")
    _link(authed_client, main, alt, direction="one_way")

    async with mcp_session(full_token) as s:
        message = await call_error(
            s, "set_substitute_evaluation",
            part_id_or_mpn=alt, substitute_id_or_mpn=main, evaluation="rejected",
        )
        empty = await call_error(
            s, "set_substitute_evaluation", part_id_or_mpn=main, substitute_id_or_mpn=alt,
        )

    assert "part.substitute_not_found" in message, message
    assert "part.substitute_invalid" in empty, empty


async def test_read_only_token_is_refused_for_set_substitute_evaluation(
    authed_client, db
):
    main = create_part(authed_client, "SM012 ro main")
    alt = create_part(authed_client, "SM012 ro alt")
    _link(authed_client, main, alt)
    readonly = _mint(authed_client, read_only=True)["token"]

    async with mcp_session(readonly) as s:
        message = await call_error(
            s, "set_substitute_evaluation",
            part_id_or_mpn=main, substitute_id_or_mpn=alt, evaluation="evaluated-ok",
        )
        listed = await call(s, "list_substitutes", part_id_or_mpn=main)

    assert "auth.token_read_only" in message, message
    assert listed["substitutes"][0]["evaluation"] == "not-evaluated"
    row = db.execute(select(PartSubstitute)).scalars().one()
    assert row.evaluation == "not-evaluated"


async def test_other_workspace_cannot_read_or_write_a_link(authed_client, db):
    main = create_part(authed_client, "SM012 iso main", mpn="SM012-ISO")
    alt = create_part(authed_client, "SM012 iso alt")
    _link(authed_client, main, alt)
    other = TestClient(app)
    signup_user(other)
    other_main = create_part(other, "SM012 their main")
    _link(other, other_main, create_part(other, "SM012 their alt"))
    other_token = _mint(other)["token"]

    async with mcp_session(other_token) as s:
        by_id = await call_error(
            s, "set_substitute_evaluation",
            part_id_or_mpn=main, substitute_id_or_mpn=alt, evaluation="rejected",
        )
        foreign_sub = await call_error(
            s, "set_substitute_evaluation",
            part_id_or_mpn=other_main, substitute_id_or_mpn=alt, evaluation="rejected",
        )
        listed = await call_error(s, "list_substitutes", part_id_or_mpn="SM012-ISO")
    rest_patch = _patch(other, main, alt, evaluation="rejected")
    rest_foreign_sub = _patch(other, other_main, alt, evaluation="rejected")
    rest_list = other.get(f"/api/parts/{main}/substitutes")

    assert "part.not_found" in by_id, by_id
    assert "part.not_found" in foreign_sub, foreign_sub
    assert "part.not_found" in listed, listed
    assert rest_patch.status_code == 404
    assert rest_foreign_sub.status_code == 404
    assert rest_foreign_sub.json()["code"] == "part.substitute_not_found"
    assert rest_list.status_code == 404
    mine = db.execute(
        select(PartSubstitute).where(PartSubstitute.part_id == UUID(main))
    ).scalars().one()
    assert mine.evaluation == "not-evaluated"
