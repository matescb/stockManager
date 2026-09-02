# tokens

Audience: engineer

Owns `ApiToken` — per-user, workspace-pinned personal access tokens. They are the non-cookie credential the KiCad HTTP library, the PCM repository and agent/REST callers authenticate with. A token acts **as** its owning user: it carries no privileges of its own, and the membership role still decides what the request may do.

## Files

| File | What |
|---|---|
| `models.py` | `ApiToken` |
| `schemas.py` | `ApiTokenIn` / `ApiTokenOut` / `ApiTokenCreated` |
| `service.py` | Mint / parse / resolve / list / revoke + the HMAC helper |

## Public surface

| Operation | Entry point |
|---|---|
| Mint (returns the one-time plaintext) | `service.py::mint_token` |
| Authenticate a plaintext | `service.py::resolve_token` |
| Best-effort last-used telemetry | `service.py::record_use` |
| List own / list workspace (admin) | `service.py::list_own`, `::list_workspace` |
| Look up one, workspace-scoped | `service.py::get_in_workspace` |
| Soft revoke | `service.py::revoke` |

REST surface: `backend/app/api/routes/tokens.py` (`/api/tokens`).
Authentication wiring: `backend/app/core/deps.py::get_current_user` and `::_workspace_for_api_token` — the only places that resolve a plaintext. Everything downstream reads `request.state.api_token`, which `get_current_user` sets (see `routes/tokens.py::_no_token_auth`).

## Hard rules (this module)

1. **The plaintext is returned exactly once**, by `POST /api/tokens`. Only its HMAC-SHA256 digest (keyed on `SESSION_SECRET`) is stored, so a database dump cannot be replayed. There is no recovery path, for anyone.
2. **Every resolution failure is the same failure.** Malformed, unknown id, wrong secret, revoked, expired, owner-no-longer-a-member — all collapse to `resolve_token() -> None` and one `auth.invalid_token` 401. Splitting them apart re-introduces an oracle.
3. **Lookup is by primary key.** The id travels in the plaintext (`smk_{id}.{secret}`) precisely so the database never scans a secret-derived column; the secret is then compared with `hmac.compare_digest`. Same reasoning as the invitation token (SEC2-013).
4. **A present `Authorization` header disables cookie auth entirely.** `get_current_user` never falls back. This is what makes the CSRF exemption in `main.py::CsrfOriginMiddleware` sound — see ADR-0029.
5. **The workspace is pinned at mint, in two places.** Neither `X-Workspace-Id` nor the workspace cookie can move a token to another tenant; a mismatched header is a `403`. `get_current_workspace` resolves the workspace, but several routes take only `CurrentUser` and never reach it — so the membership re-check lives in `_authenticate_api_token` (every token request passes through it) and the cross-workspace reads (`/auth/me`, `GET /api/workspaces`) narrow themselves via `deps.py::api_token_workspace_id`. Do not move the membership check back into the workspace dependency.
6. **Telemetry never fails auth, is throttled, and outlives a refused request.** `record_use` is called inside a `try/except` in `deps.py`; a failure is logged, rolled back, and the request proceeds. Writes are capped at one per `TELEMETRY_MIN_INTERVAL_SECONDS` (300s) per token so KiCad's polling doesn't turn every read into a contended write — and when one does happen it is committed there and then, so a read-only token probed with writes still leaves a trail after its `403` rolls the request back.
7. **Credential and tenancy administration needs a session cookie.** `deps.py::forbid_api_token` guards all of `/api/tokens`, workspace create/switch, `PATCH /workspaces/current` (it writes provider credentials), member role change/removal, catalog-token mint/revoke, and invitation issue/revoke/accept. One 403 code for the whole class.
8. **Removing a member revokes their tokens.** `revoke_all_for_user`, called by `workspaces.py::remove_member` before the seat row is deleted, so a re-invite at a lower role cannot reanimate an old credential.
9. **The CSRF skip stops at `/api/auth/`.** `main.py::CsrfOriginMiddleware` never waives the Origin check for those paths, because `logout` reads the session cookie directly and so is not covered by the no-cookie-fallback rule that justifies the waiver elsewhere.

## See also

- [ADR-0029](../../../../docs/adr/0029-api-tokens-and-csrf-exemption.md) — the token design and the CSRF exemption argument
- [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md) — workspace isolation is the caller's job
- [docs/api/tokens.md](../../../../docs/api/tokens.md) — REST reference

## Don't

- Don't add an index on `token_hmac`, or look a token up by it. The PK lookup is the whole point.
- Don't add a distinct error code for a specific token failure, however tempting the debugging story is.
- Don't let a token-authenticated request reach `/api/tokens` — a leaked credential must not be able to widen itself or revoke its siblings (`routes/tokens.py::_no_token_auth`).
- Don't store the plaintext, log it, or put the label's secret-adjacent siblings in an audit comment.
