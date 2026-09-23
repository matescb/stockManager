"""Read-only MCP tools for the workspace part policy.

The policy is how a workspace says what a complete part looks like; these
three tools are how an agent finds out before, instead of after, it
creates one. `create_part` (in `write_parts.py`) enforces the same rules
and returns the same `missing` items, so the loop an agent runs is:
`get_part_policy` once, `validate_part` until `ok`, `create_part`, then
`part_policy_status` while attaching the rest.

Evaluation is `domain/part_policy/service.py` — the same code the REST
routes use.
"""
from __future__ import annotations

from typing import Any, Literal

from app.domain.part_policy import service as policy
from app.domain.parts.services.create_part import check_references
from app.mcp.principal import Caller
from app.mcp.tools._registry import tool
from app.mcp.tools._shared import part_url, resolve_part, sid
from app.mcp.tools.write_parts import build_part_in


@tool()
def get_part_policy(caller: Caller) -> dict[str, Any]:
    """The workspace's part policy: which fields a part must have.

    Returns `rules`, each `{id, field, when, scope, message, hint}`:

    * `field` — a part field (`manufacturer`, `mpn`, `description`,
      `footprint`, `category_id`, `internal_part_number`, `name`,
      `notes_markdown`) or something attached later (`datasheet`,
      `eda.symbol`, `eda.footprint`, `eda.spice`, `eda.model3d`).
    * `when` — `create` means `create_part` refuses a part without it;
      `complete` means the part is created but reported as incomplete
      until it is filled in.
    * `scope` — `category_ids` (empty = every part; subcategories count
      when `include_descendants`) and `part_types` (empty = all).
    * `hint` — where to find the value.

    Also returns `fields`, the catalogue of every field a rule may name.
    An empty `rules` list means the workspace requires nothing beyond a
    name or MPN.
    """
    return policy.policy_payload(caller.ws)


@tool()
def validate_part(
    caller: Caller,
    name: str | None = None,
    mpn: str | None = None,
    manufacturer: str | None = None,
    description: str | None = None,
    category_id: str | None = None,
    part_type: Literal["local", "linked"] | None = None,
    internal_part_number: str | None = None,
    footprint: str | None = None,
    notes_markdown: str | None = None,
) -> dict[str, Any]:
    """Check a part against the workspace policy WITHOUT creating it.

    Takes exactly the arguments `create_part` takes. Returns:

    * `ok` — true when `create_part` with these arguments would not be
      refused by the policy.
    * `missing` — one item per unmet rule: `{field, rule_id, when,
      message, hint}`, plus `allowed` for `category_id` (the categories
      you may pick, as `{id, name, path}`). Items with `when: "create"`
      block the create; items with `when: "complete"` do not, but are
      what the part will still lack afterwards (a datasheet or symbol
      cannot exist before the part does).
    * `existing_part` — `{id, name}` when a part with this `mpn` already
      exists, in which case `create_part` would return it rather than
      create another; otherwise null.

    Fill in the `create` items and call again until `ok` is true. An
    archived category is refused (`category.archived`) exactly as
    `create_part` would refuse it.
    """
    payload = build_part_in(
        caller,
        name=name,
        mpn=mpn,
        manufacturer=manufacturer,
        description=description,
        category_id=category_id,
        part_type=part_type,
        internal_part_number=internal_part_number,
        footprint=footprint,
        notes_markdown=notes_markdown,
    )
    check_references(caller.db, ws=caller.ws, payload=payload)
    return policy.validate_payload(caller.db, ws=caller.ws, payload=payload)


@tool()
def part_policy_status(caller: Caller, part_id_or_mpn: str) -> dict[str, Any]:
    """What an existing part still lacks under the workspace policy.

    Args:
        part_id_or_mpn: The part's id or its exact MPN.

    Returns `complete` (true when nothing is missing) and `missing`, the
    same `{field, rule_id, when, message, hint, allowed?}` items
    `validate_part` returns — here including attached things: a
    datasheet (attachment or `datasheet_url`), a schematic symbol and
    PCB footprint (the part's own, or its category's default), a SPICE
    model and a 3D model.
    """
    part = resolve_part(caller, part_id_or_mpn, include_archived=True)
    return {
        "part_id": sid(part.id),
        "part_url": part_url(part.id),
        **policy.evaluate_part(caller.db, ws=caller.ws, part=part),
    }
