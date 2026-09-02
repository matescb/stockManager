# ADR-0029: API tokens, and the CSRF exemption for `Authorization`-bearing requests

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-09-02
- **Supersedes**: —
- **Superseded by**: —

## Context

Every credential this app had was a browser cookie. Three planned consumers
cannot use one:

- **KiCad's HTTP library** speaks a fixed protocol — GET-only, with the
  credential in an `Authorization: Token <string>` header. It has no cookie
  jar and no login flow.
- **The PCM (plugin & content manager) repository** fetches plain URLs with no
  auth header support at all, so its credential has to ride in the URL path.
- **Agents and scripts** driving the REST API want a long-lived credential that
  isn't tied to a browser session, and that can be revoked individually without
  logging a human out.

A cookie is also the wrong shape for these: it is ambient authority, it expires
on the session's idle window, and revoking it means killing a person's login.

Two things made this more than "add a bearer token". First, the CSRF guard
(`main.py::CsrfOriginMiddleware`, SEC2-001) rejects any state-changing request
whose `Origin`/`Referer` isn't allow-listed, and a non-browser client sends
neither. Second, the workspace a request operates on is currently chosen by an
`X-Workspace-Id` header or a cookie — client-supplied, and fine when a session
cookie has already pinned the user, but not something a long-lived token should
let a caller steer.

## Decision

**Per-user, workspace-pinned personal access tokens.** `api_tokens`
(migration `0069`) rows carry `user_id`, `workspace_id`, a label, a
`read_only` flag, optional expiry, and soft revocation. The token authenticates
*as* its owner: the membership row still supplies the role, so a viewer's token
cannot write however it was minted.

**HMAC at rest, composite plaintext.** The plaintext is
`smk_{token_id_hex}.{secret}` where `secret` is `secrets.token_urlsafe(32)`.
Only `HMAC-SHA256(secret, SESSION_SECRET)` is stored, so a database dump is not
replayable. The id travels in the plaintext so resolution is a primary-key
lookup followed by `hmac.compare_digest` — a scan over a hash column is itself
a timing oracle, which is what SEC2-013 fixed for invitation tokens. The `smk_`
prefix makes a leaked token greppable by secret scanners. The plaintext is
returned by `POST /api/tokens` and nowhere else, ever.

**One error for every token failure.** Malformed input, unknown id, wrong
secret, revoked, expired, and "owner is no longer a member" all produce
`401 auth.invalid_token`. Distinguishing them tells an attacker which half of a
guess was right.

**`read_only` tokens refuse writes** at the dependency layer, before any route
runs. They are what phases 5 and 6 hand to KiCad and the PCM, where the
plaintext lands in a config file or a URL path.

**The workspace is pinned at mint.** Neither `X-Workspace-Id` nor the workspace
cookie can move a token to another tenant. A mismatched header is
`403 auth.token_workspace_mismatch` — distinct from the 401s because it leaks
nothing (the holder already has the token, whose workspace every successful
response implies) and because silently ignoring it would do the wrong thing.
Membership is re-checked on every request, so removing someone's seat kills
their tokens without a sweep.

**No cookie fallback.** `core/deps.py::get_current_user` treats **any**
non-empty `Authorization` header — including one with an unrecognised scheme —
as a commitment to the token path. There is no path from "header present" to
"authenticated by cookie".

**CSRF: skip the Origin check when an `Authorization` header is present.**
The argument has two halves and needs both:

- A browser cannot attach an `Authorization` header to a cross-site request
  without a CORS preflight. This app never returns
  `Access-Control-Allow-Origin` for an untrusted origin, and
  `main.py::CORS_ALLOW_HEADERS` deliberately omits `Authorization`, so the
  preflight fails even from an allow-listed origin. The shapes CSRF actually
  takes — form posts, `<img>`, `<script>`, top-level navigation — cannot set
  request headers at all. **Adding `Authorization` to `CORS_ALLOW_HEADERS`
  would weaken this leg.**
- Even granting an attacker the header, the no-fallback rule means the request
  no longer rides the victim's cookie. It needs a valid token, and an attacker
  holding one has no use for CSRF.

The truthiness test for "header present" is deliberately identical in the
middleware and in `deps.py`. If they ever disagreed — a value that skips CSRF
in one and falls back to the cookie in the other — the exemption would become
a real forgery hole. `tests/test_api_tokens.py` pins all three legs
(token + no Origin → allowed; cookie + junk header + no Origin → 401, not the
cookie user; cookie + no header + no Origin → 403) plus the empty-header case.

**Tokens cannot manage tokens.** Every route on `/api/tokens` refuses a
token-authenticated request with `403 auth.token_no_token_management`. A
leaked token must not be able to mint a longer-lived successor, enumerate the
workspace's other credentials, or revoke the sibling whose `last_used_at`
would betray the intrusion.

**Minting is not member-gated.** `/api/tokens` is mounted without
`_member_gate`: a viewer may mint a token, since it inherits the viewer role.
Gating it would only block viewers from read features like the KiCad library.

## Consequences

- **Good**: KiCad, the PCM and agents get a first-class credential with per-token
  revocation, expiry, and `last_used_at` telemetry, without weakening cookie auth.
- **Good**: Compromising the database yields no usable tokens.
- **Good**: The audit log attributes token actions to a real user, which a
  workspace-wide catalog token could never do.
- **Trade-offs**: The CSRF middleware now has a data-dependent bypass. Its
  soundness rests entirely on the no-fallback rule in `deps.py`, which is a
  coupling across two files. It is documented at both ends and covered by
  tests, but a future refactor that "helpfully" restores a cookie fallback
  would silently open a CSRF hole.
- **Trade-offs**: A token carries its owner's *full* authority in the pinned
  workspace, minus the `/api/tokens` router. An admin's full-access token can
  therefore still invite a new admin, rotate the workspace's provider
  credentials, or mint a catalog token — all persistence paths that survive
  revoking the token itself. The mitigation is operational, not structural:
  prefer `read_only`, prefer minting from a low-privilege account, and treat
  the audit log as the detection surface. A capability model narrower than the
  role hierarchy was rejected above; revisit if this proves insufficient.
- **Trade-offs**: `last_used_at` rides the request's transaction, so it is only
  persisted for requests that reach a clean commit. A token that authenticates
  and is then refused by a route (`403`) leaves no telemetry, which blunts
  last-used as an intrusion signal for probing.
- **Trade-offs**: A token is bearer authority with no second factor. Mitigations
  are the `smk_` prefix (scanner-friendly), optional expiry, `read_only`,
  workspace pinning, per-token revocation, and last-used telemetry — not
  proof of possession.
- **What it forbids**: Do not add a cookie fallback for header-bearing requests.
  Do not split `auth.invalid_token` into finer codes. Do not index or query
  `token_hmac`. Do not add a plaintext-recovery or un-revoke endpoint. Do not
  let a token-authed request reach `/api/tokens`.

## Alternatives considered

- **Session cookie for KiCad** — impossible. The HTTP library sends
  `Authorization: Token …` and nothing else; there is no login flow to drive.
- **Reuse the workspace catalog token** (`WorkspaceCatalogToken`) — rejected.
  It has no user identity, so every action would be unattributable in the audit
  log and no role could be resolved; it is also workspace-wide rather than
  per-person.
- **A double-submit CSRF token for API clients** — rejected. Non-browser
  clients have nowhere to get one, and it would not protect the endpoints that
  need protecting any better than the Origin check already does for browsers.
- **OAuth 2.0 / JWT with scopes** — rejected as overkill for a single-tenant
  self-hosted app with no third-party clients. It would add key rotation, token
  refresh, and a scope vocabulary in exchange for capabilities the membership
  role already provides. `read_only` covers the one scope distinction that
  phases 5 and 6 actually need.
- **Per-token scopes instead of `read_only`** — deferred. A scope system that
  isn't derived from the role hierarchy would be a second authorization model
  to keep in sync with the first. Revisit if a consumer needs something
  narrower than "everything this member can read".

## References

- Source: `backend/app/core/deps.py` — `get_current_user`, `_authenticate_api_token`, `_workspace_for_api_token`
- Source: `backend/app/main.py` — `CsrfOriginMiddleware.dispatch`
- Source: `backend/app/domain/tokens/service.py` — plaintext format, `resolve_token`
- Source: `backend/app/api/routes/tokens.py` — `_no_token_auth`
- Source: `backend/alembic/versions/0069_api_tokens.py`
- Tests: `backend/tests/test_api_tokens.py`
- Related: [ADR-0024](0024-auth-verify-csrf-exemption.md) — the other CSRF exemption
- Related: [ADR-0002](0002-code-enforced-workspace-isolation.md)
- Related: `docs/api/tokens.md`
