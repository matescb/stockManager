# Teardown Summary

Date: 2026-05-01.
Baseline: `docs/claude-review-issues.md` (overall 3/10) — already lists `SEC-001..007`, `BE-001..009`, `FE-001..008`, `INFRA-001..006`.
This pass extends it across six areas, producing **130 net-new findings**. Reports are at `docs/teardown/<area>.md`.

## Reports

| File | Findings | Critical | High | Medium | Low |
|---|---:|---:|---:|---:|---:|
| `frontend.md`       |  22 | 1 |  8 |  9 |  4 |
| `backend.md`        |  26 | 2 |  8 | 12 |  4 |
| `security.md`       |  20 | 2 |  5 | 10 |  3 |
| `infrastructure.md` |  22 | 4 |  8 |  7 |  3 |
| `database.md`       |  15 | 0 |  3 |  7 |  5 |
| `testing.md`        |  25 | 0 |  4 | 11 | 10 |
| **TOTAL**           |**130**|**9**|**36**|**56**|**29**|

## Updated rating table (v1 review + v2 findings)

| Area | v1 rating | v2 net-new findings | Trend |
|---|---:|---:|---|
| Frontend       | 4/10 | 22 | Worse than v1 implied — query-key cache bleed and mutation hygiene are systemic. |
| Backend / API  | 3/10 | 26 | Confirmed. Concurrency / transaction boundary / cross-workspace gaps are pervasive. |
| Security       | 2/10 | 20 | Recent commits (`ff867d4`, `205ade0`, `6990a18`) partially closed three v1 IDs; new findings dominated by missing CSRF, plaintext session tokens, and surface-level header gaps. |
| Infrastructure | 3/10 | 22 | Worse than v1 — no pre-deploy DB backup, no post-deploy health gate, no off-host backup are all Critical. |
| Database       | n/a  | 15 | New axis. Mostly missing FKs and predicate-fit on indexes; no Critical, but DB-001 (no FK on `stock_entries.order_id`) is load-bearing. |
| Testing / CQ   | 5/10 | 25 | Coverage gaps (RBAC matrix, concurrency, FE) are the critical-path weakness; the suite itself is well-built. |

## Top-20 priority queue

Ranked by severity then blast radius. Each row cites the v1 ID it extends (if any) plus the v2 ID where applicable. **Fix in the order shown** — earlier items are prerequisites or unblock later ones.

| # | ID(s) | Title | Why first |
|--:|---|---|---|
|  1 | `SEC-001` / `SEC2-002` / `INFRA2-004` | `WORKSPACE_SECRETS_KEY` not in prod compose; fallback dev key committed; startup does not fail-closed | Production credentials may be encrypted with a public key. Prerequisite to credible at-rest crypto claims. |
|  2 | `INFRA2-001` | No automated DB backup before destructive deploys | Auto-deploy can apply a destructive migration in seconds; no recovery point. Prereq to safe deploys. |
|  3 | `INFRA2-003` | No off-host backup; single-VPS loss = total data loss | Disaster-recovery floor. |
|  4 | `BE-001` | Order-receive TOCTOU over-receive race | Highest-blast-radius correctness bug. Pair with `TEST-003` regression. |
|  5 | `BE-003` / `BE2-001` / `BE2-008` | Stock advisory-lock holes — build consume, add_stock, release_reservations all bypass lock | Same root cause as #4; fix together. |
|  6 | `SEC-002` / `SEC2-005` | Sentry receives full request bodies on every route except `/api/workspaces` | One 5xx on `/api/auth/*` leaks credentials. Trivial to fix. |
|  7 | `SEC-003` / `SEC2-006` / `SEC2-011` | Provider asset MIME validation + SSRF on download + no `nosniff` on serve | Stored-XSS via SVG and SSRF on the same code path. |
|  8 | `SEC2-001` | No CSRF protection on cookie-authenticated state-changing endpoints | Whole-API surface; combined with `SEC-004`/`SEC2-004` is a workspace-takeover vector. |
|  9 | `SEC-004` / `SEC2-004` | `/api/workspaces/{ws}/switch` is unauthenticated, no membership check | Workspace cookie can be flipped cross-site. |
| 10 | `SEC-006` / `SEC2-003` | Session tokens stored in plaintext | DB leak = active hijacking until expiry. |
| 11 | `INFRA2-002` | Deploy has no post-up health gate — CI green can mean prod broken | Catches every other deploy regression early. |
| 12 | `BE2-004` | Any authenticated user can create unlimited workspaces | Resource-exhaustion / billing-class issue. |
| 13 | `FE2-001` | No global 401 handler — list pages silently render empty | First-impression bug; users think data was deleted. |
| 14 | `FE2-004` / `FE2-003` | TanStack query keys missing workspace dimension; cache not flushed on switch | Cross-tenant data flashes on the screen. Combined with `FE2-002` (no-confirm switch) is a workspace-leak vector. |
| 15 | `BE-005` / `TEST-006` | Order entries can reference parts from another workspace; no test | Re-introduce-easy isolation hole. Add the test first, then the fix. |
| 16 | `INFRA-006` / `INFRA2-016` | Backups not encrypted, not verified, no off-host | Closes the backup story (#2 + #3 + this). |
| 17 | `DB-001` | `stock_entries.order_id` / `order_entry_id` / `build_id` and `lots.source_*` are not foreign keys | Orphan rows already possible; adds silent corruption surface. |
| 18 | `INFRA-001` / `INFRA2-002` companion | `/api/health` does not check DB or upload-dir writability | Required for the health gate to mean anything. |
| 19 | `BE-002` / `DB-002` / `TEST-005` | Optional stock coordinates conflict with NOT-NULL trigger; service vs trigger NULL-bucket grouping mismatch | 500s today; data-corruption shape later. Test then fix. |
| 20 | `TEST-001` / `TEST-002` | Workspace-isolation test covers ~half of routers; RBAC matrix only tests `viewer` | Meta-fix: closes the regression net so the rest of the queue stays fixed. |

After the Top-20, work the remaining `Critical`/`High` items in each per-area report, then sweep `Medium`/`Low`.

## Recent commits already addressing v1 findings

Verified by the security teardown:

- **`ff867d4` — encrypt workspace secrets at rest (Sec HIGH-9)**: *partially* resolves `SEC-001`. The encryption infrastructure is plumbed, but prod compose still does not pass `WORKSPACE_SECRETS_KEY` (`INFRA2-004`) and startup does not fail-closed when the key is missing in prod.
- **`205ade0` — reject obviously weak passwords on signup (Sec MED-4)**: resolves the signup path. `TEST-011` flags that change-password and invite-accept are not yet in scope.
- **`6990a18` — gate archive/restore/bulk-delete on admin+ (Arch HIGH-3)**: fully resolves the v1 finding. `TEST-002` flags that the regression net for it is incomplete (only `viewer` is tested; `member` rejection is not).

## Areas with insufficient coverage (per-agent self-reported)

- **Frontend**: i18n strategy not audited (no current strategy exists); public-catalog page (different routing shape) not audited.
- **Backend**: Mouser provider, Sentry tunnel envelope shape, BOM mapping-collision case inspected only briefly. `pytest --collect-only` and `mypy` not run.
- **Security**: Frontend DOM-XSS sinks not audited (deferred to FE agent — `FE2-020` partially covers leaked stack traces). Live VPS Apache config not in repo. `pip-audit` / `npm audit` not run (mutating-network).
- **Infrastructure**: Live `.env.prod`, certbot-generated TLS config, GitHub branch protections, VPS provider snapshot policy, runtime image sizes — none accessible from the repo.
- **Database**: Live Postgres not inspected; physical index sizes / planner choices unknown. Trigger semantics reviewed statically, not exercised against a real DB.
- **Testing**: Larger test files were sampled, not fully read. `.github/workflows/` not opened in this report (deferred — `TEST-014` flags the gap).

## Methodology

- 6 parallel agents (one per area). 5 produced reports directly. The Testing/CQ report was produced inline because the parallel agent hit a rate limit before starting.
- Each report uses a unique ID prefix (`FE2-`, `BE2-`, `SEC2-`, `INFRA2-`, `DB-`, `TEST-`/`CQ-`) so they compose with the v1 review (`FE-`, `BE-`, `SEC-`, `INFRA-`) without collision.
- Each report cites and extends the v1 IDs it touches; net-new findings are not duplicates.
- No source files were modified. Output is `docs/teardown/*.md` only.
