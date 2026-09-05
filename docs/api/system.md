# System API

Audience: engineer

The two build/liveness probes declared directly on the app object in `backend/app/main.py`, outside any router: `GET /api/health` and `GET /api/version`.

## Conventions

See [API conventions](./README.md) for the envelope and error shapes. Neither route is workspace-scoped and neither writes to `audit_log` — there is no workspace context and no mutation.

## Routes

### `GET /api/health`

Liveness + DB + uploads-volume probe. **Unauthenticated** — the compose healthcheck, the post-deploy CI gate and the uptime monitor all call it without a credential.

Response shapes (200 and 503) are documented in [deployment.md — Health endpoint](../deployment.md#health-endpoint), which is where on-call reads them; `tests/test_deploy_doc_snippets.py` keeps that section honest. Not repeated here.

### `GET /api/version`

Which commit this backend was built from.

**Auth** — required, and this is the one deliberate difference from `/api/health`. Health has to answer anonymously because the deploy path holds no credential; nothing needs a build SHA anonymously, and an unauthenticated build fingerprint tells a scanner which commit's known issues to try. `CurrentUser` accepts either credential (session cookie or personal access token) and does **not** resolve a workspace: the build id is a property of the server, identical for every tenant.

**Response** — `200`

```json
{ "data": { "build": "0123456789ab" }, "status": { "category": "ok", "message": "OK" } }
```

- `build` — the value of the `SENTRY_RELEASE` env var, which `docker-compose.prod.yml` populates from the deploy's `git rev-parse --short=12 HEAD`. It is also the Sentry release tag, so the string maps a bug report to a deployment and to its stack traces.
- `null` when `SENTRY_RELEASE` is unset (local dev, tests) — reported as `null` rather than `""` so a client can distinguish "not built by CI" from a real identifier.

**Why not `pyproject.toml`'s version** — `backend/pyproject.toml`, `web/package.json` and `FastAPI(version=…)` all read `0.1.0` and have not changed since the initial commit; there are no git tags. The short SHA is the only identifier in this project that actually moves.

**Consumer** — the frontend `/about` page pairs this with `import.meta.env.VITE_APP_VERSION` (the same SHA, inlined into the SPA bundle at build time) and warns when the two differ. There is no staging environment and the auto-deploy builds the web and backend images separately, so a half-applied deploy is a real failure mode and two disagreeing SHAs are the cheapest way to spot it.

**Notes**

- Source: `backend/app/main.py`.
- Tests: `backend/tests/test_version.py`; also swept by `tests/test_agent_rest_smoke.py::SWEEP_PATHS` for token reachability.
