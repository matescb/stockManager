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

Pinning is enforced in two places, and it has to be. `get_current_workspace`
resolves the `Workspace` object, but a handful of routes take only
`CurrentUser` and never depend on it — `/auth/me`, `GET` and `POST
/api/workspaces`, `/workspaces/{id}/switch`, `/invitations/accept`. So the
membership re-check lives in `_authenticate_api_token`, which every
token-authed request passes through, and the two cross-workspace *reads*
(`/auth/me`, `GET /api/workspaces`) narrow their own results to
`api_token_workspace_id(request)`. Removing someone's seat kills their tokens
on the next request everywhere, without a sweep over `api_tokens` — and
`remove_member` additionally revokes them, so a later re-invite at a lower
role can't reanimate a credential minted under the old one.

**Credential and tenancy administration is session-cookie only.** Every route
on `/api/tokens`, plus workspace creation, workspace switching, member role
changes and removals, catalog-token minting and revocation, issuing, revoking
or accepting an invitation, and `PATCH /api/workspaces/current` (it writes the
workspace's encrypted provider credentials), refuse a token-authed request with
`403 auth.token_no_token_management` (`core/deps.py::forbid_api_token`). One
code for the whole class: the client-actionable fact is "this route needs a
browser session", not which flavour of administration it was. A leaked token
must not be able to widen itself, mint a second credential that outlives its
own revocation, invite an accomplice, or move its owner between tenants.

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

**The skip is never applied under `/api/auth/`.** Leg two above says a forged
header gains nothing because the request can no longer reach cookie auth — but
that is a property of `get_current_user`, and not every route authenticates
through it. `/api/auth/logout` reads `request.cookies` directly, so an
`Authorization` header does not disable cookie auth there; exempting it would
have removed its only defence and left a forced-logout CSRF. The same applies
to `/api/auth/request-password-reset` and `/api/auth/reset-password`, which are
state-changing and cookie-adjacent. No API-token client has any business
calling any of them, so the carve-out costs nothing. (The pre-auth routes that
genuinely need an exemption — login, signup, verify — are in
`_CSRF_EXEMPT_PATHS`, matched earlier.) The general lesson is the one that bit
here: **a global exemption paired with a per-route compensating control is only
as strong as the routes that actually implement the control.**

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
- **Good**: No route reachable by a token can issue another credential or
  change who holds one. The security review's remaining persistence paths —
  inviting an accomplice, minting a catalog token, rotating the workspace's
  provider API keys — are all blocked, so revoking a leaked token is sufficient
  rather than merely necessary.
- **Trade-offs**: `PATCH /api/workspaces/current` is now closed to tokens even
  though most of its payload is ordinary settings, because it also writes the
  encrypted provider credentials. An agent that legitimately needs to change a
  non-credential setting will need a narrower endpoint; splitting the route was
  out of scope here, and the safe default won.
- **Trade-offs**: Rotating `SESSION_SECRET` invalidates every API token, because
  the stored `token_hmac` is keyed on it. See
  `docs/runbooks/secret-rotation.md` — the rotation is not complete until every
  KiCad workstation, PCM consumer and agent has been re-issued a token.
- **Good**: Probing a stolen token is visible. `record_use` runs BEFORE the
  read-only check, and when it actually writes (the throttle suppresses most
  calls) the row is committed on its own at auth time — so the trail survives
  the `403` that follows, and the rollback `get_db` performs on the failed
  request. Committing inside a dependency is the one documented exception to
  "routes never commit", and it is the same exception the cookie path already
  takes for its sliding-expiry bump: authentication runs before any route
  work, so there is nothing half-finished to strand.
- **Trade-offs**: `last_used_at` is a "still in use?" signal, not an access
  log. It is throttled to one write per 300s per token so KiCad's polling
  doesn't turn every read into a contended write, which also bounds the extra
  commit to at most one per token per interval.
- **Trade-offs**: A token is bearer authority with no second factor. Mitigations
  are the `smk_` prefix (scanner-friendly), optional expiry, `read_only`,
  workspace pinning, per-token revocation, and last-used telemetry — not
  proof of possession.
- **What it forbids**: Do not add a cookie fallback for header-bearing requests.
  Do not split `auth.invalid_token` into finer codes. Do not index or query
  `token_hmac`. Do not add a plaintext-recovery or un-revoke endpoint. Do not
  let a token-authed request reach `/api/tokens` or any other route listed under
  *Credential and tenancy administration*. Do not move the membership re-check
  out of `_authenticate_api_token` and back into `get_current_workspace` — that
  is precisely the bug this ADR's second revision fixed, and it is invisible
  until someone adds a route that takes only `CurrentUser`. Do not drop the
  explicit narrowing in `/auth/me` and `GET /api/workspaces`.

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
