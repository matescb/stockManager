"""A5 — the `spec-normalize` backfill.

The 9,377 provider `custom_fields` rows on prod pre-date the spec schema
(ADR-0034): un-normalised keys, no `provider`, no `value_num`, customs
codes and ~1,000 rows whose value is literally `-`. A3 normalises a part
on its NEXT refresh; this job does the whole table at once.

What is pinned here:

* **a dry run writes nothing.** It is the review step before a bulk
  rewrite of a table on a system with no staging environment, so it has
  to be provably side-effect-free — and it must produce the CSV the
  operator reviews.
* **`--apply` re-keys onto the canonical schema.** `Resistance: "10 kOhms"`
  becomes `resistance: "10 kΩ"` with `value_num = 10000`, junk is
  archived, `-` values are retired, and every canonical row it touches
  carries a non-NULL `provider`.
* **nothing a user touched moves.** `manual` and `override` rows are
  invisible to this job.
* **it is idempotent.** The second run reports zero changes.
* **workspace isolation.** `--workspace` scopes the run, and a run over
  every workspace never mixes two workspaces' rows.
"""
from __future__ import annotations

import csv
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.cli.run_job import BackfillOptions, main, run_job
from app.core.time import utcnow
from app.core.advisory_locks import SPEC_NORMALIZE_LOCK_CLASSID
from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.services.spec_normalize import (
    AUDIT_ACTION,
    REPORT_COLUMNS,
    UnknownWorkspaceError,
    normalize_specs,
)
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import signup_user

MPN = "RC0402FR-0710KL"


# ---------------------------------------------------------------------------
# Fixtures — a workspace whose parts carry PRE-A3 rows, written the way the
# old reconciler wrote them: bare keys, `source='provider'`, provider NULL.
# ---------------------------------------------------------------------------
@pytest.fixture
def client(db) -> TestClient:
    return TestClient(app)


def _signup(client: TestClient, email: str | None = None) -> uuid.UUID:
    return uuid.UUID(signup_user(client, email=email).json()["data"]["workspace_id"])


def _set_primary(db, ws_id: uuid.UUID, provider: str) -> None:
    db.get(Workspace, ws_id).parts_provider = provider
    db.flush()


def _category(client: TestClient, name: str, parent_id: str | None = None) -> str:
    body: dict = {"name": name}
    if parent_id:
        body["parent_id"] = parent_id
    r = client.post("/api/categories", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _part(
    client: TestClient,
    db,
    mpn: str = MPN,
    *,
    linked_provider: str | None = None,
    **kwargs,
) -> uuid.UUID:
    """A part through the API, then linked through the ORM.

    `linked_provider` is not writable over REST — only a provider import
    or a refresh sets it — so the fixture writes the column directly to
    model a part that WAS imported before the spec schema existed."""
    r = client.post(
        "/api/parts", json={"name": mpn, "part_type": "linked", "mpn": mpn, **kwargs}
    )
    assert r.status_code in (200, 201), r.text
    part_id = uuid.UUID(r.json()["data"]["id"])
    if linked_provider is not None:
        db.get(Part, part_id).linked_provider = linked_provider
        db.flush()
    return part_id


def _legacy_row(
    db,
    *,
    ws_id: uuid.UUID,
    part_id: uuid.UUID,
    key: str,
    value: str,
    source: str = "provider",
    provider: str | None = None,
    original_value: str | None = None,
) -> CustomField:
    """One `custom_fields` row shaped like the pre-A3 writer left it."""
    row = CustomField(
        workspace_id=ws_id,
        object_type="part",
        object_id=part_id,
        key=key,
        value=value,
        source=source,
        provider=provider,
        original_value=original_value,
    )
    db.add(row)
    db.flush()
    return row


def _normalize(db, path: Path | None = None, **kwargs):
    """`normalize_specs` with the report file opened the way the CLI opens
    it — `run_job._report_stream` owns the handle in production, so the
    tests do too rather than pretending the job takes a path."""
    if path is None:
        return normalize_specs(db, **kwargs)
    with path.open("w", encoding="utf-8", newline="") as stream:
        return normalize_specs(db, stream=stream, **kwargs)


RESISTOR_ROWS: tuple[tuple[str, str], ...] = (
    ("Resistance", "10 kOhms"),
    ("Tolerance", "±1%"),
    ("Power (Watts)", "0.063W, 1/16W"),
    ("Package / Case", "0402 (1005 Metric)"),
    # Junk keys — customs and compliance codes, 1,100+ rows of them on prod.
    ("ECCN", "EAR99"),
    ("TARIC", "8533210000"),
    # A placeholder value; ~1,000 prod rows look exactly like this.
    ("Failure Rate", "-"),
    # Neither junk nor canonical: kept verbatim so ICs lose nothing.
    ("Features", "Moisture Resistant"),
)


def _rows_by_key(db, part_id: uuid.UUID) -> dict[str, CustomField]:
    db.expire_all()
    return {
        row.key: row
        for row in db.execute(
            select(CustomField)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == part_id)
        ).scalars()
    }


@pytest.fixture
def resistor(client: TestClient, db) -> tuple[uuid.UUID, uuid.UUID]:
    """A DigiKey-linked chip resistor filed under Resistors, pre-A3 rows."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    for key, value in RESISTOR_ROWS:
        _legacy_row(db, ws_id=ws_id, part_id=part_id, key=key, value=value)
    db.commit()
    return ws_id, part_id


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------
def test_dry_run_writes_nothing_to_the_database(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    ws_id, part_id = resistor
    before = {
        key: (row.value, row.value_num, row.provider, row.archived_at)
        for key, row in _rows_by_key(db, part_id).items()
    }

    outcome = _normalize(db, tmp_path / "report.csv")

    assert outcome.changes > 0, "the fixture has work to do"
    after = {
        key: (row.value, row.value_num, row.provider, row.archived_at)
        for key, row in _rows_by_key(db, part_id).items()
    }
    assert after == before
    assert db.execute(select(AuditLog).where(AuditLog.action == AUDIT_ACTION)).first() is None
    assert ws_id is not None


def test_dry_run_reports_exactly_what_apply_then_does(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    dry = tmp_path / "dry.csv"
    wet = tmp_path / "wet.csv"

    _normalize(db, dry)
    _normalize(db, wet, apply=True)

    assert _report_rows(dry) == _report_rows(wet)


def _report_rows(path: Path) -> list[dict[str, str]]:
    """The CSV's change section, i.e. everything before the blank line."""
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            break
        lines.append(line)
    return list(csv.DictReader(lines))


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def test_apply_rekeys_onto_the_canonical_schema_with_a_numeric_sidecar(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    _, part_id = resistor

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert "Resistance" not in rows, "the raw key was renamed, not duplicated"
    resistance = rows["resistance"]
    assert resistance.value == "10 kΩ"
    assert float(resistance.value_num) == 10000
    assert resistance.source == "provider"
    assert resistance.archived_at is None
    assert rows["tolerance"].value == "1%"
    assert rows["package"].value == "0402 (1005 Metric)"


def test_apply_archives_junk_keys(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    _, part_id = resistor

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert rows["ECCN"].archived_at is not None
    assert rows["TARIC"].archived_at is not None


def test_apply_retires_placeholder_values(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    _, part_id = resistor

    _normalize(db, tmp_path / "report.csv", apply=True)

    assert _rows_by_key(db, part_id)["Failure Rate"].archived_at is not None


def test_apply_keeps_an_unrecognised_parametric_key_verbatim(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    _, part_id = resistor

    _normalize(db, tmp_path / "report.csv", apply=True)

    features = _rows_by_key(db, part_id)["Features"]
    assert features.value == "Moisture Resistant"
    assert features.archived_at is None


def test_every_canonical_row_it_writes_carries_a_provider(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    """The hard precondition: a canonical row with `provider IS NULL` is
    claimable by ANY provider's next refresh
    (`spec_schema.provider_outranks` treats an unstamped row as
    claimable), so leaving one behind hands a
    normalised value to whoever refreshes first."""
    _, part_id = resistor

    _normalize(db, tmp_path / "report.csv", apply=True)

    for key in ("resistance", "tolerance", "power", "package"):
        assert _rows_by_key(db, part_id)[key].provider == "digikey", key


def test_provider_falls_back_to_the_workspace_primary(
    client: TestClient, db, tmp_path: Path
) -> None:
    """A part with no `linked_provider` still gets a stamped canonical row."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id)
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="10 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    assert _rows_by_key(db, part_id)["resistance"].provider == "mouser"


def test_a_part_nobody_can_be_attributed_keeps_its_canonical_rows_unwritten(
    client: TestClient, db, tmp_path: Path
) -> None:
    """No `linked_provider`, no workspace primary — so no provider name to
    stamp. Re-keying anyway would write the NULL-provider row this job
    exists to remove. Junk is still retired: a customs code is junk
    whoever wrote it."""
    ws_id = _signup(client)
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id)
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="10 kOhms")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="ECCN", value="EAR99")
    db.commit()

    outcome = _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert "resistance" not in rows
    assert rows["Resistance"].provider is None
    assert rows["ECCN"].archived_at is not None
    assert outcome.counts["unattributed"] == 1


def test_a_higher_precedence_provider_keeps_the_canonical_row(
    client: TestClient, db, tmp_path: Path
) -> None:
    """DigiKey already answered `resistance`; the bare Mouser-era alias
    must not overwrite it (ADR-0034 precedence)."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id)
    _legacy_row(
        db,
        ws_id=ws_id,
        part_id=part_id,
        key="resistance",
        value="10 kΩ",
        provider="digikey",
    )
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="47 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert rows["resistance"].value == "10 kΩ"
    assert rows["resistance"].provider == "digikey"


def test_a_part_carrying_both_spellings_ends_with_one_canonical_row(
    client: TestClient, db, tmp_path: Path
) -> None:
    """A part refreshed since A3 can carry `resistance` next to the
    `Resistance` its first import left. One provider, one answer: the
    vendor spelling wins the tie and its row is retired."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="resistance", value="10 kΩ")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="47 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert rows["resistance"].value == "47 kΩ"
    assert rows["resistance"].provider == "digikey"
    assert rows["Resistance"].archived_at is not None


# ---------------------------------------------------------------------------
# What the job must never touch
# ---------------------------------------------------------------------------
def test_manual_and_override_rows_are_left_alone(
    client: TestClient, db, tmp_path: Path
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(
        db,
        ws_id=ws_id,
        part_id=part_id,
        key="resistance",
        value="4k7 measured",
        source="manual",
    )
    _legacy_row(
        db,
        ws_id=ws_id,
        part_id=part_id,
        key="tolerance",
        value="0.1 %",
        source="override",
        provider="digikey",
        original_value="1 %",
    )
    # A junk key a user typed by hand is still the user's row.
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="ECCN", value="mine", source="manual")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert rows["resistance"].value == "4k7 measured"
    assert rows["resistance"].source == "manual"
    assert rows["resistance"].value_num is None
    assert rows["tolerance"].value == "0.1 %"
    assert rows["tolerance"].source == "override"
    assert rows["ECCN"].archived_at is None


def test_a_raw_alias_is_not_moved_onto_a_manual_canonical_row(
    client: TestClient, db, tmp_path: Path
) -> None:
    """The user owns `resistance`, so the provider's `Resistance` row stays
    exactly where it is — removing it would delete a spec the user can see
    today and write nothing in its place."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(
        db, ws_id=ws_id, part_id=part_id, key="resistance", value="4k7", source="manual"
    )
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="10 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert rows["resistance"].value == "4k7"
    assert rows["Resistance"].value == "10 kOhms"
    assert rows["Resistance"].archived_at is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_a_second_apply_changes_nothing(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    _normalize(db, tmp_path / "first.csv", apply=True)

    second = _normalize(db, tmp_path / "second.csv", apply=True)

    assert second.changes == 0
    assert _report_rows(tmp_path / "second.csv") == []


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
def test_a_part_with_no_category_is_filed_from_the_provider_taxonomy(
    client: TestClient, db, tmp_path: Path
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    category_id = _category(client, "Capacitors")
    ceramic_id = _category(client, "Ceramic", parent_id=category_id)
    part_id = _part(client, db, linked_provider="mouser")
    _legacy_row(
        db,
        ws_id=ws_id,
        part_id=part_id,
        key="mouser:category",
        value="Ceramic Capacitors MLCC - SMD/SMT",
    )
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    db.expire_all()
    assert str(db.get(Part, part_id).category_id) == ceramic_id


def test_an_existing_category_is_never_overridden(
    client: TestClient, db, tmp_path: Path
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    resistors_id = _category(client, "Resistors")
    _category(client, "Capacitors")
    part_id = _part(client, db, category_id=resistors_id, linked_provider="mouser")
    _legacy_row(
        db, ws_id=ws_id, part_id=part_id, key="category", value="Ceramic Capacitors"
    )
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    db.expire_all()
    assert str(db.get(Part, part_id).category_id) == resistors_id


def test_a_part_filed_under_a_root_still_gets_the_finer_schema(
    client: TestClient, db, tmp_path: Path
) -> None:
    """The sub-category seed has not run anywhere, so real parts sit under
    the "Capacitors" root — which classifies to nothing, because the
    dielectric is the spec set. The provider's own taxonomy still knows
    the part is a ceramic capacitor, and the backfill has to use it or it
    hands the common schema to most of the parts it exists to re-key."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    capacitors_id = _category(client, "Capacitors")
    part_id = _part(client, db, category_id=capacitors_id, linked_provider="mouser")
    _legacy_row(
        db, ws_id=ws_id, part_id=part_id, key="category", value="Ceramic Capacitors"
    )
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Dielectric", value="X7R")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Capacitance", value="100 nF")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _rows_by_key(db, part_id)
    assert rows["capacitance"].value == "100 nF"
    assert rows["dielectric"].value == "X7R"
    db.expire_all()
    assert str(db.get(Part, part_id).category_id) == capacitors_id


def test_the_assigned_category_picks_the_spec_schema_for_the_same_run(
    client: TestClient, db, tmp_path: Path
) -> None:
    """Category first, then specs — a part filed as a resistor in this run
    must have its `Resistance` re-keyed in this run too."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _category(client, "Resistors")
    part_id = _part(client, db, linked_provider="mouser")
    _legacy_row(
        db, ws_id=ws_id, part_id=part_id, key="category", value="Thick Film Resistors - SMD"
    )
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="10 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    assert _rows_by_key(db, part_id)["resistance"].value == "10 kΩ"


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------
@pytest.fixture
def two_workspaces(client: TestClient, db) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    ws_a, part_a = _one_resistor_workspace(client, db, "a@example.com")
    other = TestClient(app)
    ws_b, part_b = _one_resistor_workspace(other, db, "b@example.com")
    db.commit()
    return ws_a, part_a, ws_b, part_b


def _one_resistor_workspace(
    client: TestClient, db, email: str
) -> tuple[uuid.UUID, uuid.UUID]:
    ws_id = _signup(client, email=email)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="10 kOhms")
    return ws_id, part_id


def test_the_workspace_flag_scopes_the_run(
    two_workspaces: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    ws_a, part_a, _, part_b = two_workspaces

    _normalize(db, tmp_path / "r.csv", apply=True, workspace_id=ws_a)

    assert "resistance" in _rows_by_key(db, part_a)
    assert "resistance" not in _rows_by_key(db, part_b)
    assert "Resistance" in _rows_by_key(db, part_b)


def test_a_full_run_normalises_every_workspace(
    two_workspaces: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    ws_a, part_a, ws_b, part_b = two_workspaces

    _normalize(db, tmp_path / "r.csv", apply=True)

    assert "resistance" in _rows_by_key(db, part_a)
    assert "resistance" in _rows_by_key(db, part_b)
    reported = {row["workspace_id"] for row in _report_rows(tmp_path / "r.csv")}
    assert reported == {str(ws_a), str(ws_b)}


def test_one_audit_row_per_workspace_carries_counts_and_key_names_only(
    two_workspaces: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    ws_a, _, ws_b, _ = two_workspaces

    _normalize(db, tmp_path / "r.csv", apply=True)

    rows = list(
        db.execute(select(AuditLog).where(AuditLog.action == AUDIT_ACTION)).scalars()
    )
    assert {row.workspace_id for row in rows} == {ws_a, ws_b}
    for row in rows:
        assert "resistance" in row.comment
        assert "10 kOhms" not in row.comment
        assert "10 kΩ" not in row.comment


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def test_the_report_carries_the_agreed_columns(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    ws_id, part_id = resistor
    report = tmp_path / "report.csv"

    _normalize(db, report)

    rows = _report_rows(report)
    assert list(rows[0]) == list(REPORT_COLUMNS)
    rekey = next(r for r in rows if r["action"] == "rekey" and r["key"] == "resistance")
    assert rekey["workspace_id"] == str(ws_id)
    assert rekey["part_id"] == str(part_id)
    assert rekey["mpn"] == MPN
    assert rekey["old_key"] == "Resistance"
    assert rekey["old_value"] == "10 kOhms"
    assert rekey["new_value"] == "10 kΩ"
    assert rekey["provider"] == "digikey"
    assert {r["action"] for r in rows} >= {"rekey", "archive", "drop"}


def test_the_report_summarises_counts_and_unmapped_keys(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    ws_id, _ = resistor
    report = tmp_path / "report.csv"

    _normalize(db, report)

    text = report.read_text(encoding="utf-8")
    assert f"count,{ws_id},rekey," in text.replace(", ", ",") or f"count,{ws_id},rekey" in text
    # The unmapped tally is the list the alias table is extended from.
    assert "unmapped,resistor,Features,1" in text


def test_a_dry_run_with_no_report_file_writes_the_csv_to_stdout(
    resistor: tuple[uuid.UUID, uuid.UUID], db, capsys
) -> None:
    """`--report` is optional and its absence means stdout, not silence —
    the CLI contract every operator-run job shares. The apply path still
    requires a file, because a pipe is not a rollback record."""
    outcome = normalize_specs(db)

    assert outcome.changes > 0
    assert outcome.counts["rekey"] == 4
    printed = capsys.readouterr().out
    assert printed.startswith(",".join(REPORT_COLUMNS))
    assert "rekey,resistance,Resistance,digikey,10 kOhms,10 k\u03a9" in printed


def test_an_unknown_workspace_id_is_an_error_not_an_empty_run(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    """`parts=0 changes=0` after a typo'd `--workspace` reads exactly like
    "nothing left to do", which is the wrong thing to believe on the apply
    step."""
    with pytest.raises(UnknownWorkspaceError):
        _normalize(db, tmp_path / "r.csv", workspace_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# Idempotency traps found in review
# ---------------------------------------------------------------------------
def test_two_rows_stripping_to_one_key_do_not_resurface_on_the_next_run(
    client: TestClient, db, tmp_path: Path
) -> None:
    """A workspace that promoted Mouser from secondary to primary carries
    `Resistance` next to `mouser:Resistance`. Both strip to one payload
    key; only one can hold `resistance`. Leaving the other live would make
    it the sole answer next time, changing a value this run had already
    reported as final."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id)
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="47 kOhms")
    _legacy_row(
        db, ws_id=ws_id, part_id=part_id, key="mouser:Resistance", value="10 kOhms"
    )
    db.commit()

    _normalize(db, tmp_path / "first.csv", apply=True)
    first = _rows_by_key(db, part_id)["resistance"].value
    second = _normalize(db, tmp_path / "second.csv", apply=True)

    assert second.changes == 0
    assert _rows_by_key(db, part_id)["resistance"].value == first


def test_a_parsed_number_is_not_degraded_by_re_reading_its_own_display(
    client: TestClient, db, tmp_path: Path
) -> None:
    """`1/3W` displays as `333.3333 mW`, and the display has fewer
    significant digits than the raw value. Re-parsing it on the next run
    must not overwrite the number the first run read from `1/3W`."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Power (Watts)", value="1/3W")
    db.commit()

    _normalize(db, tmp_path / "first.csv", apply=True)
    first = _rows_by_key(db, part_id)["power"].value_num
    second = _normalize(db, tmp_path / "second.csv", apply=True)

    assert second.changes == 0
    assert _rows_by_key(db, part_id)["power"].value_num == first


def test_an_archived_junk_row_stays_archived_across_two_runs(
    client: TestClient, db, tmp_path: Path
) -> None:
    """Archived rows are invisible to every read path, and `_retire`
    skips them. A backfill that rewrote one in place would put a customs
    code back on the Specs tab — so a junk key that is already retired
    must stay retired, on this run and every run after it."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    already = _legacy_row(db, ws_id=ws_id, part_id=part_id, key="ECCN", value="EAR99")
    already.archived_at = utcnow()
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="10 kOhms")
    db.commit()
    retired_at = _rows_by_key(db, part_id)["ECCN"].archived_at

    _normalize(db, tmp_path / "first.csv", apply=True)
    second = _normalize(db, tmp_path / "second.csv", apply=True)

    assert second.changes == 0
    eccn = _rows_by_key(db, part_id)["ECCN"]
    assert eccn.archived_at == retired_at, "not re-retired, and never revived"
    assert eccn.value == "EAR99"
    # The run that could have touched it reported nothing about it either.
    assert not [
        r
        for r in _report_rows(tmp_path / "first.csv")
        if r["key"] == "ECCN" or r["old_key"] == "ECCN"
    ]


def test_an_archived_canonical_row_is_revived_only_by_a_real_value(
    client: TestClient, db, tmp_path: Path
) -> None:
    """The one row the job may un-archive: a canonical key a live vendor
    spelling now answers. `uq_cf_unique` has no partial predicate, so the
    archived row owns that key and there is nowhere else for the value to
    go — and what lands in it is a parsed spec, never junk."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    retired = _legacy_row(
        db, ws_id=ws_id, part_id=part_id, key="resistance", value="-"
    )
    retired.archived_at = utcnow()
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="47 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    resistance = _rows_by_key(db, part_id)["resistance"]
    assert resistance.archived_at is None
    assert resistance.value == "47 kΩ"
    assert resistance.provider == "digikey"


def test_a_display_that_rounds_does_not_zero_its_own_sidecar(
    client: TestClient, db, tmp_path: Path
) -> None:
    """`±0.00001%` displays as `0%`. Re-parsing that display on a second
    run would replace a sidecar of 0.00001 with zero, which is worse than
    no number at all — the partial index on `value_num` exists so one key
    sorts as one quantity."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Tolerance", value="±0.00001%")
    db.commit()

    _normalize(db, tmp_path / "first.csv", apply=True)
    first = _rows_by_key(db, part_id)["tolerance"].value_num
    second = _normalize(db, tmp_path / "second.csv", apply=True)

    assert float(first) == 0.00001
    assert second.changes == 0
    assert _rows_by_key(db, part_id)["tolerance"].value_num == first


def test_a_placeholder_row_a_real_value_fills_is_not_reported_as_dropped(
    client: TestClient, db, tmp_path: Path
) -> None:
    """`resistance = "-"` is retired only if nothing answers `resistance`.
    Here `Resistance` does, so the row is filled rather than archived —
    and the report must not claim a `drop` the run undid."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="resistance", value="-")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="47 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    resistance = _rows_by_key(db, part_id)["resistance"]
    assert resistance.value == "47 kΩ"
    assert resistance.archived_at is None
    actions = {r["action"] for r in _report_rows(tmp_path / "report.csv")}
    assert "drop" not in actions


def test_a_merge_reports_one_line_per_row_so_the_rollback_works(
    client: TestClient, db, tmp_path: Path
) -> None:
    """When a value moves onto a row that already holds the canonical key,
    that is two changes to two rows. The `rekey` line must describe only
    the row whose value moved — naming the other row's key as `old_key`
    would make the runbook's "rename it back" reversal collide with the
    archived row on `uq_cf_unique`."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "digikey")
    category_id = _category(client, "Resistors")
    part_id = _part(client, db, category_id=category_id, linked_provider="digikey")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="resistance", value="10 kΩ")
    _legacy_row(db, ws_id=ws_id, part_id=part_id, key="Resistance", value="47 kOhms")
    db.commit()

    _normalize(db, tmp_path / "report.csv", apply=True)

    rows = _report_rows(tmp_path / "report.csv")
    rekey = next(r for r in rows if r["action"] == "rekey")
    assert rekey["key"] == "resistance"
    assert rekey["old_key"] == "resistance"
    assert rekey["old_value"] == "10 kΩ"
    assert rekey["new_value"] == "47 kΩ"
    archived = next(r for r in rows if r["action"] == "archive")
    assert archived["key"] == "Resistance"
    assert archived["old_value"] == "47 kOhms"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
def test_a_second_run_that_finds_the_lock_held_does_nothing(
    resistor: tuple[uuid.UUID, uuid.UUID], db, engine, tmp_path: Path
) -> None:
    """`run_job`'s own lock is transaction-scoped and this job commits per
    batch, so the guard has to be a session-level one it takes itself."""
    _, part_id = resistor
    holder = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        holder.execute(
            text(
                "SELECT pg_advisory_lock("
                "CAST(:classid AS int4), CAST(hashtext(:key) AS int4))"
            ),
            {"classid": SPEC_NORMALIZE_LOCK_CLASSID, "key": "spec-normalize"},
        )

        outcome = _normalize(db, tmp_path / "r.csv", apply=True)
    finally:
        holder.execute(text("SELECT pg_advisory_unlock_all()"))
        holder.close()

    assert outcome.changes == 0
    assert "Resistance" in _rows_by_key(db, part_id)


def test_a_typo_in_the_workspace_flag_exits_non_zero(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path, capsys
) -> None:
    """Through the CLI, a `--workspace` that names nothing is exit 2 with a
    message — not a successful run that changed nothing."""
    _, part_id = resistor

    exit_code = main(
        [
            "spec-normalize",
            "--apply",
            "--workspace",
            str(uuid.uuid4()),
            "--report",
            str(tmp_path / "r.csv"),
        ],
        session_factory=lambda: db,
        heartbeat_dir=tmp_path / "heartbeats",
    )

    assert exit_code == 2
    assert "no workspace with id" in capsys.readouterr().err
    assert "Resistance" in _rows_by_key(db, part_id)


def test_the_job_runs_through_the_registry(
    resistor: tuple[uuid.UUID, uuid.UUID], db, tmp_path: Path
) -> None:
    """ADR-0021: every maintenance job is reachable as `run_job <name>`,
    and a bare invocation of this one is a dry run."""
    _, part_id = resistor

    affected = run_job(
        "spec-normalize",
        session_factory=lambda: db,
        heartbeat_dir=tmp_path / "heartbeats",
        options=BackfillOptions(report_path=tmp_path / "r.csv"),
    )

    assert affected > 0
    assert "Resistance" in _rows_by_key(db, part_id), "a bare run is a dry run"
