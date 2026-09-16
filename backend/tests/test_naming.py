"""`domain/parts/naming.py` — what a part's `name` is supposed to say.

The convention: `name` is the part's canonical identity, never project
prose. A part whose category carries a `value_template` gets the
rendered template behind its class letter (`R 10 kΩ 1% 0603`);
everything else gets its MPN. What is pinned here:

* the class letter comes from `refdes_prefix`, and inherits from the
  nearest ancestor category the same way the template does;
* a template that renders nothing — which is every prod passive until
  the spec backfill re-keys the provider rows — is `None`, not a half
  name, so the caller falls back to the MPN;
* `{mpn}` renders bare, because `U STM32F103C8T6` is noise;
* classification is the rename job's input, so each of the five classes
  has to be reachable and stable;
* none of it reads another workspace's categories or specs.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.categories.models import PartCategory
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.naming import (
    NAME_MAX_LENGTH,
    canonical_name,
    canonical_names,
    classify_name,
    propose_rename,
)
from app.domain.parts.services.provider_import import (
    create_from_provider_lookup,
)
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
    *,
    specs: dict[str, str] | None = None,
    **columns: Any,
) -> Part:
    part = Part(
        workspace_id=workspace_id,
        part_type=columns.pop("part_type", "linked"),
        name=columns.pop("name", "unnamed"),
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


# ---------------------------------------------------------------------
# canonical_name
# ---------------------------------------------------------------------


def test_a_passive_is_named_class_letter_then_rendered_template(authed_client, db):
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
        mpn="RC0603FR-0710KL",
        category_id=category_id,
        specs={"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"},
    )

    # Act
    name = canonical_name(db, workspace_id=workspace_id, part=part)

    # Assert
    assert name == "R 10 kΩ 1% 0603"


def test_specs_too_thin_to_render_the_template_give_no_canonical_name(
    authed_client, db
):
    """The prod case until the spec backfill runs: the rows are there but
    under the provider's own key names, so nothing the template asks for
    resolves. The caller falls back to the MPN rather than naming a
    resistor `R`."""
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
        mpn="RC0603FR-0710KL",
        category_id=category_id,
        specs={"Resistance": "10 kOhms", "Tolerance": "±1%"},
    )

    # Act
    name = canonical_name(db, workspace_id=workspace_id, part=part)

    # Assert
    assert name is None


def test_a_part_with_no_category_has_no_canonical_name(authed_client, db):
    # Arrange
    workspace_id = _workspace(authed_client)
    part = _part(db, workspace_id, mpn="STM32F103C8T6")

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) is None


def test_a_category_with_no_template_has_no_canonical_name(authed_client, db):
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(authed_client, name="Connectors", refdes_prefix="J")
    part = _part(db, workspace_id, mpn="1734035-1", category_id=category_id)

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) is None


def test_the_mpn_template_renders_without_a_class_letter(authed_client, db):
    """`{mpn}` is the sensible default template for everything that is
    not a passive, and `U STM32F103C8T6` is not a better name than the
    part number."""
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(
        authed_client,
        name="Microcontrollers",
        refdes_prefix="U",
        value_template="{mpn}",
    )
    part = _part(db, workspace_id, mpn="STM32F103C8T6", category_id=category_id)

    # Act / Assert
    assert (
        canonical_name(db, workspace_id=workspace_id, part=part) == "STM32F103C8T6"
    )


def test_a_template_that_cannot_render_whole_is_no_name_at_all(authed_client, db):
    """`render_value` drops a missing spec so a KiCad `Value` still reads
    well half-specified. A name cannot: `{resistance} {tolerance}
    {package}` with only `package` renders `0603`, and naming every
    half-specified resistor `R 0603` is worse than naming it by its part
    number."""
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
        mpn="RC0603FR-0710KL",
        category_id=category_id,
        specs={"resistance": "10 kΩ", "package": "0603"},
    )

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) is None


def test_one_spec_short_is_still_no_name(authed_client, db):
    """The shape the rename job would otherwise act on at scale."""
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
        mpn="RC0603FR-0710KL",
        category_id=category_id,
        specs={"package": "0603"},
    )

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) is None


def test_an_mpn_template_on_a_part_without_one_renders_nothing(authed_client, db):
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(
        authed_client, name="Modules", refdes_prefix="U", value_template="{mpn}"
    )
    part = _part(db, workspace_id, name="Hand-built jig", category_id=category_id)

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) is None


def test_template_and_class_letter_both_inherit_from_an_ancestor(authed_client, db):
    """A template set on *Capacitors* covers *Capacitors / Ceramic*, and
    so does the `C`. Neither has to be repeated on every leaf."""
    # Arrange
    workspace_id = _workspace(authed_client)
    parent_id = _category(
        authed_client,
        name="Capacitors",
        refdes_prefix="C",
        value_template="{capacitance} {voltage_rating} {dielectric} {package}",
    )
    child_id = _category(authed_client, name="Ceramic", parent_id=str(parent_id))
    part = _part(
        db,
        workspace_id,
        mpn="CL10B105KA8NNNC",
        category_id=child_id,
        specs={
            "capacitance": "1 µF",
            "voltage_rating": "50 V",
            "dielectric": "X7R",
            "package": "0805",
        },
    )

    # Act / Assert
    assert (
        canonical_name(db, workspace_id=workspace_id, part=part)
        == "C 1 µF 50 V X7R 0805"
    )


def test_a_child_class_letter_overrides_its_parents(authed_client, db):
    # Arrange
    workspace_id = _workspace(authed_client)
    parent_id = _category(
        authed_client,
        name="Diodes",
        refdes_prefix="D",
        value_template="{vz} {power}",
    )
    child_id = _category(
        authed_client, name="Zener", refdes_prefix="DZ", parent_id=str(parent_id)
    )
    part = _part(
        db,
        workspace_id,
        mpn="BZX84C5V1",
        category_id=child_id,
        specs={"vz": "5.1 V", "power": "250 mW"},
    )

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) == "DZ 5.1 V 250 mW"


def test_a_category_without_a_class_letter_renders_the_bare_template(
    authed_client, db
):
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(
        authed_client, name="Resistors", value_template=RESISTOR_TEMPLATE
    )
    part = _part(
        db,
        workspace_id,
        mpn="RC0603FR-0710KL",
        category_id=category_id,
        specs={"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"},
    )

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) == "10 kΩ 1% 0603"


def test_canonical_names_reports_which_templates_could_not_render(authed_client, db):
    """The rename job separates "no rule for this part" from "there is a
    rule and the part cannot satisfy it" — the second is the count that
    says how much the spec backfill still owes."""
    # Arrange
    workspace_id = _workspace(authed_client)
    category_id = _category(
        authed_client,
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )
    thin = _part(db, workspace_id, mpn="THIN-1", category_id=category_id)
    uncategorised = _part(db, workspace_id, mpn="UNCAT-1")

    # Act
    results = canonical_names(
        db, workspace_id=workspace_id, parts=[thin, uncategorised]
    )

    # Assert
    assert results[thin.id].unrenderable is True
    assert results[uncategorised.id].unrenderable is False
    assert results[thin.id].name is None


def test_each_workspace_names_with_its_own_class_letter(authed_client, db):
    """Both lookups run a workspace-wide category query — the prefix walk
    and the template walk. Two workspaces holding the same category name
    and template under different letters is what catches either one
    losing its `workspace_id` filter."""
    # Arrange
    workspace_a = _workspace(authed_client)
    other = TestClient(authed_client.app)
    workspace_b = _workspace(other)
    specs = {"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"}
    category_a = _category(
        authed_client,
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )
    category_b = _category(
        other,
        name="Resistors",
        refdes_prefix="RB",
        value_template=RESISTOR_TEMPLATE,
    )
    part_a = _part(db, workspace_a, mpn="A-1", category_id=category_a, specs=specs)
    part_b = _part(db, workspace_b, mpn="B-1", category_id=category_b, specs=specs)

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_a, part=part_a) == "R 10 kΩ 1% 0603"
    assert (
        canonical_name(db, workspace_id=workspace_b, part=part_b) == "RB 10 kΩ 1% 0603"
    )


def test_a_part_from_another_workspace_is_skipped_not_named(authed_client, db):
    """`parts.category_id` is trigger-pinned to the part's own workspace
    (migration 0036), so the only way a foreign part reaches this code is
    a caller passing the wrong `workspace_id`. It gets nothing back
    rather than workspace B's answer."""
    # Arrange
    workspace_a = _workspace(authed_client)
    other = TestClient(authed_client.app)
    workspace_b = _workspace(other)
    category_b = _category(
        other, name="Resistors", refdes_prefix="R", value_template=RESISTOR_TEMPLATE
    )
    intruder = _part(
        db,
        workspace_b,
        mpn="B-2",
        category_id=category_b,
        specs={"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"},
    )

    # Act
    results = canonical_names(db, workspace_id=workspace_a, parts=[intruder])

    # Assert
    assert results == {}
    assert canonical_name(db, workspace_id=workspace_b, part=intruder) == (
        "R 10 kΩ 1% 0603"
    )


def test_archived_categories_do_not_name_their_parts(authed_client, db):
    """An archived category is no category at all on this surface — the
    same rule the KiCad library applies."""
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
        mpn="RC0603FR-0710KL",
        category_id=category_id,
        specs={"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"},
    )
    archived = authed_client.post(f"/api/categories/{category_id}/archive")
    assert archived.status_code == 200, archived.text

    # Act / Assert
    assert canonical_name(db, workspace_id=workspace_id, part=part) is None


# ---------------------------------------------------------------------
# classify_name
# ---------------------------------------------------------------------


def _bare_part(**columns: Any) -> Part:
    return Part(workspace_id=uuid.uuid4(), part_type="linked", **columns)


@pytest.mark.parametrize(
    ("name", "canonical", "expected"),
    [
        ("R 10 kΩ 1% 0603", "R 10 kΩ 1% 0603", "canonical"),
        ("RC0603FR-0710KL", "R 10 kΩ 1% 0603", "mpn"),
        ("RES SMD 10K OHM 1% 1/16W 0603", "R 10 kΩ 1% 0603", "description"),
        ("R 10 kΩ 1% 0603 - bias divider", "R 10 kΩ 1% 0603", "role_suffix"),
        ("1k 1% 0402 - TL431 ref feed R", "R 10 kΩ 1% 0603", "free"),
    ],
)
def test_every_classification_is_reachable(name, canonical, expected):
    # Arrange
    part = _bare_part(
        name=name,
        mpn="RC0603FR-0710KL",
        description="RES SMD 10K OHM 1% 1/16W 0603",
    )

    # Act / Assert
    assert classify_name(part, canonical) == expected


def test_a_role_suffix_is_recognised_behind_the_mpn_too():
    """Most prod parts have no renderable canonical name yet, so the
    head of a ` - role` name is the MPN."""
    # Arrange
    part = _bare_part(
        name="STM32F103C8T6 - Servo integrator + bias monitor",
        mpn="STM32F103C8T6",
    )

    # Act / Assert
    assert classify_name(part, None) == "role_suffix"


def test_a_name_that_is_only_the_separator_is_free_text():
    """`X - ` has no role behind it, so there is nothing to preserve and
    nothing to call a suffix."""
    # Arrange
    part = _bare_part(name="STM32F103C8T6 - ", mpn="STM32F103C8T6")

    # Act / Assert
    assert classify_name(part, None) == "free"


def test_classification_ignores_surrounding_whitespace():
    # Arrange
    part = _bare_part(name="  RC0603FR-0710KL  ", mpn="RC0603FR-0710KL")

    # Act / Assert
    assert classify_name(part, None) == "mpn"


# ---------------------------------------------------------------------
# propose_rename
# ---------------------------------------------------------------------


def test_a_part_already_holding_its_canonical_name_is_left_alone():
    # Arrange
    part = _bare_part(name="R 10 kΩ 1% 0603", mpn="RC0603FR-0710KL")

    # Act
    proposal = propose_rename(part, "R 10 kΩ 1% 0603")

    # Assert
    assert proposal.classification == "canonical"
    assert proposal.new_name is None
    assert proposal.alias is None


def test_a_description_name_is_still_parked_in_the_alias():
    """`description` looks self-preserving and is not: it is
    provider-owned on a linked part, so the next refresh rewrites it and
    the old name would exist nowhere."""
    # Arrange
    part = _bare_part(
        name="RES SMD 10K OHM 1% 1/16W 0603",
        mpn="RC0603FR-0710KL",
        description="RES SMD 10K OHM 1% 1/16W 0603",
    )

    # Act
    proposal = propose_rename(part, "R 10 kΩ 1% 0603")

    # Assert
    assert proposal.classification == "description"
    assert proposal.new_name == "R 10 kΩ 1% 0603"
    assert proposal.alias == "RES SMD 10K OHM 1% 1/16W 0603"
    assert proposal.preserved_in == "alias"


def test_a_name_that_is_the_mpn_needs_no_alias():
    """The one class where the old name has a column of its own."""
    # Arrange
    part = _bare_part(name="RC0603FR-0710KL", mpn="RC0603FR-0710KL")

    # Act
    proposal = propose_rename(part, "R 10 kΩ 1% 0603")

    # Assert
    assert proposal.new_name == "R 10 kΩ 1% 0603"
    assert proposal.alias is None
    assert proposal.preserved_in == "mpn"


def test_a_truncated_description_is_still_a_description_name():
    """The import this replaces cut the description to the 300-char
    column, so a long one left a name matching only its head."""
    # Arrange
    description = "RES SMD " + "X" * 400
    part = _bare_part(
        name=description[:300], mpn="RC0603FR-0710KL", description=description
    )

    # Act / Assert
    assert classify_name(part, None) == "description"


def test_a_part_number_too_long_for_the_column_is_skipped_not_truncated():
    """Unreachable while `parts.mpn` is String(200); here so that
    widening it produces a skipped row and not a DataError at flush."""
    # Arrange
    part = _bare_part(name="something else", mpn="M" * (NAME_MAX_LENGTH + 1))

    # Act
    proposal = propose_rename(part, None)

    # Assert
    assert proposal.new_name is None
    assert proposal.skip_reason == "name_too_long"


def test_a_role_suffix_keeps_the_role_as_the_alias():
    # Arrange
    part = _bare_part(
        name="STM32F103C8T6 - Servo integrator + bias monitor",
        mpn="STM32F103C8T6",
    )

    # Act
    proposal = propose_rename(part, None)

    # Assert
    assert proposal.new_name == "STM32F103C8T6"
    assert proposal.alias == "Servo integrator + bias monitor"
    assert proposal.preserved_in == "alias"


def test_free_text_is_kept_whole_as_the_alias():
    """A hand-typed name has no other column holding it, so the rename
    carries all of it across or it is gone."""
    # Arrange
    part = _bare_part(name="1k 1% 0402 - TL431 ref feed R", mpn="RC0402FR-071KL")

    # Act
    proposal = propose_rename(part, None)

    # Assert
    assert proposal.classification == "free"
    assert proposal.new_name == "RC0402FR-071KL"
    assert proposal.alias == "1k 1% 0402 - TL431 ref feed R"


def test_a_part_with_nothing_to_be_renamed_to_is_left_alone():
    """No category rule and no MPN: a local part's hand-typed name is
    the only identity it has."""
    # Arrange
    part = _bare_part(name="Front panel bracket", mpn=None)

    # Act
    proposal = propose_rename(part, None)

    # Assert
    assert proposal.classification == "free"
    assert proposal.new_name is None
    assert proposal.alias is None


def test_the_canonical_name_wins_over_the_mpn_as_the_target():
    # Arrange
    part = _bare_part(name="RC0603FR-0710KL", mpn="RC0603FR-0710KL")

    # Act
    proposal = propose_rename(part, "R 10 kΩ 1% 0603")

    # Assert
    assert proposal.classification == "mpn"
    assert proposal.new_name == "R 10 kΩ 1% 0603"


# ---------------------------------------------------------------------
# The convention at creation time
# ---------------------------------------------------------------------


def test_a_provider_import_names_the_part_by_its_mpn_not_its_description(
    authed_client, db
):
    """127 prod parts are named by the provider's marketing copy because
    this defaulted to `description`. The copy belongs in `description`,
    which keeps it."""
    # Arrange
    workspace_id = _workspace(authed_client)

    # Act
    outcome = create_from_provider_lookup(
        db,
        workspace_id=workspace_id,
        user_id=None,
        provider_name="mouser",
        mpn="RC0603FR-0710KL",
        lookup_result={
            "mpn": "RC0603FR-0710KL",
            "manufacturer": "Yageo",
            "description": "RES SMD 10K OHM 1% 1/16W 0603",
            "specs": [{"key": "Resistance", "value": "10 kOhms"}],
        },
    )

    # Assert
    assert outcome.part.name == "RC0603FR-0710KL"
    assert outcome.part.description == "RES SMD 10K OHM 1% 1/16W 0603"


def test_a_blank_upstream_part_number_still_names_the_part(authed_client, db):
    """A lookup record with an empty `mpn` must not produce a part with
    an empty name."""
    # Arrange
    workspace_id = _workspace(authed_client)

    # Act
    outcome = create_from_provider_lookup(
        db,
        workspace_id=workspace_id,
        user_id=None,
        provider_name="mouser",
        mpn="RC0603FR-0710KL",
        lookup_result={"mpn": "  ", "description": "RES SMD 10K OHM"},
    )

    # Assert
    assert outcome.part.name == "RC0603FR-0710KL"


def test_importing_a_resistor_names_it_by_its_value(authed_client, db):
    """End to end, through the real import: the provider's own category
    text files the part under Resistors, its attributes normalise to
    canonical spec keys, and the category's template turns those into the
    name. Nothing in the payload says `R 10 kΩ 1% 0603`."""
    # Arrange
    workspace_id = _workspace(authed_client)
    _category(
        authed_client,
        # The name `spec_category_map` files a resistor under. The import
        # never creates a category, so the workspace has to have it.
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )

    # Act
    outcome = create_from_provider_lookup(
        db,
        workspace_id=workspace_id,
        user_id=None,
        provider_name="digikey",
        mpn="RC0603FR-0710KL",
        lookup_result={
            "mpn": "RC0603FR-0710KL",
            "manufacturer": "Yageo",
            "description": "RES SMD 10K OHM 1% 1/16W 0603",
            "category": "Chip Resistor - Surface Mount",
            "specs": [
                {"key": "Resistance", "value": "10 kOhms"},
                {"key": "Tolerance", "value": "±1%"},
                {"key": "Package / Case", "value": "0603"},
                {"key": "Power (Watts)", "value": "0.1W"},
            ],
        },
    )

    # Assert
    assert outcome.part.name == "R 10 kΩ 1% 0603"
    # The provider's copy is still where it belongs, and the part is
    # filed, which is what made the name possible.
    assert outcome.part.description == "RES SMD 10K OHM 1% 1/16W 0603"
    assert outcome.part.category_id is not None


def test_importing_a_part_no_category_rule_matches_keeps_the_mpn(
    authed_client, db
):
    """ICs, connectors, crystals — the families the spec schema does not
    model. No category, so no template, so the part number stands."""
    # Arrange
    workspace_id = _workspace(authed_client)

    # Act
    outcome = create_from_provider_lookup(
        db,
        workspace_id=workspace_id,
        user_id=None,
        provider_name="digikey",
        mpn="STM32F103C8T6",
        lookup_result={
            "mpn": "STM32F103C8T6",
            "description": "IC MCU 32BIT 64KB FLASH 48LQFP",
            "category": "Embedded - Microcontrollers",
            "specs": [{"key": "Package / Case", "value": "48-LQFP"}],
        },
    )

    # Assert
    assert outcome.part.name == "STM32F103C8T6"
    assert outcome.part.category_id is None


def test_a_resistor_short_of_a_spec_keeps_its_part_number(authed_client, db):
    """The half-specified case, through the real import. The template
    cannot render whole, so the MPN stands rather than `R 10 kΩ 0603`."""
    # Arrange
    workspace_id = _workspace(authed_client)
    _category(
        authed_client,
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )

    # Act
    outcome = create_from_provider_lookup(
        db,
        workspace_id=workspace_id,
        user_id=None,
        provider_name="digikey",
        mpn="RC0603FR-0710KL",
        lookup_result={
            "mpn": "RC0603FR-0710KL",
            "description": "RES SMD 10K OHM 0603",
            "category": "Chip Resistor - Surface Mount",
            "specs": [
                {"key": "Resistance", "value": "10 kOhms"},
                {"key": "Package / Case", "value": "0603"},
            ],
        },
    )

    # Assert
    assert outcome.part.name == "RC0603FR-0710KL"


def test_a_user_supplied_name_is_never_rewritten(authed_client, db):
    """`create_part` is the hand-typed path. Somebody who names a part
    "Front panel bracket" means it."""
    # Arrange
    _workspace(authed_client)

    # Act
    created = authed_client.post(
        "/api/parts",
        json={"part_type": "local", "name": "Front panel bracket", "mpn": "FPB-1"},
    )

    # Assert
    assert created.status_code in (200, 201), created.text
    assert created.json()["data"]["name"] == "Front panel bracket"


# ---------------------------------------------------------------------
# The pre-loaded snapshot a caller in a loop hands in
# ---------------------------------------------------------------------


def test_a_supplied_snapshot_gives_the_same_answer_as_querying(authed_client, db):
    """Two code paths resolving the same thing is two chances to drift,
    so the one bulk-import uses has to agree with the one everything else
    uses — including on an archived category and a foreign one, which the
    snapshot carries and the query would never return."""
    # Arrange
    workspace_id = _workspace(authed_client)
    parent_id = _category(
        authed_client,
        name="Capacitors",
        refdes_prefix="C",
        value_template="{capacitance} {voltage_rating}",
    )
    child_id = _category(authed_client, name="Ceramic", parent_id=str(parent_id))
    archived_id = _category(
        authed_client,
        name="Obsolete",
        refdes_prefix="X",
        value_template="{capacitance}",
    )
    specs = {"capacitance": "1 µF", "voltage_rating": "50 V"}
    inherited = _part(
        db, workspace_id, mpn="C-1", category_id=child_id, specs=specs
    )
    orphaned = _part(
        db, workspace_id, mpn="C-2", category_id=archived_id, specs=specs
    )
    assert (
        authed_client.post(f"/api/categories/{archived_id}/archive").status_code == 200
    )
    # Everything the workspace has, archived rows included — the shape
    # `CategoryIndex.rows_by_id` holds.
    snapshot = (
        db.execute(select(PartCategory).where(PartCategory.workspace_id == workspace_id))
        .scalars()
        .all()
    )

    # Act
    queried = canonical_names(
        db, workspace_id=workspace_id, parts=[inherited, orphaned]
    )
    supplied = canonical_names(
        db,
        workspace_id=workspace_id,
        parts=[inherited, orphaned],
        workspace_categories=snapshot,
    )

    # Assert
    assert queried[inherited.id].name == "C 1 µF 50 V"
    assert queried[orphaned.id].name is None
    assert supplied == queried


def test_a_snapshot_from_another_workspace_names_nothing(authed_client, db):
    """The snapshot is a caller's, and a caller can be wrong. Rows are
    filtered to this workspace rather than trusted."""
    # Arrange
    workspace_a = _workspace(authed_client)
    other = TestClient(authed_client.app)
    _workspace(other)
    category_b = _category(
        other, name="Resistors", refdes_prefix="R", value_template=RESISTOR_TEMPLATE
    )
    category_a = _category(
        authed_client,
        name="Resistors",
        refdes_prefix="R",
        value_template=RESISTOR_TEMPLATE,
    )
    part = _part(
        db,
        workspace_a,
        mpn="A-1",
        category_id=category_a,
        specs={"resistance": "10 kΩ", "tolerance": "1%", "package": "0603"},
    )
    foreign = (
        db.execute(select(PartCategory).where(PartCategory.id == category_b))
        .scalars()
        .all()
    )

    # Act
    results = canonical_names(
        db, workspace_id=workspace_a, parts=[part], workspace_categories=foreign
    )

    # Assert — B's rows say nothing about A's part.
    assert results[part.id].name is None
