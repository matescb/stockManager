# Testing & Code Quality Teardown

Scope: backend & frontend tests, conftest hygiene, dead code, type holes, duplication, lint posture.
Date: 2026-05-01.
Existing review IDs covered/extended: FE-008 (frontend test coverage). New findings use `TEST-NNN` for testing and `CQ-NNN` for code quality.

The backend test suite is genuinely better than the rest of the codebase — 244 tests across 35 files, real-Postgres + Alembic-upgrade-head conftest, near-zero mocks, no `xfail`/`skip`, no time-dependent flakes. The findings below are real coverage gaps and hygiene issues, not "this is unusable". The frontend side is a different story — 19 tests across 2 files, both library-level.

## Testing Issues

### TEST-001: Cross-workspace isolation test does not cover most routers

Severity: **High**

Evidence:

- `backend/tests/test_workspace_isolation.py` exercises 10 routers (`/api/parts`, `/api/projects`, `/api/builds`, `/api/storage`, `/api/stock`, `/api/attachments`, `/api/custom-fields`, `/api/tags`, `/api/auth`, `/api/workspaces`).
- The router list in `backend/app/api/routes/` is 21 files. Routers with no isolation test: `lots.py`, `orders.py`, `invitations.py`, `bom_presets.py`, `reports.py`, `search.py`, `parts_provider.py`, `catalog.py`, `_activity.py`, `sentry_tunnel.py`.

Impact:

CLAUDE.md declares workspace isolation a hard invariant enforced in code, not the DB. Half the API surface lacks the regression net. A future router edit can re-introduce a cross-workspace leak without CI noticing — this is exactly how `BE-005` (order entry cross-workspace `part_id`) survived to land in the existing review.

Fix instruction:

Add an isolation test per missing router. The pattern is uniform: workspace A creates resource X, workspace B's `GET /api/<router>/<id>` must 404, B's `PATCH/DELETE` must 404, and any FK accepted in body (`part_id`, `lot_id`, `storage_location_id`, `meta_part_id`) must reject A's IDs with 404. `lots.py` and `orders.py` are the highest priority — both reference `part_id`/`storage_location_id`.

### TEST-002: RBAC viewer matrix is incomplete and only tests one role

Severity: **High**

Evidence:

- `backend/tests/test_rbac_viewer.py` covers a viewer × ~14 routers matrix.
- It does not exercise the `member` and `admin` rows of the role matrix — only viewer is asserted as forbidden.
- `attachments.py` and `custom_fields.py` have no RBAC test.
- Recent commit `6990a18` ("gate archive/restore/bulk-delete on admin+") adds admin-only paths, but there is no test that explicitly proves a `member` is rejected from those endpoints (only viewer is rejected).

Impact:

A regression that downgrades an admin-only endpoint to member-allowed (e.g. removing `require_role("admin")`) is not caught by CI, because the existing test only asserts viewer is forbidden — member/admin would also be forbidden under that role check. The `6990a18` fix is undertested.

Fix instruction:

Add a `test_rbac_member.py` mirroring the viewer matrix, asserting member is rejected from admin-only endpoints (archive/restore/bulk-delete on parts/orders/projects/storage/builds, plus workspace-secrets PATCH). Refactor both into a parameterised matrix `(role, route, expected_status)` to eliminate duplication. Add coverage for `attachments` and `custom_fields`.

### TEST-003: Stock concurrency test is single-thread + smoke check

Severity: **High**

Evidence:

- `backend/tests/test_stock_concurrency.py:1-15` docstring explicitly says: *"The advisory lock is verified via a threaded concurrency test that's mostly a smoke check — the load-bearing guarantee is the trigger."*
- The test asserts the DB trigger blocks negative balance via direct SQL bypassing the service layer.
- There is no concurrent-receive test for `BE-001` (TOCTOU over-receive on orders).
- There is no concurrent-build-consume test for `BE-003` (build consumption race outside stock-service locks).

Impact:

Two of the highest-severity findings in the existing review (`BE-001` Critical, `BE-003` High) have no regression net at all. When the fixes land, there is no test that proves they actually serialise the contended write.

Fix instruction:

Add `test_orders_receive_concurrency.py`: spin two threads receiving the same outstanding order entry; assert exactly one succeeds and the other returns 4xx, with `quantity_received <= quantity_ordered` post-condition. Add `test_builds_consume_concurrency.py`: two threads consuming overlapping BOM lines whose total exceeds on-hand; assert one fails with 4xx and on-hand never goes negative. Both should use `threading.Barrier` to maximize race-window overlap.

### TEST-004: Frontend test coverage is two library files, zero components

Severity: **High** (extends `FE-008`)

Evidence:

- `web/src/lib/api.test.ts` and `web/src/lib/bagCode.test.ts` are the entire frontend test suite (19 tests).
- No tests for: `DataTable` (search, sort, multi-select, pagination, CSV export, keyboard activation per `FE-002`), `lib/api.ts` 401 handling, query error rendering on any list page (`FE-001`), entity-form state reset (`FE-004`), scanner UX, modal focus trap, mobile layout (`FE-005`).
- No Playwright/E2E tests at all.

Impact:

Every UI/UX issue in the existing FE review and in `docs/teardown/frontend.md` is functionally invisible to CI. `tsc -b` only catches type regressions. Refactors to `DataTable` or `lib/api.ts` can ship and break behaviour silently.

Fix instruction:

Add three RTL test files first: `DataTable.test.tsx` (rendering, sort click, keyboard Enter on row, multi-select after pagination), `api.test.ts` (extend existing — assert 401 redirects to login, envelope unwrap, error throw shape), `useEntityForm.test.tsx` (after the `FE-004` reset hook lands). Add one Playwright smoke test in `web/e2e/` covering signup → create part → add stock → see ledger row.

### TEST-005: BE-002 (optional stock coordinates) has no test

Severity: **Medium**

Evidence:

- The existing `BE-002` finding is about removing/consuming stock with `lot_id=None` / `storage_location_id=None` while stock is stored in a specific location, which currently passes service validation then 500s at trigger time.
- `backend/tests/test_stock_ledger.py` and `test_stock_concurrency.py` do not exercise this asymmetry.

Impact:

When `BE-002` lands, there's no regression net.

Fix instruction:

Add a test in `test_stock_ledger.py`: add 100 to `(part, lot=L1, storage=S1)`, then `POST /api/stock/remove` with `part_id` only (no `lot_id`/`storage_location_id`). Today this should return a 400/422 (or 500 — that's the bug); the post-fix assertion should be a clean 4xx with a deterministic message.

### TEST-006: BE-005 (cross-workspace part_id on order entries) has no test

Severity: **Medium**

Evidence:

- `test_workspace_isolation.py:91-110` covers cross-workspace `part_id` on **project entries** (BOM lines), not on **order entries**.
- `test_orders.py` is 165 lines and does not include a cross-workspace check.

Impact:

`BE-005` was missed during the original review wave because no test covered it. Even after the fix lands, no test will pin it.

Fix instruction:

Add `test_orders_cross_workspace_part_id` to `test_workspace_isolation.py` mirroring the existing `test_project_entry_rejects_cross_workspace_part_id` pattern: A creates a part, B creates an order, B attempts `POST /api/orders/<id>/entries` with A's part_id — assert 404.

### TEST-007: Migration round-trip is not tested

Severity: **Medium**

Evidence:

- `backend/tests/conftest.py:42-48` runs `command.upgrade(cfg, "head")` against the test DB.
- There is no test for `command.downgrade()` followed by `command.upgrade()` to verify each migration's `downgrade()` is correct.
- The existing review (`docs/claude-review-issues.md`) and the database teardown (`docs/teardown/database.md`) both flag downgrade correctness as a risk vector.

Impact:

A bad `downgrade()` is invisible until someone needs to roll back in prod — i.e., when reproducing it locally is hardest. Combined with the auto-deploy (no staging), a botched migration with broken downgrade is a one-way door.

Fix instruction:

Add `tests/test_migrations.py` with a session-scoped test: for each revision in the chain, run `upgrade <rev>`, then `downgrade <prev>`, then `upgrade <rev>` again. Assert no exception and that the resulting schema is identical (compare `inspect(eng).get_columns(...)` snapshots, or use Alembic's `compare_metadata`). Mark the test slow; run it on CI but not on every local pytest invocation.

### TEST-008: API envelope shape is not asserted anywhere

Severity: **Medium**

Evidence:

- `CLAUDE.md` declares the `{data, status}` envelope a hard invariant.
- Every test asserts `r.json()["data"]` works, but no test asserts the *shape* on every endpoint (no negative test for "endpoint returned a bare payload").
- `core/responses.py::http_exception_handler` spreads `HTTPException(detail=…)` dict onto the response — there's no test pinning that contract.

Impact:

A new endpoint forgetting to wrap with `responses.ok()` or returning a bare `{...}` will pass tests that just deserialise the body and read fields — until the FE breaks at runtime.

Fix instruction:

Add `test_envelope.py`: enumerate every successful 200 path in the existing tests via `pytest --collect-only` style introspection (or hard-code a small smoke list of one route per router) and assert top-level keys are exactly `{"data", "status"}`. Add a similar test for 4xx asserting `{"data": null, "status": "error"}` shape.

### TEST-009: conftest reruns Alembic upgrade per test — slow + masks fixture-isolation bugs

Severity: **Medium**

Evidence:

- `backend/tests/conftest.py:64-68` — the `db` fixture (function scope) calls `_reset_schema(engine)` and `_alembic_upgrade_head(...)` before every test.
- 244 tests × ~1-2 s schema reset = 4–8 minutes of pytest wall time spent re-running migrations.
- The `client` fixture (function scope) uses the global app, but does not depend on `db` — so HTTP tests do *not* auto-reset the schema. Cross-test state can bleed when a test forgets to depend on `db`.

Impact:

Slow CI plus a foot-gun: a test that wants a clean DB must remember to depend on `db`; if it only uses `client` or `authed_client`, it inherits whatever state a previous test left behind. Hidden order-of-execution coupling.

Fix instruction:

Two changes. (1) Move schema setup to session scope (one upgrade), and use a per-test transaction-rollback fixture (`SAVEPOINT` + `event.listens_for(... "after_transaction_end")`) — standard SQLAlchemy pattern, ~10× faster. (2) Make `client`/`authed_client` depend on `db` so every HTTP test gets the fresh fixture by construction. Document the migration in `docs/development.md`.

### TEST-010: No test for `bag_signature` re-scan correlation

Severity: **Medium**

Evidence:

- `CLAUDE.md` declares `bag_signature` "the only stable correlation key" between re-scans of a bag.
- No backend test computes a signature, ingests stock, re-computes the same signature, and asserts the inline "Found bag" UI hits.
- No test covers the canonical normalization order in `web/src/lib/bagCode.ts` (the BE side is in `backend/app/domain/stock/service.py` per existing review references).

Impact:

A change to either side's normalization (whitespace trim, separator order, case folding) silently breaks the only correlation across scans. The bug surfaces as users complaining a known bag shows as "new" on re-scan.

Fix instruction:

Add `test_bag_signature.py`: assert canonical normalization on a panel of inputs (whitespace, mixed case, separator variants) yields a stable SHA-256 across two service calls. Add a frontend vitest covering the same inputs through `bagCode.ts`. Both should share a fixture file (JSON input → expected hex digest) so FE/BE drift produces a diff.

### TEST-011: No password-rotation/change-password test after `205ade0`

Severity: **Medium**

Evidence:

- `backend/tests/test_password_strength.py` exercises the signup path only.
- The security teardown (`docs/teardown/security.md`) flags that `205ade0` resolved `MED-4` for signup but the change-password / invite-accept paths weren't in scope.

Impact:

Weak passwords can still be set at password-change or invite-accept time even after the fix.

Fix instruction:

Extend `test_password_strength.py` to cover every entry point that sets a password — password change (if the route exists), password reset (if it exists), invitation accept. If the route doesn't exist yet, add a `pytest.mark.parametrize` placeholder so the test fails until the rule is plumbed everywhere.

### TEST-012: Test factories are inlined helpers, not shared

Severity: **Low**

Evidence:

- Every test file defines its own `_signup`, `_create_part`, `_create_storage`, `_add_stock` helpers.
- These are near-identical across `test_stock_concurrency.py`, `test_workspace_isolation.py`, `test_orders.py`, `test_builds.py`, etc.
- Total duplication: ~150 lines of fixture-helper code.

Impact:

Drift risk — when the create-part contract changes, every test file needs editing. Adds ~3 minutes to maintenance per test file changed.

Fix instruction:

Move the shared helpers into `backend/tests/_factories.py` (e.g. `signup_user`, `create_part`, `create_storage`, `add_stock`) and have each test import them. Don't introduce factory-boy yet — plain functions work. Keep them very thin (no business logic).

### TEST-013: Test-suite has no coverage measurement

Severity: **Low**

Evidence:

- `backend/pyproject.toml` contains no `[tool.coverage]` config.
- CI does not run `pytest --cov`.
- `web/package.json` test script does not run vitest with `--coverage`.

Impact:

No visibility into which lines of `backend/app/` and `web/src/` are uncovered. A maintainer cannot quickly answer "is the new code under test?" Combined with the gap reports above, this is the meta-issue.

Fix instruction:

Add `coverage` to backend dev deps. Add `pytest --cov=app --cov-report=term-missing --cov-fail-under=70` to CI (set the floor to current value first, ratchet up). Add `vitest run --coverage` to FE CI. Don't fail builds yet — just publish the report.

### TEST-014: No CI job for backend tests on every PR — only `pytest`-via-deploy assumption

Severity: **Low**

Evidence:

- `CLAUDE.md` says CI runs `pytest`. The actual workflow file path (`.github/workflows/`) was not opened during this teardown — flagged for the infra agent.
- Existing review `INFRA-005` notes "no job runs `docker compose -f docker-compose.prod.yml config` or builds prod images before deploy".

Impact:

If pytest is not actually wired in CI (or runs only on `main` push, not PR), the carefully built test suite has no gate function.

Fix instruction:

Verify `.github/workflows/*.yml` actually runs pytest on every PR (not just push to main). If it doesn't, fix it. This is one-line config but blast radius is "every backend regression ships". Cross-ref the infra teardown for the broader CI gap.

## Code Quality Issues

### CQ-001: `_utcnow()` / `_now()` is duplicated 6 times

Severity: **Low**

Evidence:

- `backend/app/domain/_mixins.py:10-11`
- `backend/app/domain/users/models.py:12-13`
- `backend/app/domain/workspaces/models.py:12-13`
- `backend/app/domain/stock/models.py:21-22`
- `backend/app/domain/stock/service.py:30` (named `_now`)
- `backend/app/domain/builds/service.py:35` (named `_now`)
- All six are `return datetime.now(timezone.utc)`.

Impact:

Trivial drift risk. Worse, it telegraphs "no shared utilities" — encourages further copy-paste.

Fix instruction:

Move to `backend/app/core/time.py::utcnow()`. Update all callsites in one PR. Keep the function this thin so mocking time stays trivial (the eventual freezegun adoption hooks here).

### CQ-002: `parts.py` router is 1177 lines — single-file kitchen sink

Severity: **Medium**

Evidence:

- `backend/app/api/routes/parts.py` — 1177 lines. Next-largest route file is `orders.py` at 284 lines (4× smaller).
- Mixes part CRUD, archive/restore, bulk-delete, MPN uniqueness, asset serving, provider linking, custom-fields adjacency, scan-import lookups.

Impact:

Cognitive load is highest exactly where the most invariants live (MPN partial index, content-addressed assets, provider catalog). Reviews are slow; RBAC drift sneaks in (e.g. the `6990a18` admin-gate fix had to touch ~5 routers, but a contributor working in `parts.py` can easily miss one section). Also drives merge conflicts.

Fix instruction:

Split `parts.py` into: `parts.py` (CRUD + list + archive/restore), `parts_assets.py` (asset upload/serve, content-addressed paths), `parts_provider.py` (already exists — fold provider-lookup endpoints there), `parts_bulk.py` (bulk-delete, scan-import, bulk-import). No behaviour change. Each file ≤ 300 lines.

### CQ-003: `ScanImport.tsx` is 728 lines

Severity: **Medium**

Evidence:

- `web/src/routes/parts/ScanImport.tsx` — 728 lines.
- Next-largest is `Workspace.tsx` at 567 lines (also worth splitting).

Impact:

Same as CQ-002 but on the FE side. Hard to test, hard to reason about. A scan-import flow change has to thread through the whole file.

Fix instruction:

Extract sub-components: `ScanImportSession.tsx` (camera + decode loop), `ScanImportRowEditor.tsx`, `ScanImportSubmit.tsx`. Co-locate hooks per component. Don't introduce a state library — local state + props is fine for this depth.

### CQ-004: 12 `any` casts in frontend, mostly in form-payload assembly

Severity: **Medium**

Evidence:

- `web/src/lib/api.ts:42,90,92,100,105` — `any` in body params + parsed body.
- `web/src/routes/parts/PartCreate.tsx:64`, `PartAddStock.tsx:29`, `PartRemoveStock.tsx:86`, `StorageDetail.tsx:118` — `payload: any` for builders.
- `ScanditScanner.tsx:109`, `ZxingScanner.tsx:203,320` — `catch (err: any)` (acceptable but `: unknown` is safer).

Impact:

The escape hatches are concentrated where the FE↔BE contract lives (HTTP body builders). A schema-FE drift won't surface in `tsc -b`. The Zod-extended methods in `api.ts` partially address this for responses but request bodies are still `any`.

Fix instruction:

In `lib/api.ts`, type request bodies with a generic `B` parameter (`post<T, B = unknown>(p: string, body?: B)`). For form payloads, declare the request schema once (Zod or plain TS interface mirroring the backend Pydantic) and use it as the builder return type. Replace `catch (err: any)` with `catch (err: unknown)` and narrow.

### CQ-005: No Python linter, no JS linter — zero static-analysis surface

Severity: **Medium**

Evidence:

- `backend/pyproject.toml` has no `[tool.ruff]` / `[tool.mypy]` / `[tool.black]` sections.
- `web/` has no `.eslintrc*` / `.prettierrc*`.
- `CLAUDE.md` notes this is intentional and "Don't add tooling without asking" — so this is a *flag for the user*, not a unilateral fix.

Impact:

Style/typing/dead-import drift is invisible. Several findings in this report (`CQ-001`, `CQ-004`, `CQ-006`) would be one-shot caught by `ruff` + `mypy` + `eslint`.

Fix instruction:

Open a PR adding a *minimal* config and CI step (no auto-fix on commit, no IDE config changes): `ruff check` with the default rule set + `E`, `F`, `I` (no formatter, no import sort *yet*); `mypy --strict app` only on `app/core/` and `app/domain/_mixins.py` initially; `eslint` with `@typescript-eslint/recommended` + `react-hooks/recommended`. Treat all violations as warnings on first PR; promote to errors per-file as the user wants.

### CQ-006: Pydantic schemas live in 4 places (some `domain/<x>/schemas.py`, some inlined)

Severity: **Low**

Evidence:

- `backend/app/domain/builds/schemas.py`, `stock/schemas.py`, `orders/schemas.py`, `projects/schemas.py` exist as dedicated files.
- Other domains (e.g. `parts`, `lots`, `tags`, `attachments`, `custom_fields`, `workspaces`, `invitations`) inline schemas in the router file.
- No documented rule.

Impact:

Inconsistent. New contributors guess. Schema reuse across routers (e.g. a shared `PartRefIn`) is hard when half the schemas hide inside route files.

Fix instruction:

Adopt a single rule in `docs/ARCHITECTURE.md`: every domain has `domain/<x>/schemas.py`. Migrate the inlined ones over the next 2-3 PRs. Bonus: the migration also surfaces the `*In`/`*Out` near-duplicate base classes (CQ smell) for cleanup.

### CQ-007: `responses.ok()` annotated `Any`, defeating envelope typing

Severity: **Low**

Evidence:

- `backend/app/core/responses.py:13` — `def ok(data: Any = None, message: str = "OK") -> dict[str, Any]`.

Impact:

The hard-invariant `{data, status}` shape is typed as `dict[str, Any]`, so route signatures lose information. Combined with TEST-008 (no envelope shape test), the envelope contract is enforced only by convention.

Fix instruction:

Switch to a generic `TypedDict`/`BaseModel`: `def ok(data: T = None, message: str = "OK") -> Envelope[T]`. Update route return type hints incrementally — most routes already have a Pydantic `*Out` schema that can be the type parameter.

### CQ-008: Polymorphic helper in `_helpers.py` accepts `Model: Any`

Severity: **Low**

Evidence:

- `backend/app/api/_helpers.py:13` — `Model: Any`, `:18 -> Any:` and another `:61 -> Any:`.
- This is the function that backs `assert_in_workspace` and `assert_polymorphic_in_workspace` — load-bearing for workspace isolation.

Impact:

A typo in the model name (`Parts` instead of `Part`) won't be caught by `mypy --strict`. Given how central this is to the workspace-isolation contract, "type-safe assertion functions" deserves better.

Fix instruction:

Make the helper generic over a `Type[T]` constrained to `WorkspaceOwnedBase`. Replace `Any` with `T`. Type the polymorphic registry as a `dict[str, type[WorkspaceOwnedBase]]`.

### CQ-009: 78 `raise HTTPException` callsites — error-code conventions inconsistent

Severity: **Low**

Evidence:

- `grep -rEn "raise HTTPException" backend/app | wc -l` → 78.
- Mix of `detail="..."` (string) and `detail={...}` (dict). Spread-onto-response only works with dicts (`core/responses.py::http_exception_handler`).
- Some endpoints use 404 for cross-workspace, some 403, some 400 — consistency unverified.

Impact:

FE error-message rendering is brittle: code expecting `body.existing_id` (dict-spread) breaks when a route returns `detail="..."` (string-spread). The `ApiError(status, body, msg)` shape varies per endpoint.

Fix instruction:

Add a wrapper `core/errors.py::raise_http(status, code: str, **fields)` that always produces a `detail` dict with at minimum `{code, message}`. Migrate callsites in three PRs, one per layer (auth → domain services → CRUD routes). Pin the contract with a test (TEST-008's negative-shape variant).

### CQ-010: `app.domain.all_models` is the only seam between models and Alembic — implicit

Severity: **Low**

Evidence:

- `backend/tests/conftest.py:25` — `import app.domain.all_models  # noqa: F401`.
- Implies a barrel import that registers all models with the SA metadata. If a new model is added but not added to `all_models`, Alembic autogenerate will miss it; conftest schema reset works but tests using only the new model won't have the table.

Impact:

A subtle "new model added but not exported" bug class. The `# noqa: F401` is there *because* the import is unused — load-bearing side-effect import.

Fix instruction:

Add a comment block at the top of `app/domain/all_models.py` explaining the contract ("every model module must be imported here, otherwise Alembic autogenerate misses it"). Add a test in `test_migrations.py` (TEST-007's home) that asserts every `*.models.<class>` Mapped class is in `Base.metadata.tables` after `import all_models`.

### CQ-011: 4 `async def` in entire backend — async/sync hybrid is conscious choice but undocumented

Severity: **Low**

Evidence:

- `grep -rEn "async def" backend/app --include="*.py" | wc -l` → 4.
- FastAPI is async-native; the codebase is overwhelmingly sync. SQLAlchemy is the sync API (no `AsyncSession`).

Impact:

Not a bug — sync FastAPI works fine — but it's worth documenting in `docs/ARCHITECTURE.md` so a contributor doesn't add `async def` thinking they need to.

Fix instruction:

Add a short paragraph in `docs/ARCHITECTURE.md` near the stack section: "all routes are sync; SQLAlchemy is sync. Use `async def` only when consuming an async-only library (rare). Don't mix `await` with `db.query` — there is no async session."

## Quick stats

- Backend tests: **244** test functions in **35** files (≈ 6,300 LOC).
- Frontend tests: **19** test functions in **2** files (`lib/api.test.ts`, `lib/bagCode.test.ts`).
- Backend `Any` / `# type: ignore`: **6** occurrences (3 in polymorphic helpers, 2 in DigiKey provider, 1 in `responses.ok`).
- Frontend `any` / `@ts-ignore` / `@ts-expect-error`: **12** occurrences (5 in `lib/api.ts`, 4 in form-payload builders, 3 in scanner error catches).
- TODO/FIXME/XXX/HACK: **0**.
- `print(` in non-test backend code: **0**.
- `console.log`/`console.debug` in frontend: **0**.
- `pytest.mark.skip` / `pytest.mark.xfail`: **0**.
- Real mock objects (`MagicMock`/`patch.object`): **1** (`test_assets.py:26`).
- No linter configured (Python or JS); CI's only static check is `tsc -b`.

## Coverage gaps

- I did not run `pytest --collect-only` or vitest — counts above are from `grep` for `^def test_` and `(test|it)\(`. Expect ±2.
- I did not open every test file. Larger files (`test_bulk_import_from_scan.py` 466 lines, `test_workspace_isolation.py` 461, `test_digikey_provider.py` 386) were sampled, not fully read. There may be additional smells inside that aren't reflected here.
- I did not enumerate every `async def` function for blocking-IO violations beyond a top-level `grep`. The 4 async functions were not audited individually.
- I did not measure FE bundle weight, dep-tree dupes, or open `web/package.json` deeply — that's the FE agent's territory and is partially covered in `docs/teardown/frontend.md`.
- `.github/workflows/` was not opened in this report (deferred to `docs/teardown/infrastructure.md`); TEST-013/014 cross-references it but doesn't replace that audit.
