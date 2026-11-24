# Part Policy API

Audience: engineer

The workspace part policy: which fields a part must carry, the dry-run validator, the create enforcement, and per-part completeness. The contract CLI and MCP agents code against.

## Conventions

See [API conventions](./README.md) for envelope, errors, auth. All four routes mount behind the member gate (`backend/app/main.py:574-585`): reads are open to every role and to read-only tokens; non-GETs need member+ and a full-access token. Why the policy is enforced where it is: [ADR-0035](../adr/0035-workspace-part-policy.md).

## The rule

A policy is a list of rules, stored whole on `workspaces.part_policy` (migration 0085). Default is NULL — no rules — so nothing changes until an admin writes one.

| Field | Type | Notes |
|---|---|---|
| `id` | string | `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$`. Optional on input; generated as `r_<8 hex>` when omitted. Echoed as `rule_id` in every `missing` item. |
| `field` | string | One of the part fields `name`, `manufacturer`, `mpn`, `internal_part_number`, `description`, `notes_markdown`, `footprint`, `category_id`, or the asset fields `datasheet`, `eda.symbol`, `eda.footprint`, `eda.spice`, `eda.model3d` (`backend/app/domain/part_policy/schemas.py:32-56`). |
| `when` | `"create"` \| `"complete"` | Default `"complete"`. `create` blocks `POST /api/parts`; `complete` only reports. Asset fields are `complete`-only. |
| `scope.category_ids` | UUID[] | Empty = every part (uncategorised included). Otherwise only parts filed under one of these. |
| `scope.include_descendants` | bool | Default `true`: a rule on *Passives* also covers *Passives / Resistors*. |
| `scope.part_types` | string[] | Subset of `linked`, `local`, `meta`, `sub_assembly`. Empty = all. |
| `message` | string \| null | ≤ 300 chars. Default in `missing`: `"<Label> is required"`. |
| `hint` | string \| null | ≤ 1000 chars. Where to find the value. Default in `missing`: a built-in per-field hint (`field_catalog()[].default_hint`). |

"Present" means (`backend/app/domain/part_policy/service.py:393-493`):

- part fields — not null and, for strings, not blank after strip;
- `datasheet` — an unarchived attachment with `file_type="datasheet"`, or a non-blank `datasheet_url` custom field;
- `eda.symbol` / `eda.footprint` — the part's `part_eda` hosted id or external ref, else its category's `default_symbol_ref` / `default_footprint_ref` (what the KiCad HTTP library serves);
- `eda.spice` — `part_eda.spice_datafile_id` set;
- `eda.model3d` — a 3D model attached to the part's hosted footprint.

## The `missing` item

Returned by all three evaluating routes and the MCP tools, one per unmet rule, in policy order:

```json
{
  "field": "category_id",
  "rule_id": "cat",
  "when": "create",
  "message": "Category is required",
  "hint": "Id of a workspace category; pick one from `allowed`.",
  "allowed": [{ "id": "3f0c…", "name": "Resistors", "path": "Passives / Resistors" }]
}
```

`allowed` appears only on `category_id` items: the workspace's active categories (`service.py:371-378`).

## Routes

### `GET /api/part-policy`

The rules plus the catalogue of fields a rule may name.

**Response** — `200 OK`

```json
{ "data": {
    "rules": [ { "id": "mfr", "field": "manufacturer", "when": "create",
                 "scope": { "category_ids": [], "include_descendants": true, "part_types": [] },
                 "message": null, "hint": "as printed on the datasheet" } ],
    "fields": [ { "field": "manufacturer", "label": "Manufacturer", "kind": "part",
                  "create_allowed": true, "default_hint": "Manufacturer name as printed…" }, … ] },
  "status": { … } }
```

**Notes** — Source: `backend/app/api/routes/part_policy.py:36-39`.

### `PUT /api/part-policy`

Replace the whole rule list. **Admin / owner only.** Writes one `part_policy.updated` audit row (`comment="rules=<n>"`).

**Request** — `{ "rules": [ <rule>, … ] }`, at most 100 rules, unknown keys refused.

**Response** — `200 OK`, same shape as `GET`.

**Errors**

- `403 resource.insufficient_role` (`required_role: "admin"`) — member or viewer.
- `422 part_policy.invalid_rule` with `rule_index` — an asset field at `when="create"`, a duplicate `id`, or a scope category not in this workspace (`service.py:227-276`).
- `422` validation error — unknown `field`, bad `id` pattern, over-long text.

**Notes** — Source: `backend/app/api/routes/part_policy.py:42-62`.

### `POST /api/parts/validate`

Dry-run `POST /api/parts`. Same body (`PartIn`); nothing is written.

**Response** — `200 OK`

```json
{ "data": {
    "ok": false,
    "missing": [
      { "field": "manufacturer", "rule_id": "mfr", "when": "create",
        "message": "Manufacturer is required", "hint": "as printed on the datasheet" },
      { "field": "datasheet", "rule_id": "ds", "when": "complete",
        "message": "Datasheet is required", "hint": "Attach the PDF with POST /api/attachments …" } ],
    "existing_part": null },
  "status": { … } }
```

- `ok` — true when no item has `when: "create"`, i.e. `POST /api/parts` with this body would not be refused by the policy.
- `missing` — every unmet rule, `create` and `complete`. Asset `complete` rules are always listed for a part that does not exist yet, except `eda.symbol` / `eda.footprint` satisfied by the category's default.
- A body with neither `name` nor `mpn` adds a `create` item with `rule_id: "builtin:name_or_mpn"` — the check `create_part` has always made.
- `existing_part` — `{id, name}` of the live part already holding `mpn`, else `null`. The create would answer `409 part.mpn_conflict`.

**Errors** — the create's reference checks run first and answer identically (`backend/app/domain/parts/services/create_part.py:75-97`): `404` for a `category_id` or `default_storage_location_id` not in this workspace, `409 category.archived` for an archived category. Viewers and read-only tokens get `403`: it is a POST behind the member gate.

**Notes** — Source: `backend/app/api/routes/part_policy.py:65-76`; `service.py:520-554`.

### `POST /api/parts` — policy enforcement

The existing create (see [parts](parts.md#post-apiparts)) now refuses a body that fails any `when="create"` rule in scope. Checked after the name/MPN, MPN-conflict and category checks, so an existing MPN still answers `409` (`backend/app/domain/parts/services/create_part.py:125`).

**Errors** — `422 Unprocessable Entity`, nothing written:

```json
{ "data": null,
  "status": { "category": "validation_error",
              "message": "part is missing 2 field(s) required by the workspace part policy" },
  "code": "part.policy_violation",
  "missing": [
    { "field": "manufacturer", "rule_id": "mfr", "when": "create",
      "message": "Manufacturer is required", "hint": "manufacturer name as printed on the datasheet" },
    { "field": "category_id", "rule_id": "cat", "when": "create",
      "message": "Category is required", "hint": "Id of a workspace category; pick one from `allowed`.",
      "allowed": [ { "id": "3f0c…", "name": "Resistors", "path": "Resistors" } ] } ] }
```

`missing` holds only `create` items. The other create doors — scan import, provider import, BOM import — are not gated.

### `GET /api/parts/{part_id}/policy-status`

What a stored part (archived included) still lacks, against every rule — `create` rules too, since a part can predate the rule.

**Response** — `200 OK`

```json
{ "data": { "part_id": "9b1e…", "complete": false,
            "missing": [ { "field": "eda.symbol", "rule_id": "sym", "when": "complete",
                           "message": "Schematic symbol is required", "hint": "PUT /api/parts/{id}/eda …" } ] },
  "status": { … } }
```

**Errors** — `404 part.not_found`, including for a part in another workspace.

**Notes** — Source: `backend/app/api/routes/part_policy.py:78-83`; `service.py:557-567`.

## MCP

Same evaluator, same items — see [mcp](mcp.md#part-policy): `get_part_policy`, `validate_part`, `part_policy_status` (read), and the policy check inside `create_part`, which returns the refusal as a result (`created: false`, `error: "part.policy_violation"`, `missing`) rather than a tool error.
