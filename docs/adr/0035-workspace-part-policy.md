# ADR-0035: The workspace, not the client, defines which fields a part must have

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-09-23
- **Supersedes**: —
- **Superseded by**: —

## Context

Parts are created through four clients: the web form, raw REST, the MCP
`create_part` tool and the netsch CLI. Each had only one rule in common —
`name` or `mpn` (`backend/app/domain/parts/services/create_part.py:110-115`).
Anything stricter ("every IC has a manufacturer", "every passive has a
datasheet before it is usable") lived in whoever wrote the client, so an
AI agent filling the library produced whatever its prompt happened to ask
for, and the workspace had no way to say otherwise or to find out which
parts fell short.

Agents also need the answer as data. A 422 whose message says "manufacturer
required" is enough for a person; an agent needs the field name, where to
find the value, and — for a category — the ids it may choose from.

## Decision

Each workspace carries a part policy — a list of rules `{id, field, when,
scope, message, hint}` in `workspaces.part_policy` (JSONB, NULL = no
rules). It is enforced inside the shared create service, so every door that
goes through `create_part` gets it, and one evaluator
(`backend/app/domain/part_policy/service.py:317-358`) answers the create
check, the dry-run validator and the per-part completeness report. Every
refusal and report is the same structured `missing` list.

## Consequences

- **Good**: one definition of a complete part per workspace, readable by
  any client (`GET /api/part-policy`, MCP `get_part_policy`); agents can
  validate before creating and act on `missing` field by field; an empty
  policy is exactly the old behaviour.
- **Trade-offs**: rules are checked at create only. `PATCH /api/parts/{id}`
  can clear a required field, and the scan, provider and BOM importers
  build `Part` rows directly and are not gated — `policy-status` reports
  those parts, it does not stop them. Asset rules (datasheet, EDA) can
  never block a create, because nothing can be attached before the part
  exists.
- **What it forbids**: a second evaluator. Validate, create and status must
  keep going through `_evaluate`, or a dry run that passes will be followed
  by a create that 422s. Policy checks go after the MPN-conflict check in
  `create_part`, so an existing MPN is reported as the existing part (the
  MCP tool's `found_existing` success) rather than as a policy failure.
  Don't let a client define its own required fields instead of reading the
  policy.

## Alternatives considered

- **A `part_policy_rules` table** — rejected: the policy is read and
  replaced whole, belongs to exactly one workspace, and a table would add a
  query and a delete-and-reinsert PUT for no query the app needs. JSONB on
  `workspaces` matches the `active_*` lists already there.
- **Required-field flags on categories** — rejected: covers category scope
  only, cannot express "all parts" or part-type scope, and scatters the
  policy across every category row.
- **Enforcing in each client** — the status quo this replaces.

## References

- Source: `backend/app/domain/part_policy/service.py:499-517` (create enforcement)
- Related: `backend/app/domain/parts/services/create_part.py:125`
- Migration: `backend/alembic/versions/0085_workspace_part_policy.py`
- API: [part-policy](../api/part-policy.md)
