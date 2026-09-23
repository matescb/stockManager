"""Pydantic shapes for the workspace part policy.

A policy is a list of rules; each rule says "parts in this scope must
have this field", and whether that is enforced when the part is created
(`when="create"`) or only reported until it is filled in
(`when="complete"`). The vocabulary of fields is closed — `PART_FIELDS`
are `PartIn` columns a create payload can carry, `ASSET_FIELDS` are
things attached to a part after it exists — so a rule can never name
something the evaluator does not know how to check.

Every input schema keeps `extra="forbid"` (`tests/test_extra_forbid.py`).
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ASSET_FIELDS",
    "PART_FIELDS",
    "POLICY_FIELDS",
    "PolicyField",
    "PartPolicyIn",
    "PartPolicyRuleIn",
    "RuleScopeIn",
]

# `PartIn` fields a rule may require. Only fields that both create doors
# (`POST /api/parts` and the MCP `create_part` tool) accept — a rule on a
# field one door cannot set would make that door unable to create
# anything in scope.
PART_FIELDS: tuple[str, ...] = (
    "name",
    "manufacturer",
    "mpn",
    "internal_part_number",
    "description",
    "notes_markdown",
    "footprint",
    "category_id",
)

# Attached after the part exists, so only ever `when="complete"`. Named
# after what `domain/eda` calls them: a part's symbol and footprint live
# on `part_eda` (hosted id or external `Lib:Entry` ref, else the
# category default), its SPICE model is `part_eda.spice_datafile_id`, and
# its 3D model hangs off the hosted footprint.
ASSET_FIELDS: tuple[str, ...] = (
    "datasheet",
    "eda.symbol",
    "eda.footprint",
    "eda.spice",
    "eda.model3d",
)

POLICY_FIELDS: tuple[str, ...] = PART_FIELDS + ASSET_FIELDS

PolicyField = Literal[
    "name",
    "manufacturer",
    "mpn",
    "internal_part_number",
    "description",
    "notes_markdown",
    "footprint",
    "category_id",
    "datasheet",
    "eda.symbol",
    "eda.footprint",
    "eda.spice",
    "eda.model3d",
]

PartType = Literal["linked", "local", "meta", "sub_assembly"]

# Rule ids are chosen by the admin (or generated) and quoted back in every
# `missing` item, so agents and logs can name the rule that fired. Kept
# to a slug alphabet so they survive a URL, a CSV and a shell.
_RULE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"

# Caps. A policy is read on every part create; 100 rules is far past any
# real "part culture" and keeps a pathological PUT from making every
# create slow.
MAX_RULES = 100
_MAX_SCOPE_CATEGORIES = 200
_MESSAGE_MAX = 300
_HINT_MAX = 1000


class RuleScopeIn(BaseModel):
    """Which parts a rule applies to. Omitted or empty means every part."""

    model_config = ConfigDict(extra="forbid")

    # Empty / null = every category, including uncategorised parts. A
    # non-empty list matches only parts filed under one of these (and,
    # with `include_descendants`, under any subcategory of one).
    category_ids: list[UUID] | None = Field(default=None, max_length=_MAX_SCOPE_CATEGORIES)
    include_descendants: bool = True
    # Empty / null = every part type.
    part_types: list[PartType] | None = Field(default=None, max_length=4)


class PartPolicyRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional on input: a rule without one is given a generated id, and
    # a rule re-sent with its id keeps it across edits.
    id: str | None = Field(default=None, pattern=_RULE_ID_PATTERN)
    field: PolicyField
    when: Literal["create", "complete"] = "complete"
    scope: RuleScopeIn = Field(default_factory=RuleScopeIn)
    # Human-readable reason. Defaults server-side to "<Field> is required".
    message: str | None = Field(default=None, max_length=_MESSAGE_MAX)
    # Where an agent (or a person) finds the value — "manufacturer name as
    # printed on the datasheet". Defaults server-side per field.
    hint: str | None = Field(default=None, max_length=_HINT_MAX)


class PartPolicyIn(BaseModel):
    """`PUT /api/part-policy` body — the whole rule list, replacing the old."""

    model_config = ConfigDict(extra="forbid")

    rules: list[PartPolicyRuleIn] = Field(default_factory=list, max_length=MAX_RULES)
