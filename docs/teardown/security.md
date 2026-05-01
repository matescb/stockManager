# Security Teardown

Scope: AuthN/Z, secrets, surface, logging, input, supply chain.
Date: 2026-05-01.
Existing review IDs covered/extended: SEC-001..SEC-007.
Recent fixes verified: ff867d4 (encrypted workspace secrets) — **partially resolved** SEC-001 (plumbed through prod compose, but startup still does not fail-closed when the key is missing in prod, and the dev fallback key is committed to source). 205ade0 (weak-password rejection) — **resolved at signup only**; password rotation / change-password and the invite-accept signup path were not in scope. 6990a18 (admin RBAC on archive/restore/bulk-delete) — **fully resolved** for parts/orders/projects/storage/builds; spot-checked all five routers.

## Security Issues

### SEC2-001: No CSRF protection on cookie-authenticated state-changing endpoints

Severity: **Critical**

Evidence:
- `backend/app/main.py:125-131` — CORS middleware: `allow_origins=settings().cors_origin_list`, `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`. No CSRF middleware is installed.
- `backend/app/api/routes/auth.py:44-56` — session cookie is `SameSite=Lax`. Lax does **not** stop top-level POST navigations (form-submit → `application/x-www-form-urlencoded` posts). It also doesn't help against same-site sub-domain takeovers.
- `backend/app/api/routes/workspaces.py:231` — `POST /api/workspaces/{workspace_id}/switch` mutates a long-lived cookie and accepts the workspace UUID from the path.
- `backend/app/api/routes/parts.py:408` — `POST /api/parts/bulk-delete` archives parts.
- `backend/app/api/routes/builds.py:140-154`, `orders.py:169-182`, `projects.py:113-126` — admin-gated archive/restore via `POST` (no body required, classic CSRF target).
- No endpoint in `backend/app/api/routes/` checks `Origin` / `Referer` or a CSRF token. The only `request.headers` read in the entire backend is `X-Workspace-Id` in `core/deps.py:49`.

Impact:

A logged-in admin who visits an attacker page (or a malicious comment with a hidden `<form>`) can be made to issue arbitrary state-changing POSTs to the API. SameSite=Lax is bypassable via top-level POST forms with `enctype=text/plain` or via any browser that downgrades to None for cross-site top-level POST (older Safari, embedded webviews). Combined with `allow_credentials: true` on `*` methods/headers, any browser-based RCE on a same-eTLD+1 page is also a fully-credentialed cross-origin XHR away from blowing the workspace open. Catalog-page XSS (see SEC2-009) provides such a same-site staging point.

Fix instruction:

Add an `Origin`/`Referer` allow-list check on every state-changing route (POST/PATCH/DELETE), enforced as a FastAPI middleware: reject when the header is absent or its origin is not in `settings().cors_origin_list`. Consider a double-submit CSRF token cookie for defence in depth. Tighten CORS to `allow_methods=["GET","POST","PATCH","DELETE"]` and an explicit header allow-list. Lock SameSite to `Strict` for the workspace-switch cookie which is purely server-driven.

### SEC2-002: Workspace-secrets fallback key is committed; prod startup does not fail-closed

Severity: **Critical**

Evidence:
- `backend/app/core/secrets.py:46` — `_DEV_DEFAULT_KEY = b"OXmO1Y_-zTtTJ_NXxL5RQqGsbwI3wQAOJ-V_M5HH4_o="` is a real, valid Fernet key in plaintext in the repo.
- `backend/app/core/secrets.py:53-63` — when `WORKSPACE_SECRETS_KEY` is empty, the fallback is silently used and only a one-shot `log.warning` fires; this matches the post-mortem note in `alembic/versions/0016_encrypt_workspace_secrets.py:32-36` ("a soft warning is the right posture").
- `backend/app/core/config.py:39` — `WORKSPACE_SECRETS_KEY: str = ""` (no validator).
- `docker-compose.prod.yml` — `WORKSPACE_SECRETS_KEY` is **not** in the `backend.environment` block (still missing post-ff867d4); the deploy template (`deploy/.env.prod.example:43`) ships it empty by default.
- `backend/app/main.py` — no startup probe asserts the key is non-fallback in prod.

Impact:

Refines SEC-001. ff867d4 added the plumbing (column widening, encrypt at write, decrypt at read) but the load-bearing operational guard never landed. A fresh prod deploy with `WORKSPACE_SECRETS_KEY=` (blank) in `.env.prod` will silently encrypt every workspace's Mouser/DigiKey API keys + Scandit license under a Fernet key that is in this public repo. Anyone who can read a backup or replica can decrypt them with `cryptography.fernet.Fernet(b"OXmO1Y_-zTtTJ_NXxL5RQqGsbwI3wQAOJ-V_M5HH4_o=").decrypt(ct)`. The fix is silent by design and the warning is one-shot, so an operator inspecting `docker logs backend` after a few minutes sees nothing.

Fix instruction:

In `core/config.py`, add a Pydantic validator that raises if `APP_ENV == "prod"` and `WORKSPACE_SECRETS_KEY` is empty or equal to the dev default. Pass `WORKSPACE_SECRETS_KEY: ${WORKSPACE_SECRETS_KEY}` in `docker-compose.prod.yml` backend env. Remove the committed default key — instead synthesize a random per-process key in `_fernet()` when unset and `APP_ENV != "prod"` so dev still works without `.env`. Rotate any prod credentials that may have been written under the fallback. Add a regression test that boots the app with `APP_ENV=prod` + empty key and asserts startup fails.

### SEC2-003: Session tokens stored in plaintext (refines SEC-006)

Severity: **High**

Evidence:
- `backend/app/core/auth.py:68-83` — `new_session_token()` returns `secrets.token_urlsafe(48)` which is stored verbatim as `UserSession.token`.
- `backend/app/core/deps.py:26-28` — auth lookup is `db.query(UserSession).filter(UserSession.token == token).first()`, comparing the raw cookie value to the DB column directly (also non-constant-time, see SEC2-013).
- `backend/app/core/auth.py:86-91` — `revoke_session()` looks up by raw token.

Impact:

Same as SEC-006. Any DB compromise — backup, replica, log line, dev export, accidentally-public Postgres — yields a list of bearer tokens that are immediately valid (sessions live 30 days by default, see `config.py:15`). Invitation tokens were correctly fixed in 905bf11 (`invitations.py:113` stores `token_hash`); the auth path was not.

Fix instruction:

Mirror the invitations fix. Store `sha256(token).hexdigest()` (or HMAC-SHA-256 keyed by `SESSION_SECRET`) in `UserSession.token_hash`; set the raw token only on the cookie. Update create / lookup / revoke paths. Add an alembic migration that drops the `token` column and forces re-login (existing sessions invalidated). Add a regression test.

### SEC2-004: `/api/workspaces/{ws}/switch` is unauthenticated and unvalidated (extends SEC-004)

Severity: **High**

Evidence:
- `backend/app/api/routes/workspaces.py:231-250` — `switch_workspace` signature is `(workspace_id: str, response: Response)`. No `CurrentUser`, no `CurrentWorkspace`, no membership check, no UUID parse, no Origin check.
- The path argument is typed `str` so the cookie is set to *whatever string the path contains*. `core/deps.py:62-65` parses it later with `try/except ValueError` and silently falls through, so a malformed value just causes the next request to fall back to the first membership.
- The route is a state-mutating POST that accepts a path parameter only — perfect CSRF target (SEC2-001).

Impact:

Existing SEC-004 marked this **Medium**. With CSRF on the table this is **High**: an attacker page can force any logged-in admin to silently switch workspaces, then chain that with another CSRF (e.g. `POST /api/parts/bulk-delete`) executed under the wrong tenant context. The route bypasses every other workspace-isolation guard because no other layer re-checks membership when the cookie is mutated.

Fix instruction:

Require `CurrentUser`, parse `workspace_id` as `UUID`, look up `WorkspaceMember` to confirm `status='active'`, and return 404 otherwise. Add the Origin check from SEC2-001. Optionally rate-limit (cookie thrash is a DoS vector against the auth-lookup path). Add tests covering: missing user → 401; unknown workspace → 404; non-member → 403; happy path → 200.

### SEC2-005: Sentry scrubber redacts only `/api/workspaces` bodies (extends SEC-002)

Severity: **High**

Evidence:
- `backend/app/main.py:28-43` — `_scrub_event` removes `request.data` only for `method in ("PATCH","POST") and "/api/workspaces" in url`.
- `backend/app/main.py:67` — `send_default_pii=True` is on by design, which Sentry documents as shipping request bodies, headers, and IPs.
- Sensitive POST/PATCH bodies that escape redaction:
  - `auth.py:62` `POST /api/auth/signup` — carries `password` plaintext.
  - `auth.py:94` `POST /api/auth/login` — carries `password` plaintext.
  - `invitations.py:164` `POST /api/invitations/accept` — carries the raw invitation token (still bearer-equivalent until accepted).
  - `parts_provider.py:22` `POST /api/parts/lookup-mpn` — server-side decrypts API keys for the upstream call; a 5xx during MPN lookup may attach the surrounding scope.
  - `parts.py:820` `POST /api/parts/bulk-import-from-scan` — decrypted API keys are in scope while the request runs.
  - `attachments.py:103` `POST /api/attachments` — multipart (typically scrubbed by Sentry's default size cap, but `send_default_pii=True` overrides it).
- Sentry SDK breadcrumbs and local-variable capture (`with_locals=True` is default for `send_default_pii=True`) will pull `payload.password` / `decrypt(...)` return values out of stack frames if a 5xx surfaces from one of these handlers.

Impact:

Existing SEC-002 already flagged this. The post-fix-attempt scrubber still passes login/signup credentials, invite tokens, and decrypted provider API keys to Sentry on any 5xx that occurs in or below those handlers. A single `db.commit()` failure on signup ships the user's plaintext password to Sentry breadcrumbs. The fix in `_scrub_event` was scoped too narrowly.

Fix instruction:

Default-deny `request.data` for **all** PATCH/POST routes, then re-enable on a small read-only allow-list (e.g. `GET` + `/api/health` + a few diagnostic POSTs you trust). Additionally, scrub `frames[*].vars.password`, `vars.api_key`, `vars.api_secret`, `vars.scanner_license_key`, `vars.token`, `vars.payload.*` for those field names in event/breadcrumb walks. Set `with_locals=False` if you don't need them. Add tests that capture the event at the `before_send` boundary and assert no plaintext appears.

### SEC2-006: Provider asset download is vulnerable to SSRF (extends SEC-003)

Severity: **High**

Evidence:
- `backend/app/domain/parts/services/assets.py:79` — only checks `url.lower().startswith(("http://", "https://"))`. No host allow-list.
- `backend/app/domain/parts/services/assets.py:60` — `httpx.Client(timeout=_TIMEOUT_SEC, follow_redirects=True)` — redirects are followed, so an attacker can return `Location: http://169.254.169.254/...` from an attacker-hosted upstream.
- The `url` ultimately reaches `fetch_provider_asset` from values returned by `MouserProvider.lookup_mpn` / `DigiKeyProvider.lookup_mpn`. Mouser is a fixed, trusted upstream (good). DigiKey too. **However**: a workspace admin sets the provider's API key/endpoint configuration; in test or staging the workspace operator can point Mouser at any URL by abusing the API-key field if a future provider class ever lets the workspace configure a base URL. Today the providers are hardcoded to Mouser/DigiKey hosts (digikey.py:25, mouser.py:12) — but `assets.py` has no enforcement of that, and the asset-fetch pulls from `result["image_url"]` / `result["datasheet_url"]` which are returned by upstream and **not validated**.
- A compromised or malicious upstream Mouser/DigiKey response can return `image_url: http://10.0.0.1:8500/admin` (internal) or `http://169.254.169.254/latest/meta-data/iam/security-credentials/` (cloud metadata) and trigger a server-side fetch with the response cached on disk + served same-origin.
- Body cap is 10 MB; content-type allow-list (`_EXT_BY_MIME`) still includes `image/svg+xml` (line 31).

Impact:

The downloaded body is stored under `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}` and served by `GET /api/parts/assets/{ws_id}/{filename}` (parts.py:156) **inline** with `Cache-Control: immutable` and no `X-Content-Type-Options: nosniff`. An attacker who controls the asset URL (provider compromise, MITM on TLS misconfiguration, or a future user-supplied URL field) can:
1. Probe internal services (SSRF) and read whatever a 200 returns.
2. Cache an SVG with embedded `<script>` and link a victim to a workspace-internal URL — the SVG runs JS in the API's origin.
3. Cache HTML-as-other-extension and trigger MIME sniffing on legacy browsers.

SEC-003 already flagged the SVG/`octet-stream` parts; the SSRF and redirect-following paths are new.

Fix instruction:

Restrict outbound `_http_get` to the provider host allow-list (mouser.com, digikey.com, mediacdn.digikey.com, etc.). Disallow `follow_redirects=True` or restrict redirect targets to the same allow-list. Reject private/loopback/link-local IPs after DNS resolution (use `ipaddress.ip_address(socket.gethostbyname(host)).is_global`). Drop `image/svg+xml` from `_EXT_BY_MIME`. On the serve side (`parts.py:156`), add `X-Content-Type-Options: nosniff` to the response, and serve datasheets with `Content-Disposition: attachment` (matching the attachments path) rather than inline.

### SEC2-007: BOM import unbounded base64 → memory exhaustion (extends SEC-007)

Severity: **High**

Evidence:
- `backend/app/domain/projects/schemas.py:70` — `BomImportPreviewIn.text_b64: str` (no max_length).
- `backend/app/domain/projects/schemas.py:99` — `BomImportCommitIn.text_b64: str` (no max_length).
- `backend/app/domain/projects/bom_import.py:27-28` — `_decode_b64(b64)` calls `base64.b64decode` on the full payload; a 100 MB base64 payload allocates ~75 MB in one go.
- `bom_import.py:75` — `[list(r) for r in rows_iter if any(c.strip() for c in r)]` materialises every row in memory before any limit applies.
- `bom_import.py:92` — preview only takes `body[:200]` for the **response**, but the parse step has already buffered the entire decoded text in `text` and the entire row list in `all_rows`.
- FastAPI default JSON body limit comes from Starlette/h11 — there is **no** explicit middleware-level cap; `client_max_body_size 25m` in nginx (`deploy/nginx-web.conf:17`) is the only ceiling, and direct hits on the backend port bypass it.

Impact:

A signed-in member can post a 25 MB JSON containing a single base64 string. The route decodes it (75 MB), decodes it as text (75 MB), splits it into rows (potentially millions), and only then trims for preview. Concurrent uploaders can fill the worker's memory and OOM the container. With `--workers 1` (compose.prod), one bad request crashes the API.

Fix instruction:

Add `text_b64: str = Field(..., max_length=5_000_000)` (≈3.75 MB raw) to both schemas. After `_decode_b64`, assert `len(raw) < cap` and `len(all_rows) < ROW_CAP` (e.g. 10 000). Cap mapping list length and per-cell size. Reject with 413 / 422 instead of partial parse. Add an integration test that posts a 10 MB payload and asserts 413.

### SEC2-008: Public catalog token is fetched with non-constant-time SQL match

Severity: **Medium**

Evidence:
- `backend/app/api/routes/catalog.py:25-38` — `_resolve_workspace` does `Workspace.catalog_token == token` (Postgres `=` on String — early-mismatch byte comparison).
- `backend/app/api/routes/workspaces.py:144` — token is `secrets.token_urlsafe(32)` (256 bits — brute-force infeasible).
- The route is unauthenticated (`/catalog/{token}` is mounted with `dependencies=[]`).

Impact:

In theory, Postgres' string equality on a btree index is not constant-time. With a 256-bit token brute-force is infeasible regardless, but timing leaks can hint at *prefix match length* if an attacker can issue thousands of requests. Combined with the absence of rate-limiting on `/catalog/*` (rate limit lives on `/api/auth` and `/api/sentry-tunnel`, not catalog), an attacker can probe at sustained rates from a single IP. Also: the `catalog.html` route returns the *workspace name* (catalog.py:127 `<h1>{name}</h1>`) on token match, leaking organisation identity.

Fix instruction:

Apply `@limiter.limit("60/minute")` (or similar) to `catalog_html` and `catalog_json`. Even without timing-attack mitigation that's enough headroom to make brute-force economically infeasible. Optionally HMAC-key the lookup column (`HMAC-SHA-256(SESSION_SECRET, plaintext_token)` indexed; constant-time hash compare). Document the leakage of workspace name on token match in `_render_html` and decide whether that matches the operator's expectation.

### SEC2-009: Public catalog HTML rendered without CSP / nosniff / frame-options

Severity: **Medium**

Evidence:
- `backend/app/api/routes/catalog.py:115-133` — `_render_html` returns user-supplied content (workspace `name`, part `name/manufacturer/mpn/footprint/description`) inside `<title>`, `<h1>`, `<td>`, escaped via `html.escape` (correct).
- `catalog.py:117-124` — response sets no security headers: no `Content-Security-Policy`, no `X-Frame-Options`, no `X-Content-Type-Options`, no `Referrer-Policy`. `<meta name='robots' content='noindex,nofollow'>` is the only meta header.
- The HTML is served same-origin to the SPA (`/catalog/...` lives under `parts.matescb.cz`), so any HTML injection in this page becomes a same-origin XSS for the SPA's session cookie.

Impact:

`html.escape` covers the four classic chars (`<>&"`) and is what's needed for text nodes — so a stored XSS via this exact rendering is unlikely today. But: the surface is broad (every part field plus workspace name), there's no defence-in-depth, and the page is publicly cacheable. A future change that adds `mark_safe` / a markdown-rendered description would silently become a stored XSS that runs under the same origin as the authenticated SPA. The lack of `X-Frame-Options: DENY` on the SPA also means the attacker can frame the catalog page on a phishing site to dress up an exploit.

Fix instruction:

Add response middleware (or per-response headers) that sets `Content-Security-Policy: default-src 'self'; script-src 'none'; style-src 'unsafe-inline'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, and `Permissions-Policy: ()`. The CSP `script-src 'none'` is safe here because the page has no script. Apply HSTS (`Strict-Transport-Security: max-age=31536000; includeSubDomains`) at the nginx layer (`deploy/nginx-web.conf`) since TLS is terminated upstream. Add tests asserting the headers are present.

### SEC2-010: SPA / API responses ship no security headers

Severity: **Medium**

Evidence:
- `backend/app/main.py:125-131` — only middleware mounted is `CORSMiddleware`. No security-headers middleware (e.g. `secure-headers`, `starlette-csp`).
- `deploy/nginx-web.conf` — no `add_header Strict-Transport-Security`, no `add_header X-Content-Type-Options`, no `add_header X-Frame-Options`, no `add_header Content-Security-Policy`.
- `deploy/parts.matescb.cz.conf` (Apache) — only sets `ProxyPreserveHost`. No HSTS header pin.
- The browser fetches the SPA from the same origin as the API, so a stored XSS anywhere in the codebase has full session-cookie access.

Impact:

A modern web app with cookie-based auth and uploaded user content (datasheets, images, public catalog HTML) needs basic browser-side hardening as a default. Their absence is not directly exploitable but provides zero defence-in-depth: a future MIME-sniffing bug, a future inline-SVG, a future inline-`<script>` in catalog HTML, all become RCE-class problems instead of degraded-experience problems.

Fix instruction:

In `deploy/nginx-web.conf` add to the `server { }` block:

```
add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;
add_header Referrer-Policy same-origin always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; connect-src 'self' https://*.ingest.sentry.io; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'" always;
```

(Adjust CSP `connect-src` to match Sentry tunnel + any allow-listed CDN.) Apply a matching `Header always set Strict-Transport-Security` at the Apache layer in `deploy/parts.matescb.cz.conf` so the public TLS terminator owns the canonical HSTS policy.

### SEC2-011: Asset response has no `nosniff` and serves user-influenced content inline

Severity: **Medium**

Evidence:
- `backend/app/api/routes/parts.py:176-189` — `get_provider_asset` returns `FileResponse(abs_path, headers={"Cache-Control": ...})` and (when `?name=` is given) `Content-Disposition: inline`. **No** `X-Content-Type-Options: nosniff`. **No** explicit `media_type=` — `FileResponse` infers from extension.
- `backend/app/domain/parts/services/assets.py:54-55` — fallback extension is `bin` for unrecognised content-type, but earlier `_EXT_BY_MIME` still maps `image/svg+xml → svg` (extends SEC-003).
- `backend/app/api/routes/attachments.py:209-214` — handles this correctly: forces `application/octet-stream` for non-allow-listed MIMEs and `content_disposition_type="attachment"`. The provider-assets path does not have parity.

Impact:

A provider response that returns SVG (Mouser/DigiKey have served them historically as part-image previews) is downloaded, content-addressed, and re-served inline at `/api/parts/assets/{ws}/{sha}.svg`. SVGs can carry `<script>` and run as same-origin JS, exfiltrating the session cookie. Even non-SVG: legacy IE/Safari MIME-sniff variants can interpret an `image/jpeg`-declared body as HTML. The fix used in `attachments.py` was not propagated.

Fix instruction:

Mirror the attachments hardening here: drop `image/svg+xml` from `_EXT_BY_MIME`, sniff magic bytes server-side, default to `octet-stream` for anything outside the allow-list, set `X-Content-Type-Options: nosniff` on every response, and force `Content-Disposition: attachment` for non-image MIMEs. Add a test that downloads an asset whose body starts with `<svg ...>` and asserts the response carries `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`.

### SEC2-012: Workspace patch returns provider/scanner secret-flag info to admin only — but `/scanner-license-key` returns plaintext to any role (extends SEC-005)

Severity: **Medium**

Evidence:
- `backend/app/api/routes/workspaces.py:84-93` — `GET /api/workspaces/current/scanner-license-key` calls `decrypt(ws.scanner_license_key)` and returns the plaintext. The route declaration has **no** `dependencies=[Depends(require_role(...))]`.
- The router itself has no router-level role gate — it's mounted at `app.include_router(workspaces.router, prefix="/api/workspaces", ...)` in `main.py:141` with **no** `_member_gate`.
- Compare with `parts`/`storage`/`stock`/etc. (`main.py:142+`) which all have `dependencies=_member_gate` (`require_member_for_writes`). Workspaces router gets the cookie-based `CurrentWorkspace` dep on the route handler, so a viewer with valid session + valid workspace cookie reaches the handler and gets the plaintext.

Impact:

Existing SEC-005 already noted this. After ff867d4 the column is now encrypted at rest, but the read endpoint still returns plaintext to any active member, including `viewer`. A viewer-role user can cheaply harvest paid Scandit license keys for every workspace they're viewing — a directly monetisable leak.

Fix instruction:

Add `dependencies=[Depends(require_role("member"))]` to `current_scanner_license_key` (the policy line: only roles allowed to actually scan should be able to retrieve the SDK key). For defence-in-depth, consider scoping the key to a short-lived signed token endpoint that the SDK trades in once at mount time, rather than returning the raw key on every page load. Add an RBAC test: `viewer → 403`, `member → 200`.

### SEC2-013: Auth lookups use non-constant-time string compare

Severity: **Medium**

Evidence:
- `backend/app/core/deps.py:26` — `db.query(UserSession).filter(UserSession.token == token).first()`.
- `backend/app/core/auth.py:89` — `db.query(UserSession).filter(UserSession.token == token).first()`.
- `backend/app/api/routes/invitations.py:171-178` — `WorkspaceInvitation.token_hash == _hash_token(payload.token)`. The hash compare is constant *byte-length* but the `==` is still SQL `=` over the indexed btree, so prefix-mismatch leaks back via timing.
- `backend/app/api/routes/catalog.py:29` — `Workspace.catalog_token == token`. Same.

Impact:

256-bit tokens make brute-force infeasible end-to-end. Timing oracles via Postgres btree comparison are weak in practice (network jitter dwarfs them) but this is a defence-in-depth issue rather than an immediate vulnerability. Worth fixing alongside SEC2-003 since the moment you store hashed tokens, the right primitive is `hmac.compare_digest` against the candidate hash retrieved by user_id / by-id, not a SQL `WHERE`.

Fix instruction:

After SEC2-003 is fixed, store an HMAC of the token keyed by `SESSION_SECRET`, look the row up by an unkeyed-cheap index (e.g. user_id from a separate signed cookie), and compare the candidate HMAC to the stored HMAC with `hmac.compare_digest`. Same approach for invitations and catalog tokens. Document the rationale.

### SEC2-014: No login lockout / signup bot protection beyond IP rate-limit

Severity: **Medium**

Evidence:
- `backend/app/api/routes/auth.py:62-63` — signup `@limiter.limit("5/hour")` per IP.
- `backend/app/api/routes/auth.py:94-95` — login `@limiter.limit("10/minute")` per IP.
- `backend/app/core/ratelimit.py:23` — `enabled=settings().APP_ENV == "prod"` — disabled outside prod.
- `backend/app/core/auth.py:39-58` — `validate_password_strength` from 205ade0 has a 30-entry blocklist. No HIBP / k-anonymity check; entropy heuristic is `len(set(password)) >= 4`.
- No per-account lockout, no "first failed login → require captcha", no MFA.

Impact:

The IP-bucketed slowapi cap is the only deterrent against (a) password stuffing across many compromised credentials, (b) signup abuse for spam / resource exhaustion. A botnet bypasses the per-IP cap trivially. There is no per-account counter that would catch "10 different IPs all trying user@x.com once per minute". The 30-entry blocklist after 205ade0 is a fig-leaf; HIBP-Pwned-Passwords k-anonymity (5-char SHA-1 prefix lookup, no plaintext leaves the server) costs nothing operationally and catches ~99% of real breaches. Signup → personal workspace creation is automatic (`auth.py:78-81`); a bot can create thousands of workspaces under different inboxes with no email-verification gate.

Fix instruction:

Add a per-account login failure counter that locks the account for N minutes after M failed attempts (independent of IP). Wire HIBP k-anonymity to the password validator (`requests.get(f"https://api.pwnedpasswords.com/range/{sha1(password)[:5]}")` then check the suffix). Require email verification before workspace creation: signup creates a `pending_users` row, sends a token, and the actual `User` + `Workspace` rows are only created on verify-click. Add MFA (TOTP) as an optional opt-in for admins/owners.

### SEC2-015: Session does not rotate on login or privilege change; long lifetime, no idle expiry

Severity: **Medium**

Evidence:
- `backend/app/core/auth.py:76-83` — `create_session_row` mints a fresh row but the route never invalidates a pre-existing session.
- `backend/app/api/routes/auth.py:96-108` — login does not call `revoke_session` for the prior cookie if present. A successful login under an existing session leaves the old token live until natural expiry.
- `backend/app/core/auth.py:72-73` — `expires_at = now + 30 days` (`SESSION_LIFETIME_DAYS`). No sliding renewal, no idle timeout, no rotation on workspace switch / role change.
- `backend/app/api/routes/workspaces.py:193-214` — `patch_member` can demote/promote without touching `UserSession`; the demoted user's existing tokens remain valid until 30 days post-mint.

Impact:

Stolen-cookie window is up to 30 days. Compromised-session-replay after privilege downgrade goes undetected: an admin demoted to viewer keeps admin authorities for the cookie's remaining TTL because `_membership_role` reads the live DB role on every request — wait, actually that's correct; the session's `user_id` is fixed but the role lookup is live (deps.py:84-94), so role downgrade is enforced. **However**, session **revocation** isn't, so a logout from another device doesn't invalidate the active cookie. And ownership theft of a user account (via password reset bypass — there's no reset flow yet, but the absence of session rotation will bite when one is added) keeps prior cookies valid.

Fix instruction:

On every login, revoke any existing session for the same user (or rotate it). Add a sliding-expiry mechanism: `last_used_at` updated on each request; reject sessions with `last_used_at < now - 24h`. On password change / role change, force-revoke all sessions for that user. Cap `SESSION_LIFETIME_DAYS` to 7 by default; let opt-in remember-me extend. Add a `GET /api/auth/sessions` + `DELETE /api/auth/sessions/{id}` so a user can review/kill active sessions.

### SEC2-016: Backend dependencies use unbounded `>=` ranges — no lockfile

Severity: **Medium**

Evidence:
- `backend/pyproject.toml:5-23` — every dep is `>=`: `fastapi>=0.115`, `sqlalchemy>=2.0`, `psycopg[binary]>=3.2`, `cryptography>=42.0`, `httpx>=0.27`, `sentry-sdk[fastapi]>=2.18`, `slowapi>=0.1.9`, `python-multipart>=0.0.9`, `argon2-cffi>=23.1`, `email-validator>=2.2`, `chardet>=5.2`, `itsdangerous>=2.2`, `pydantic>=2.7`, `pydantic-settings>=2.4`.
- `backend/Dockerfile` (per CLAUDE.md and INFRA-004) does `pip install -e .` against this open-ended pyproject.
- No `requirements.txt`, no `pdm.lock`, no `poetry.lock`, no `uv.lock` in the backend tree.

Impact:

Every redeploy can pick up new minor/patch versions of every dep. Specifically:
- `cryptography` <44.0.1 has CVE-2024-12797 (OpenSSL bundled, CBC oracle); a future redeploy could pin you to a *newer* fixed version, but it could also pin you to a version that re-introduces something. More importantly, *prod and CI may run different versions.*
- `python-multipart` <0.0.18 has CVE-2024-53981 (DoS via malformed multipart). A `>=0.0.9` range covers the vulnerable window.
- `sentry-sdk` <2.21.0 has CVE-2024-40647 (env-var leak). `>=2.18` covers vulnerable version.

Without a lockfile this is a release-day-roulette problem. Production-only breakage on dep churn is also INFRA-004; mirrored here because the missing lock is also a *security* control (you can't audit-pin what you can't enumerate).

Fix instruction:

Adopt `uv` or `pip-tools`. Generate a hashed lockfile (`uv lock` / `pip-compile --generate-hashes`) and commit it. Have the Dockerfile install from the lockfile (`pip install --require-hashes -r requirements.lock`). Add a CI job that fails when the lock differs from `pyproject.toml`. Run `pip-audit` (or `uv tool run pip-audit`) on the lockfile in CI as a separate step.

### SEC2-017: Rate-limit posture is per-IP only; key-by-IP is bypassable behind shared NAT and per-process

Severity: **Medium**

Evidence:
- `backend/app/core/ratelimit.py:21-24` — `Limiter(key_func=get_remote_address, enabled=APP_ENV == "prod")`, in-memory bucket store.
- `docker-compose.prod.yml:87` — `--workers 1` — load-bearing per CLAUDE.md "Things that have bitten us".
- The only state mutating endpoints with rate limits are signup (5/h), login (10/min), invite-accept (10/min), sentry-tunnel (60/min). No rate limit on:
  - `parts.bulk_import_from_scan` (200 rows × upstream provider call → cheap fan-out).
  - `parts.refresh_from_provider` (one upstream call per part).
  - `parts.bulk_delete` (no rate limit, archives 100 parts per call).
  - Catalog reads (already noted in SEC2-008).

Impact:

A malicious member can call `refresh-from-provider` in a tight loop and burn the workspace's Mouser/DigiKey API quota (which is per-key, paid). Same for `bulk_import_from_scan`. The IP-bucket disabled outside prod means staging has no rate-limit backstop, and a single hostile member can rack up a real bill against a workspace whose owner pays.

Fix instruction:

Add per-workspace rate-limits on the provider-fanout endpoints (e.g. `@limiter.limit("60/minute", key_func=lambda req: req.state.workspace_id if hasattr(req.state, "workspace_id") else get_remote_address(req))`). Plumb `workspace_id` into `request.state` from `get_current_workspace`. Long-term: switch slowapi to a Redis backend so prod can scale beyond `--workers 1`. Add monitoring for upstream-quota exhaustion and notify the workspace owner.

### SEC2-018: OpenAPI / docs disabled in prod, but `/api/health` leaks no version info — verify nothing else does

Severity: **Low**

Evidence:
- `backend/app/main.py:111-114` — `docs_url=None`, `redoc_url=None`, `openapi_url=None` in prod (good — closes the SEC discovered surface).
- `backend/app/main.py:172-174` — `/api/health` returns a static `{"status": "ok"}` (safe).
- `backend/app/main.py:106-108` — `FastAPI(title="Parts Inventory & Production Manager", version="0.1.0")` — version is in the Server header by default but the OpenAPI surface is off.
- 5xx responses log to stdout via `responses.py:42-51`. The body returned to the client is `err(...)` which spreads `exc.detail` keys — non-debug stack traces are *not* leaked, but a programmer raising `HTTPException(detail={"trace": traceback.format_exc()})` would inadvertently leak. No safeguard.

Impact:

Low-severity. The only realistic leak is the `Server: uvicorn` header (ID's the stack to attackers who don't already know). The `responses.py` envelope handler is correct today.

Fix instruction:

In nginx, strip the upstream `Server` header (`proxy_hide_header Server; server_tokens off;`). Add a CI grep that fails the build if any `HTTPException(detail=` includes the strings `traceback`, `format_exc`, or `__class__`.

### SEC2-019: Catalog token is a workspace-wide secret with no rotation cadence and no revocation list

Severity: **Low**

Evidence:
- `backend/app/api/routes/workspaces.py:96-145` — `regenerate_catalog_token` rotates the token by overwriting `ws.catalog_token`. No history, no revocation list.
- `backend/app/api/routes/catalog.py:29` — match is purely on `Workspace.catalog_token == token`. Once rotated, prior URLs 404 immediately (good).
- However, an admin who shared a catalog URL on Slack cannot tell who's accessing it; no per-recipient tokens, no audit log.

Impact:

Operationally weak rather than directly exploitable. If a catalog URL leaks (Slack screenshot, LLM crawler, search-engine cache despite `noindex`), the only remediation is full rotation, which invalidates every legitimate consumer at once.

Fix instruction:

Optional: support multiple catalog tokens per workspace (so old ones can be revoked individually), each with a label and `last_used_at` for audit. Or: gate `/catalog/{token}` behind a one-shot exchange to a short-lived signed JWT. Document the share-link blast radius for operators.

### SEC2-020: Provider HTTPS calls don't pin certificates; httpx defaults are fine but hardening is worth noting

Severity: **Low**

Evidence:
- `backend/app/domain/parts/providers/mouser.py:18`, `digikey.py:47`, `services/assets.py:60`, `sentry_tunnel.py:111` — all use `httpx.Client()` / `httpx.AsyncClient()` with default TLS (system trust store). No `verify=False`, no pinning. Default `httpx` validation is on.
- `pyproject.toml:23` — `httpx>=0.27` (which uses `certifi` and validates by default). No `trust_env` overrides anywhere.

Impact:

Low. TLS validation is on, so this is a passing audit on the default. The note is for future-proofing: a future contributor might disable verification for "test" hosts (CVE-pattern). No issue today.

Fix instruction:

Add a CI grep that fails the build on `verify=False`, `trust_env=False`, or `ssl=False` patterns under `backend/`. Document the policy in CLAUDE.md.

## Coverage gaps

- **Frontend security**: I did not audit `web/` for DOM-based XSS sinks (`dangerouslySetInnerHTML`, `innerHTML`, unsanitised `<a href>`). The frontend agent should pick this up (`web/src/lib/api.ts`, `web/src/routes/**`, especially anywhere markdown is rendered).
- **Live secret values**: No `.env` / `.env.prod` files were on disk in this checkout (gitignored). The audit cannot confirm prod actually has `WORKSPACE_SECRETS_KEY` set or that the value is unique; SEC2-002's risk is contingent on the operator filling it in.
- **`workspace-isolation-checker` subagent**: not invoked. Spot-checks across `parts.py`, `orders.py`, `builds.py`, `projects.py`, `storage.py`, `lots.py`, `tags.py`, `attachments.py`, `custom_fields.py` show consistent `ws.id` filtering and `assert_in_workspace` usage on FK lookups. The historical leaks listed in 2026-04-30 review (f56d84d "close remaining cross-workspace FK leaks") have been addressed. A formal cross-workspace test pass is still the right belt-and-braces step.
- **Apache layer (parts.matescb.cz)**: I read the in-repo Apache config but the live VPS config (with certbot's TLS vhost) is not in the repo. HSTS / TLS protocol config / cipher suites cannot be confirmed from the repo alone.
- **Provider response signing**: Mouser / DigiKey responses are trusted as-is. I did not verify whether the providers offer response signing or a stricter content-type contract that would let `assets.py` accept fewer formats.
- **Database-level row security**: by design, isolation is in app code only (CLAUDE.md hard invariant). I did not propose pg-side RLS as a finding because that would conflict with the documented architecture; flagged only that the discipline must be maintained on every new endpoint.
- **Backup contents**: SEC2-002 flags the encryption-key-in-repo risk on backups. I did not audit `deploy/backup.sh` end-to-end (covered by INFRA agent).
- **`pip-audit` / `npm audit`**: not run (operator instruction was no mutating-network commands). SEC2-016 flags the dep-range hygiene that makes a CVE-check meaningful.
