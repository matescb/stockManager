"""Reading, replacing and evaluating a workspace's part policy.

Three callers ask the same question — "which of this workspace's rules
does this part (or this would-be part) fail?" — and must never disagree
about the answer:

* `create_part` (both the REST route and the MCP tool, through
  `domain/parts/services/create_part.py`) refuses a create that fails a
  `when="create"` rule;
* `POST /api/parts/validate` and the MCP `validate_part` tool report the
  same thing without writing;
* `GET /api/parts/{id}/policy-status` and `part_policy_status` report
  what a stored part still lacks, including the attached assets
  (datasheet, symbol, footprint, SPICE and 3D model).

So there is one evaluator, `_evaluate`, and each caller only decides how
to derive the "is this field present" answers for its subject.

Workspace isolation: the policy lives on the workspace row itself, every
lookup below filters by `ws.id`, and scope category ids are checked
against the workspace when the policy is written.
"""
from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.domain.attachments.models import Attachment
from app.domain.categories.models import PartCategory
from app.domain.categories.service import category_index
from app.domain.categories.tree import ancestor_ids, load_parent_map
from app.domain.custom_fields.models import CustomField
from app.domain.eda.models import EdaFootprintModel, PartEda
from app.domain.part_policy.schemas import (
    ASSET_FIELDS,
    PART_FIELDS,
    POLICY_FIELDS,
    PartPolicyIn,
    PartPolicyRuleIn,
)
from app.domain.parts.models import Part
from app.domain.parts.schemas import PartIn
from app.domain.parts.services.mpn_unique import active_part_by_mpn

__all__ = [
    "Rule",
    "enforce_create",
    "evaluate_part",
    "field_catalog",
    "load_rules",
    "policy_payload",
    "replace_policy",
    "validate_payload",
]

_log = logging.getLogger(__name__)

POLICY_VERSION = 1

# Rule id the built-in "name or mpn" check reports under in validate
# results. Not a policy rule — `create_part` has always refused a part
# with neither — but a dry run that passes and then a create that 422s
# would teach an agent to distrust the dry run.
BUILTIN_NAME_OR_MPN = "builtin:name_or_mpn"

_LABELS: dict[str, str] = {
    "name": "Name",
    "manufacturer": "Manufacturer",
    "mpn": "MPN",
    "internal_part_number": "Internal part number",
    "description": "Description",
    "notes_markdown": "Notes",
    "footprint": "Footprint",
    "category_id": "Category",
    "datasheet": "Datasheet",
    "eda.symbol": "Schematic symbol",
    "eda.footprint": "PCB footprint",
    "eda.spice": "SPICE model",
    "eda.model3d": "3D model",
}

# Used when a rule has no hint of its own, so an agent always gets told
# where the value comes from and how to set it.
_DEFAULT_HINTS: dict[str, str] = {
    "name": (
        "Short human-readable name. Defaults to the MPN when omitted, so a "
        "rule on `name` asks for an explicit one."
    ),
    "manufacturer": 'Manufacturer name as printed on the datasheet, e.g. "Texas Instruments".',
    "mpn": "Manufacturer part number exactly as on the datasheet or distributor page.",
    "internal_part_number": "The organisation's own part number for this part.",
    "description": "One line saying what the part is and its key parameters.",
    "notes_markdown": "Free-form notes (Markdown).",
    "footprint": 'Package / footprint name, e.g. "0402" or "SOIC-8".',
    "category_id": "Id of a workspace category; pick one from `allowed`.",
    "datasheet": (
        "Attach the PDF with POST /api/attachments (object_type=part, "
        "file_type=datasheet), or set the part's `datasheet_url` custom field."
    ),
    "eda.symbol": (
        "PUT /api/parts/{id}/eda with `symbol_id` (hosted) or "
        '`symbol_ref_external` (e.g. "Device:R"), or give the category a '
        "default symbol."
    ),
    "eda.footprint": (
        "PUT /api/parts/{id}/eda with `footprint_id` (hosted) or "
        '`footprint_ref_external` (e.g. "Resistor_SMD:R_0402_1005Metric"), or '
        "give the category a default footprint."
    ),
    "eda.spice": (
        "Upload the model with POST /api/eda/datafiles (kind=spice), then set "
        "`spice_datafile_id` with PUT /api/parts/{id}/eda."
    ),
    "eda.model3d": (
        "Attach a STEP/WRL to the part's hosted footprint with "
        "POST /api/eda/footprints/{footprint_id}/models."
    ),
}


@dataclass(frozen=True)
class Rule:
    """One stored rule, normalised. Immutable: evaluation never edits it."""

    id: str
    field: str
    when: str
    category_ids: tuple[UUID, ...]
    include_descendants: bool
    part_types: tuple[str, ...]
    message: str | None
    hint: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "field": self.field,
            "when": self.when,
            "scope": {
                "category_ids": [str(c) for c in self.category_ids],
                "include_descendants": self.include_descendants,
                "part_types": list(self.part_types),
            },
            "message": self.message,
            "hint": self.hint,
        }


def field_catalog() -> list[dict[str, Any]]:
    """Every field a rule may name, with what it is and when it can apply."""
    return [
        {
            "field": f,
            "label": _LABELS[f],
            "kind": "part" if f in PART_FIELDS else "asset",
            "create_allowed": f in PART_FIELDS,
            "default_hint": _DEFAULT_HINTS[f],
        }
        for f in POLICY_FIELDS
    ]


def _rule_from_in(rule: PartPolicyRuleIn, rule_id: str) -> Rule:
    scope = rule.scope
    return Rule(
        id=rule_id,
        field=rule.field,
        when=rule.when,
        category_ids=tuple(dict.fromkeys(scope.category_ids or ())),
        include_descendants=scope.include_descendants,
        part_types=tuple(dict.fromkeys(scope.part_types or ())),
        message=(rule.message or "").strip() or None,
        hint=(rule.hint or "").strip() or None,
    )


def load_rules(ws: Any) -> list[Rule]:
    """The workspace's rules, in stored order. NULL policy = no rules.

    A stored rule that no longer validates (the vocabulary shrank under
    it) is skipped and logged rather than failing every part create in
    the workspace.
    """
    doc = ws.part_policy or {}
    out: list[Rule] = []
    for raw in doc.get("rules") or ():
        try:
            parsed = PartPolicyRuleIn.model_validate(raw)
        except ValidationError:
            _log.warning("part policy: skipping invalid stored rule in ws %s", ws.id)
            continue
        out.append(_rule_from_in(parsed, parsed.id or _new_rule_id()))
    return out


def policy_payload(ws: Any) -> dict[str, Any]:
    """`GET /api/part-policy` / `get_part_policy` response body."""
    return {
        "rules": [r.as_dict() for r in load_rules(ws)],
        "fields": field_catalog(),
    }


def _new_rule_id() -> str:
    return f"r_{secrets.token_hex(4)}"


def _invalid_rule(index: int, message: str) -> None:
    raise_http(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        ErrorCodes.PART_POLICY_INVALID_RULE,
        f"rule {index}: {message}",
        rule_index=index,
    )


def replace_policy(db: Session, *, ws: Any, body: PartPolicyIn) -> list[Rule]:
    """Validate `body` and store it as the workspace's whole policy.

    Refuses (422 `part_policy.invalid_rule`, naming the rule's index):
    an asset field at `when="create"` (it cannot exist before the part
    does), a duplicate rule id, and a scope category that is not in this
    workspace. Does not commit and does not audit — the route owns both.
    """
    seen_ids: set[str] = set()
    rules: list[Rule] = []
    for index, rule_in in enumerate(body.rules):
        if rule_in.field in ASSET_FIELDS and rule_in.when == "create":
            _invalid_rule(
                index,
                f"`{rule_in.field}` is attached after the part exists, so it "
                'can only be required with when="complete"',
            )
        rule_id = rule_in.id or _new_rule_id()
        while rule_in.id is None and rule_id in seen_ids:
            rule_id = _new_rule_id()
        if rule_id in seen_ids:
            _invalid_rule(index, f"duplicate rule id {rule_id!r}")
        seen_ids.add(rule_id)
        rules.append(_rule_from_in(rule_in, rule_id))

    _assert_scope_categories(db, ws_id=ws.id, rules=rules)

    ws.part_policy = {
        "version": POLICY_VERSION,
        "rules": [r.as_dict() for r in rules],
    }
    db.flush()
    return rules


def _assert_scope_categories(db: Session, *, ws_id: UUID, rules: list[Rule]) -> None:
    wanted = {c for r in rules for c in r.category_ids}
    if not wanted:
        return
    found = set(
        db.execute(
            select(PartCategory.id)
            .where(PartCategory.workspace_id == ws_id)
            .where(PartCategory.id.in_(wanted))
        ).scalars()
    )
    for index, rule in enumerate(rules):
        unknown = [c for c in rule.category_ids if c not in found]
        if unknown:
            _invalid_rule(index, f"category {unknown[0]} not found in this workspace")


# --- evaluation --------------------------------------------------------


@dataclass(frozen=True)
class _Subject:
    """What the evaluator needs to know about a part, real or proposed."""

    part_type: str
    category_id: UUID | None
    # Field -> present? Asset fields are resolved lazily, because most
    # policies name none and those lookups cost queries.
    present: Callable[[str], bool]


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _in_scope(
    rule: Rule, subject: _Subject, ancestors: Callable[[UUID], list[UUID]]
) -> bool:
    if rule.part_types and subject.part_type not in rule.part_types:
        return False
    if not rule.category_ids:
        return True
    if subject.category_id is None:
        return False
    if subject.category_id in rule.category_ids:
        return True
    if not rule.include_descendants:
        return False
    return any(a in rule.category_ids for a in ancestors(subject.category_id))


def _evaluate(
    db: Session,
    *,
    ws: Any,
    rules: Iterable[Rule],
    subject: _Subject,
    when: str | None = None,
) -> list[dict[str, Any]]:
    """The `missing` items for every in-scope rule `subject` fails.

    `when` narrows to one kind of rule (create enforcement asks only
    about `create`). One item per failing rule, in policy order, so two
    rules on the same field with different messages both reach the
    caller.
    """
    parent_map: dict[UUID, UUID | None] | None = None

    def ancestors(category_id: UUID) -> list[UUID]:
        nonlocal parent_map
        if parent_map is None:
            parent_map = load_parent_map(db, workspace_id=ws.id)
        return ancestor_ids(parent_map, category_id)

    presence: dict[str, bool] = {}
    missing: list[dict[str, Any]] = []
    for rule in rules:
        if when is not None and rule.when != when:
            continue
        if not _in_scope(rule, subject, ancestors):
            continue
        if rule.field not in presence:
            presence[rule.field] = subject.present(rule.field)
        if presence[rule.field]:
            continue
        missing.append(_missing_item(rule))

    if any(item["field"] == "category_id" for item in missing):
        allowed = _allowed_categories(db, ws_id=ws.id)
        for item in missing:
            if item["field"] == "category_id":
                item["allowed"] = allowed
    return missing


def _missing_item(rule: Rule) -> dict[str, Any]:
    return {
        "field": rule.field,
        "rule_id": rule.id,
        "when": rule.when,
        "message": rule.message or f"{_LABELS[rule.field]} is required",
        "hint": rule.hint or _DEFAULT_HINTS[rule.field],
    }


def _allowed_categories(db: Session, *, ws_id: UUID) -> list[dict[str, str]]:
    """Active categories an agent may file the part under: id, name, path."""
    index = category_index(db, ws_id=ws_id)
    return [
        {"id": str(row.id), "name": row.name, "path": index.paths.get(row.id, row.name)}
        for row in index.rows_by_id.values()
        if row.archived_at is None
    ]


def _workspace_category(
    db: Session, *, ws_id: UUID, category_id: UUID | None
) -> PartCategory | None:
    if category_id is None:
        return None
    return db.execute(
        select(PartCategory)
        .where(PartCategory.id == category_id)
        .where(PartCategory.workspace_id == ws_id)
    ).scalar_one_or_none()


def _payload_subject(db: Session, *, ws: Any, payload: PartIn) -> _Subject:
    def present(field: str) -> bool:
        if field in PART_FIELDS:
            return _has_value(getattr(payload, field))
        # Before the part exists nothing can be attached to it — except
        # that KiCad resolves a missing symbol/footprint to the
        # category's default, so a category that has one satisfies the
        # rule from the start.
        if field in ("eda.symbol", "eda.footprint"):
            category = _workspace_category(db, ws_id=ws.id, category_id=payload.category_id)
            attr = "default_symbol_ref" if field == "eda.symbol" else "default_footprint_ref"
            return category is not None and _has_value(getattr(category, attr))
        return False

    return _Subject(
        part_type=payload.part_type,
        category_id=payload.category_id,
        present=present,
    )


def _part_subject(db: Session, *, ws: Any, part: Part) -> _Subject:
    config_cache: list[PartEda | None] = []

    def config() -> PartEda | None:
        if not config_cache:
            config_cache.append(
                db.execute(
                    select(PartEda)
                    .where(PartEda.workspace_id == ws.id)
                    .where(PartEda.part_id == part.id)
                ).scalar_one_or_none()
            )
        return config_cache[0]

    def present(field: str) -> bool:
        if field in PART_FIELDS:
            return _has_value(getattr(part, field))
        if field == "datasheet":
            return _has_datasheet(db, ws_id=ws.id, part_id=part.id)
        cfg = config()
        if field in ("eda.symbol", "eda.footprint"):
            return _has_eda_ref(db, ws_id=ws.id, part=part, cfg=cfg, kind=field[4:])
        if field == "eda.spice":
            return cfg is not None and cfg.spice_datafile_id is not None
        if field == "eda.model3d":
            return _has_model3d(db, ws_id=ws.id, cfg=cfg)
        return False

    return _Subject(part_type=part.part_type, category_id=part.category_id, present=present)


def _has_datasheet(db: Session, *, ws_id: UUID, part_id: UUID) -> bool:
    """A datasheet attachment, or a non-blank `datasheet_url` field."""
    attachment = db.execute(
        select(Attachment.id)
        .where(Attachment.workspace_id == ws_id)
        .where(Attachment.object_type == "part")
        .where(Attachment.object_id == part_id)
        .where(Attachment.file_type == "datasheet")
        .where(Attachment.archived_at.is_(None))
        .limit(1)
    ).first()
    if attachment is not None:
        return True
    values = db.execute(
        select(CustomField.value)
        .where(CustomField.workspace_id == ws_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id == part_id)
        .where(CustomField.key == "datasheet_url")
    ).scalars()
    return any(_has_value(v) for v in values)


def _has_eda_ref(
    db: Session, *, ws_id: UUID, part: Part, cfg: PartEda | None, kind: str
) -> bool:
    """Symbol / footprint resolved the way `eda/kicad_library.py` does.

    The part's own hosted entry or external ref first, else its
    category's default — that is what KiCad will actually be served, so
    it is what "has a symbol" has to mean here.
    """
    if cfg is not None and (getattr(cfg, f"{kind}_id") or getattr(cfg, f"{kind}_ref_external")):
        return True
    category = _workspace_category(db, ws_id=ws_id, category_id=part.category_id)
    return category is not None and _has_value(getattr(category, f"default_{kind}_ref"))


def _has_model3d(db: Session, *, ws_id: UUID, cfg: PartEda | None) -> bool:
    """A 3D model on the part's hosted footprint (same rule as `eda_status`)."""
    if cfg is None or cfg.footprint_id is None:
        return False
    row = db.execute(
        select(EdaFootprintModel.id)
        .where(EdaFootprintModel.workspace_id == ws_id)
        .where(EdaFootprintModel.footprint_id == cfg.footprint_id)
        .limit(1)
    ).first()
    return row is not None


# --- the three entry points -----------------------------------------------


def enforce_create(db: Session, *, ws: Any, payload: PartIn) -> None:
    """422 `part.policy_violation` if `payload` fails any `when=create` rule.

    The caller has already workspace-checked `payload.category_id`.
    """
    rules = load_rules(ws)
    if not any(r.when == "create" for r in rules):
        return
    missing = _evaluate(
        db, ws=ws, rules=rules, subject=_payload_subject(db, ws=ws, payload=payload),
        when="create",
    )
    if missing:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.PART_POLICY_VIOLATION,
            f"part is missing {len(missing)} field(s) required by the workspace part policy",
            missing=missing,
        )


def validate_payload(db: Session, *, ws: Any, payload: PartIn) -> dict[str, Any]:
    """Dry run of a create: `{ok, missing, existing_part}`.

    `missing` holds every rule the payload fails — `create` rules (which
    would block the create) and `complete` rules (which would not, but
    would be reported by `policy-status` straight away). `ok` is true
    when nothing in `missing` is a `create` rule. The caller has already
    workspace-checked `payload.category_id`.
    """
    missing: list[dict[str, Any]] = []
    if not _has_value(payload.name) and not _has_value(payload.mpn):
        missing.append({
            "field": "name",
            "rule_id": BUILTIN_NAME_OR_MPN,
            "when": "create",
            "message": "provide at least one of `name` or `mpn`",
            "hint": _DEFAULT_HINTS["mpn"],
        })
    missing.extend(
        _evaluate(
            db, ws=ws, rules=load_rules(ws),
            subject=_payload_subject(db, ws=ws, payload=payload),
        )
    )
    existing = None
    mpn = (payload.mpn or "").strip()
    if mpn:
        found = active_part_by_mpn(db, workspace_id=ws.id, mpn=mpn)
        if found is not None:
            existing = {"id": str(found.id), "name": found.name}
    return {
        "ok": not any(item["when"] == "create" for item in missing),
        "missing": missing,
        "existing_part": existing,
    }


def evaluate_part(db: Session, *, ws: Any, part: Part) -> dict[str, Any]:
    """`{complete, missing}` for a stored part, against every rule.

    Create rules are included: a part created before the rule existed
    (or through a door the policy does not gate) can lack one, and it is
    no more complete for that.
    """
    missing = _evaluate(
        db, ws=ws, rules=load_rules(ws), subject=_part_subject(db, ws=ws, part=part)
    )
    return {"complete": not missing, "missing": missing}
