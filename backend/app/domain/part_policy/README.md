# part_policy

Audience: engineer

Owns the workspace part policy: the rules saying which fields a part must carry, stored as one JSONB document on `workspaces.part_policy` (migration 0085). The workspace defines "part culture", not the client tool — the web form, the netsch CLI and MCP agents all read the same rules and get the same structured `missing` list back. Why it is enforced where it is: [ADR-0035](../../../../docs/adr/0035-workspace-part-policy.md).

## Files

| File | What |
|---|---|
| `schemas.py` | `PartPolicyIn` / `PartPolicyRuleIn` / `RuleScopeIn`, and the closed field vocabulary (`PART_FIELDS`, `ASSET_FIELDS`) |
| `service.py` | Load / replace the policy, and the one evaluator every caller shares |

## Public surface

| Operation | Entry point |
|---|---|
| Read the rules + field catalogue | `service.py::policy_payload` |
| Replace the rules (validated) | `service.py::replace_policy` |
| Refuse a create that fails a `when=create` rule | `service.py::enforce_create` (called from `parts/services/create_part.py`) |
| Dry-run a create | `service.py::validate_payload` |
| What a stored part still lacks | `service.py::evaluate_part` |

REST surface: `backend/app/api/routes/part_policy.py` — `GET`/`PUT /api/part-policy`, `POST /api/parts/validate`, `GET /api/parts/{id}/policy-status`. MCP: `backend/app/mcp/tools/part_policy.py` plus the policy check inside `write_parts.py::create_part`. Contract: [docs/api/part-policy.md](../../../../docs/api/part-policy.md).

## Hard rules (this module)

- **One evaluator.** Create enforcement, validate and policy-status all go through `_evaluate`; only the "is this field present" answer differs per subject. A second copy would let a dry run pass a create that then 422s.
- **Asset fields are `complete`-only.** A datasheet, symbol, footprint, SPICE or 3D model cannot exist before the part does; `replace_policy` refuses them at `when="create"`.
- **Symbol / footprint presence follows `eda/kicad_library.py`.** The part's own hosted id or external ref, else its category's default — what KiCad will be served.
- **An empty policy changes nothing.** NULL or `rules: []` means `enforce_create` returns before evaluating anything.
