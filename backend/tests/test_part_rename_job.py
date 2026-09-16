"""The `part-rename` job — proposing, then applying, the naming convention.

A rename is the one maintenance job that rewrites something a user reads
every day, on a database with no staging copy behind it. So the rules
pinned here are mostly about restraint:

* a dry run is the default and writes **nothing** — not a name, not an
  alias, not an audit row;
* `--apply` renames and parks the text it would otherwise destroy in a
  `manual` `alias` custom field, and never overwrites an `alias` that is
  already there;
* a second run changes nothing, because every part it touched now
  classifies `canonical` or `mpn`;
* the audit trail carries counts, never names;
* `--workspace` means that workspace only.
"""
from __future__ import annotations

import csv
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.time import utcnow
from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.naming import ALIAS_CUSTOM_FIELD_KEY
from app.domain.parts.services.part_rename import (
    REPORT_COLUMNS,
    RenameOutcome,
    rename_parts,
)
from app.domain.workspaces.models import WorkspaceMember
from tests._factories import signup_user

RESISTOR_TEMPLATE = "{resistance} {tolerance} {package}"


def _workspace(client: TestClient) -> uuid.UUID:
    return uuid.UUID(signup_user(client).json()["data"]["workspace_id"])


def _category(client: TestClient, **body: Any) -> uuid.UUID:
    payload: dict[str, Any] = {"name": f"Cat-{uuid.uuid4().hex[:8]}"}
    payload.update(body)
    response = client.post("/api/categories", json=payload)
    assert response.status_code in (200, 201), response.text
    return uuid.UUID(response.json()["data"]["id"])


def _part(
    db: Any,
    workspace_id: uuid.UUID,
    name: str,
    *,
    specs: dict[str, str] | None = None,
    **columns: Any,
) -> Part:
    part = Part(
        workspace_id=workspace_id,
        part_type="linked",
        name=name,
        **columns,
    )
    db.add(part)
    db.flush()
    for key, value in (specs or {}).items():
        db.add(
            CustomField(
                workspace_id=workspace_id,
                object_type="part",
                object_id=part.id,
                key=key,
                value=value,
                source="provider",
            )
        )
    db.flush()
    return part


def _alias(db: Any, part: Part) -> CustomField | None:
    return db.execute(
        select(CustomField)
        .where(CustomField.workspace_id == part.workspace_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id == part.id)
        .where(CustomField.key == ALIAS_CUSTOM_FIELD_KEY)
    ).scalars().first()


def _rename_audit_rows(db: Any) -> list[AuditLog]:
    return (
        db.execute(select(AuditLog).where(AuditLog.action == "part.bulk_renamed"))
        .scalars()
        .all()
    )


def _report_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def report_path(tmp_path: Path) -> Path:
    return tmp_path / "part-rename.csv"


def _run(db: Any, report_path: Path, **kwargs: Any) -> RenameOutcome:
    """Run the job with its CSV going to `report_path`.

    The stream belongs to the caller — in production that is
    `run_job._report_stream`, which also owns the 0700 directory and the
    0600 file. Here it is just a file the assertions can read back.
    """
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        return rename_parts(db, stream=handle, **kwargs)


# ---------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------


def test_a_dry_run_proposes_and_writes_nothing(authed_client, db, report_path):
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(
        db,
        workspace_id,
        "RES SMD 10K OHM 1% 1/16W 0603",
        mpn="RC0603FR-0710KL",
        description="RES SMD 10K OHM 1% 1/16W 0603",
    )

    # Act
    outcome = _run(db, report_path)
    db.flush()

    # Assert
    assert outcome.applied is False
    assert outcome.counts.renamed == 1
    assert part.name == "RES SMD 10K OHM 1% 1/16W 0603"
    assert _alias(db, part) is None
    assert _rename_audit_rows(db) == []


def test_the_report_names_every_proposal_and_its_class(
    authed_client, db, report_path
):
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(
        db,
        workspace_id,
        "STM32F103C8T6 - Servo integrator",
        mpn="STM32F103C8T6",
    )

    # Act
    _run(db, report_path)

    # Assert
    rows = _report_rows(report_path)
    assert list(rows[0]) == list(REPORT_COLUMNS)
    assert rows[0]["part_id"] == str(part.id)
    assert rows[0]["workspace_id"] == str(workspace_id)
    assert rows[0]["mpn"] == "STM32F103C8T6"
    assert rows[0]["old_name"] == "STM32F103C8T6 - Servo integrator"
    assert rows[0]["new_name"] == "STM32F103C8T6"
    assert rows[0]["class"] == "role_suffix"
    assert rows[0]["alias_written"] == "Servo integrator"


def test_parts_already_holding_the_right_name_stay_out_of_the_report(
    authed_client, db, report_path
):
    """The report is a change list. A catalogue that is already converted
    produces an empty one, which is how a second run is read."""
    # Arrange
    workspace_id = _workspace(authed_client)
    _part(db, workspace_id, "STM32F103C8T6", mpn="STM32F103C8T6")

    # Act
    outcome = _run(db, report_path)

    # Assert
    assert outcome.counts.renamed == 0
    assert _report_rows(report_path) == []


def test_a_part_whose_template_cannot_render_is_counted_and_falls_back(
    authed_client, db, report_path
):
    """The prod state: the category has a template, the part's specs are
    still under the provider's key names, so the MPN stands. The count is
    how much the spec backfill still owes."""
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(
        authed_client,
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )
    _part(
        db,
        workspace_id,
        "RES SMD 10K OHM 1% 1/16W 0603",
        mpn="RC0603FR-0710KL",
        description="RES SMD 10K OHM 1% 1/16W 0603",
        category_id=category_id,
        specs={"Resistance": "10 kOhms"},
    )

    # Act
    outcome = _run(db, report_path)

    # Assert
    assert outcome.counts.template_unrenderable == 1
    assert _report_rows(report_path)[0]["new_name"] == "RC0603FR-0710KL"


def test_archived_parts_are_not_considered(authed_client, db, report_path):
    # Arrange
    workspace_id = _workspace(authed_client)
    _part(
        db,
        workspace_id,
        "RES SMD 10K OHM 1% 1/16W 0603",
        mpn="RC0603FR-0710KL",
        archived_at=utcnow(),
    )

    # Act
    outcome = _run(db, report_path)

    # Assert
    assert outcome.counts.considered == 0
    assert _report_rows(report_path) == []


# ---------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------


def test_apply_renames_the_part_and_keeps_the_role_as_an_alias(
    authed_client, db, report_path
):
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(
        db,
        workspace_id,
        "STM32F103C8T6 - Servo integrator + bias monitor",
        mpn="STM32F103C8T6",
    )

    # Act
    outcome = _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert outcome.applied is True
    assert outcome.counts.renamed == 1
    assert outcome.counts.alias_written == 1
    assert part.name == "STM32F103C8T6"
    alias = _alias(db, part)
    assert alias is not None
    assert alias.value == "Servo integrator + bias monitor"
    assert alias.source == "manual"


def test_apply_renames_a_passive_to_its_canonical_name(
    authed_client, db, report_path
):
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(
        authed_client,
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )
    part = _part(
        db,
        workspace_id,
        "RES SMD 10K OHM 1% 1/16W 0603",
        mpn="RC0603FR-0710KL",
        description="RES SMD 10K OHM 1% 1/16W 0603",
        category_id=category_id,
        specs={"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"},
    )

    # Act
    _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert part.name == "R 10 kΩ 1% 0603"
    # `description` is provider-owned and a refresh rewrites it, so the
    # old name is parked rather than trusted to that column.
    assert _alias(db, part).value == "RES SMD 10K OHM 1% 1/16W 0603"


def test_apply_keeps_hand_typed_names_whole_in_the_alias(
    authed_client, db, report_path
):
    """A free-text name is the only copy of what somebody wrote — unlike
    a description name, which its own column keeps."""
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(
        db, workspace_id, "1k 1% 0402 - TL431 ref feed R", mpn="RC0402FR-071KL"
    )

    # Act
    _run(db, report_path, apply=True, include_free=True)
    db.flush()

    # Assert
    assert part.name == "RC0402FR-071KL"
    assert _alias(db, part).value == "1k 1% 0402 - TL431 ref feed R"


def test_apply_never_overwrites_an_alias_that_is_already_there(
    authed_client, db, report_path
):
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(db, workspace_id, "MPN-1 - bias divider", mpn="MPN-1")
    db.add(
        CustomField(
            workspace_id=workspace_id,
            object_type="part",
            object_id=part.id,
            key=ALIAS_CUSTOM_FIELD_KEY,
            value="the operator's own alias",
            source="manual",
        )
    )
    db.flush()

    # Act
    outcome = _run(db, report_path, apply=True)
    db.flush()

    # Assert — not renamed at all. The alias slot is taken, so the role
    # in the old name would have gone nowhere.
    assert part.name == "MPN-1 - bias divider"
    assert _alias(db, part).value == "the operator's own alias"
    assert outcome.counts.renamed == 0
    assert outcome.counts.skipped_alias_conflict == 1
    row = _report_rows(report_path)[0]
    assert row["new_name"] == ""
    assert row["skip_reason"] == "alias_conflict"


def test_an_archived_alias_row_also_blocks_the_write(
    authed_client, db, report_path
):
    """`uq_cf_unique` has no `archived_at` predicate, so an archived row
    still owns the key — inserting a second one is an IntegrityError, not
    an overwrite."""
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(db, workspace_id, "MPN-1 - bias divider", mpn="MPN-1")
    db.add(
        CustomField(
            workspace_id=workspace_id,
            object_type="part",
            object_id=part.id,
            key=ALIAS_CUSTOM_FIELD_KEY,
            value="archived alias",
            source="manual",
            archived_at=utcnow(),
        )
    )
    db.flush()

    # Act
    outcome = _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert part.name == "MPN-1 - bias divider"
    assert outcome.counts.renamed == 0
    assert outcome.counts.skipped_alias_conflict == 1


def test_a_second_apply_changes_nothing(authed_client, db, report_path):
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(
        db, workspace_id, "STM32F103C8T6 - Servo integrator", mpn="STM32F103C8T6"
    )
    _run(db, report_path, apply=True)
    db.flush()

    # Act
    second = _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert second.counts.renamed == 0
    assert second.counts.alias_written == 0
    assert part.name == "STM32F103C8T6"
    assert _alias(db, part).value == "Servo integrator"
    assert _report_rows(report_path) == []


def test_apply_writes_one_audit_row_per_workspace_carrying_counts_only(
    authed_client, db, report_path
):
    # Arrange
    workspace_id = _workspace(authed_client)
    secret_name = "1k 1% 0402 - TL431 ref feed R"
    _part(db, workspace_id, secret_name, mpn="RC0402FR-071KL")
    _part(db, workspace_id, "MPN-2 - role", mpn="MPN-2")

    # Act
    _run(db, report_path, apply=True, include_free=True)
    db.flush()

    # Assert
    rows = _rename_audit_rows(db)
    assert len(rows) == 1
    assert rows[0].workspace_id == workspace_id
    assert "renamed=2" in rows[0].comment
    # Names are user data and a rename is a bulk event — the report file
    # carries the detail, the audit row carries the fact.
    assert secret_name not in rows[0].comment
    assert "MPN-2" not in rows[0].comment


def test_a_workspace_with_nothing_to_rename_writes_no_audit_row(
    authed_client, db, report_path
):
    # Arrange
    workspace_id = _workspace(authed_client)
    _part(db, workspace_id, "STM32F103C8T6", mpn="STM32F103C8T6")

    # Act
    _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert _rename_audit_rows(db) == []


# ---------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------


def test_every_workspace_is_swept_when_none_is_named(authed_client, db, report_path):
    # Arrange
    workspace_a = _workspace(authed_client)
    other = TestClient(authed_client.app)
    workspace_b = _workspace(other)
    part_a = _part(db, workspace_a, "A-1 - role", mpn="A-1")
    part_b = _part(db, workspace_b, "B-1 - role", mpn="B-1")

    # Act
    outcome = _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert outcome.counts.renamed == 2
    assert (part_a.name, part_b.name) == ("A-1", "B-1")


def test_naming_one_workspace_leaves_the_others_alone(
    authed_client, db, report_path
):
    # Arrange
    workspace_a = _workspace(authed_client)
    other = TestClient(authed_client.app)
    workspace_b = _workspace(other)
    part_a = _part(db, workspace_a, "A-1 - role", mpn="A-1")
    part_b = _part(db, workspace_b, "B-1 - role", mpn="B-1")

    # Act
    outcome = _run(db, report_path, apply=True, workspace_id=workspace_a)
    db.flush()

    # Assert
    assert outcome.counts.renamed == 1
    assert part_a.name == "A-1"
    assert part_b.name == "B-1 - role"
    assert {row["workspace_id"] for row in _report_rows(report_path)} == {
        str(workspace_a)
    }


def test_more_parts_than_one_batch_are_all_swept(authed_client, db, report_path, monkeypatch):
    """Keyset pagination, not OFFSET: the sweep reads at READ COMMITTED
    and a part created between two batches would shift every later
    offset and push one part out of the run."""
    # Arrange
    monkeypatch.setattr(
        "app.domain.parts.services.part_rename._BATCH_SIZE", 2
    )
    workspace_id = _workspace(authed_client)
    parts = [
        _part(db, workspace_id, f"MPN-{index} - role", mpn=f"MPN-{index}")
        for index in range(5)
    ]

    # Act
    outcome = _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert outcome.counts.considered == 5
    assert outcome.counts.renamed == 5
    assert [part.name for part in parts] == [f"MPN-{index}" for index in range(5)]


def test_the_recovery_columns_are_verbatim_and_the_rest_is_neutralised(
    authed_client, db, report_path
):
    """`old_name` and `alias_written` are the off-database copy of text
    the rename overwrites, so an apostrophe glued to the front of them
    would corrupt the thing they exist to preserve. Every other column
    is neutralised, because this file gets opened in a spreadsheet."""
    # Arrange
    workspace_id = _workspace(authed_client)
    _part(
        db,
        workspace_id,
        '=HYPERLINK("http://evil","click") - role',
        mpn="=CMD|calc",
    )

    # Act
    _run(db, report_path, include_free=True)

    # Assert
    row = _report_rows(report_path)[0]
    assert row["old_name"] == '=HYPERLINK("http://evil","click") - role'
    assert row["alias_written"] == '=HYPERLINK("http://evil","click") - role'
    assert row["mpn"] == "'=CMD|calc"


# ---------------------------------------------------------------------
# Free-text names, which are somebody's deliberate choice
# ---------------------------------------------------------------------


def test_a_free_text_name_is_reported_but_not_renamed(
    authed_client, db, report_path
):
    """The default. A hand-typed name is the one class the import did not
    produce, so it is presumed meant."""
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(
        db, workspace_id, "1k 1% 0402 - TL431 ref feed R", mpn="RC0402FR-071KL"
    )

    # Act
    outcome = _run(db, report_path, apply=True)
    db.flush()

    # Assert — untouched, and listed with the reason so the operator can
    # decide from the report whether to opt in.
    assert part.name == "1k 1% 0402 - TL431 ref feed R"
    assert outcome.counts.renamed == 0
    assert outcome.counts.skipped_free == 1
    row = _report_rows(report_path)[0]
    assert row["class"] == "free"
    assert row["new_name"] == ""
    assert row["skip_reason"] == "free_excluded"


def test_include_free_opts_into_renaming_them(authed_client, db, report_path):
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(
        db, workspace_id, "1k 1% 0402 - TL431 ref feed R", mpn="RC0402FR-071KL"
    )

    # Act
    outcome = _run(db, report_path, apply=True, include_free=True)
    db.flush()

    # Assert
    assert part.name == "RC0402FR-071KL"
    assert outcome.counts.skipped_free == 0
    assert _alias(db, part).value == "1k 1% 0402 - TL431 ref feed R"


def test_a_free_name_with_an_alias_already_taken_is_never_renamed(
    authed_client, db, report_path
):
    """The data-loss case: opted in, but the one slot that would have
    held the old text is occupied. The name stays."""
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(db, workspace_id, "hand-typed name", mpn="MPN-9")
    db.add(
        CustomField(
            workspace_id=workspace_id,
            object_type="part",
            object_id=part.id,
            key=ALIAS_CUSTOM_FIELD_KEY,
            value="the operator's own alias",
            source="manual",
        )
    )
    db.flush()

    # Act
    outcome = _run(db, report_path, apply=True, include_free=True)
    db.flush()

    # Assert
    assert part.name == "hand-typed name"
    assert _alias(db, part).value == "the operator's own alias"
    assert outcome.counts.renamed == 0
    assert outcome.counts.skipped_alias_conflict == 1
    assert _report_rows(report_path)[0]["skip_reason"] == "alias_conflict"


def test_a_description_name_with_an_alias_taken_is_renamed_and_says_so(
    authed_client, db, report_path
):
    """The one case where an occupied alias slot still allows the rename:
    the old name is the `description`, which still holds it. The report
    says so, because that column is provider-owned and a refresh can
    rewrite it."""
    # Arrange
    workspace_id = _workspace(authed_client)
    description = "RES SMD 10K OHM 1% 1/16W 0603"
    part = _part(
        db, workspace_id, description, mpn="RC0603FR-0710KL", description=description
    )
    db.add(
        CustomField(
            workspace_id=workspace_id,
            object_type="part",
            object_id=part.id,
            key=ALIAS_CUSTOM_FIELD_KEY,
            value="the operator's own alias",
            source="manual",
        )
    )
    db.flush()

    # Act
    outcome = _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert part.name == "RC0603FR-0710KL"
    assert part.description == description
    assert outcome.counts.renamed == 1
    assert outcome.counts.skipped_alias_conflict == 1
    row = _report_rows(report_path)[0]
    assert row["old_name_preserved_in"] == "description"
    assert row["skip_reason"] == "alias_conflict"


def test_the_report_says_where_every_old_name_went(authed_client, db, report_path):
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(
        authed_client,
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )
    specs = {"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"}
    # Named by its MPN: the column keeps it.
    _part(
        db,
        workspace_id,
        "RC0603FR-0710KL",
        mpn="RC0603FR-0710KL",
        category_id=category_id,
        specs=specs,
    )
    # Named by a role: the role goes to the alias.
    _part(db, workspace_id, "MPN-7 - bias", mpn="MPN-7")

    # Act
    _run(db, report_path)

    # Assert
    preserved = {
        row["mpn"]: row["old_name_preserved_in"] for row in _report_rows(report_path)
    }
    assert preserved == {"RC0603FR-0710KL": "mpn", "MPN-7": "alias"}


def test_the_report_survives_a_failure_part_way_through(
    authed_client, db, report_path, monkeypatch
):
    """An apply run that has already mutated rows and then raises must
    still leave the operator the list of what it was doing."""
    # Arrange
    workspace_id = _workspace(authed_client)
    _part(db, workspace_id, "MPN-5 - role", mpn="MPN-5")
    boom = RuntimeError("constraint")

    def _explode(*args, **kwargs):
        raise boom

    monkeypatch.setattr(
        "app.domain.parts.services.part_rename.audit_log", _explode
    )

    # Act
    with pytest.raises(RuntimeError):
        _run(db, report_path, apply=True)

    # Assert
    assert _report_rows(report_path)[0]["new_name"] == "MPN-5"
def test_a_rename_does_not_credit_itself_to_the_last_human_editor(
    authed_client, db, report_path
):
    """No user ran this. Leaving `updated_by` alone would put the last
    person who touched the part next to a change they did not make."""
    # Arrange
    workspace_id = _workspace(authed_client)
    editor = db.execute(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id
        )
    ).scalars().first()
    assert editor is not None
    part = _part(db, workspace_id, "MPN-8 - role", mpn="MPN-8")
    part.updated_by = editor
    db.flush()

    # Act
    _run(db, report_path, apply=True)
    db.flush()

    # Assert
    assert part.name == "MPN-8"
    assert part.updated_by is None
