# Runbook: provider (DigiKey / Mouser) outage

Audience: engineer / on-call

DigiKey or Mouser API is unreachable, returning 5xx, or rate-limiting
the workspace's API key. The app stays up; provider-dependent flows
degrade. This is **not** a SEV-1 because manual data entry is always
available as a workaround.

- **When to run**:
  - Sentry shows a spike in `httpx.HTTPStatusError` /
    `httpx.ConnectError` from
    `backend/app/domain/parts/providers/digikey.py` or `mouser.py`.
  - Users report that `POST /api/parts/lookup-mpn` returns errors or
    empty results for known-good MPNs.
  - Provider's public status page reports an incident.
  - BOM scan-import (`POST /api/parts/bulk-import-from-scan`) fails to
    populate provider metadata for some/all rows.
- **Severity**: SEV-2 if all provider lookups are failing (degraded
  workflow); SEV-3 if a single workspace is rate-limited.
- **Time-to-recovery target**: provider-dependent (we wait for them);
  workaround within 15 min.
- **Owner**: `<TODO(verify): on-call rotation>`

## What's affected vs not

| Feature | Affected? |
|---|---|
| Logging in, viewing existing parts, stock movements | No |
| Creating a part by hand (typing MPN + manufacturer) | No |
| Creating a part via "lookup MPN" autofill | **Yes** — autofill empty / errored |
| BOM CSV import (no provider lookup needed) | No |
| Scan-import flow that resolves MPN through provider | **Yes** — falls back to "unmatched" rows |
| Provider images / datasheets already downloaded | No (content-addressed at `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}` — see CLAUDE.md "Content-addressed assets") |
| Per-workspace provider credential management | No |

The integration code lives at:

- `backend/app/domain/parts/providers/base.py` — abstract base.
- `backend/app/domain/parts/providers/digikey.py` — DigiKey adapter.
- `backend/app/domain/parts/providers/mouser.py` — Mouser adapter.
- `backend/app/domain/parts/services/provider.py` — service layer that
  orchestrates per-workspace provider selection. Catalog vs spec key
  split is documented in CLAUDE.md "Provider catalog vs spec keys".

Per-workspace API keys are encrypted at rest with `WORKSPACE_SECRETS_KEY`
(`backend/app/core/secrets.py`). A failure to decrypt would surface
differently — see `secret-rotation.md` rather than this runbook.

## Pre-flight

- SSH access to the VPS as `deploy` (for log inspection).
- Sentry access (for error categorisation).
- The workspace ID(s) of users reporting issues, if it's a single-tenant
  problem.

## Steps

### 1. Identify which provider, which scope

1. Open Sentry. Filter to the last hour, group by exception class.
2. Note which provider module is throwing — `providers/digikey.py` or
   `providers/mouser.py`.
3. Note whether it's one workspace (single `X-Workspace-Id` header
   value across events) or all of them.
4. Check the provider's status page:
   - DigiKey: `<TODO(verify): DigiKey API status page URL>`
   - Mouser: `<TODO(verify): Mouser API status page URL>`

### 2. Categorise

| HTTP status from provider | Cause | Action |
|---|---|---|
| Timeout / connect refused | Provider down or network issue | Wait + workaround (step 4) |
| 5xx (500/502/503) | Provider degraded | Wait + workaround |
| 401 / 403 | Workspace's API key is invalid or revoked | Workspace owner re-issues key (step 5) |
| 429 | Rate-limit hit | Workaround for now; reduce call frequency (step 6) |
| 200 with empty body / unexpected schema | Provider changed schema | Bug in our adapter; file an issue and patch |

### 3. Confirm from the VPS

Tail the backend logs for provider calls:

```bash
ssh deploy@<vps-host>
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    logs --tail=300 backend | grep -iE 'digikey|mouser|lookup_mpn'
```

If you see `# noqa: tls-verify` in the output, that's intentional (only
test doubles use it — CLAUDE.md "No `verify=False` on httpx clients").

### 4. Workaround for users — manual data entry

While the provider is unreachable, users can still create parts
manually. Communicate to affected workspaces:

- **Create part by hand**: use the "Create part" form, type the MPN +
  manufacturer + at least one storage location.
- **Skip lookup-MPN autofill**: the form works without it; provider
  metadata can be added later when the provider is back (re-run lookup
  on the existing part).
- **For scan-import**: rows that fail to match show as "unmatched" in
  the scan-import queue (`web/src/routes/parts/ScanImport/`). Users
  can map them manually now and the queue persists across the outage
  window.

There is **no need** to disable the lookup-MPN endpoint server-side —
it returns an error envelope (`{ data: null, status: "error" }` or a
specific HTTPException), and the frontend already handles that path
(degraded autofill state).

### 5. If a single workspace is affected (401/403)

The workspace's stored credentials are wrong or expired. Workspace
admin re-issues the key:

1. The admin opens the workspace settings UI → Providers tab.
2. They paste a fresh API key from the provider's developer dashboard.
3. The key is encrypted by `backend/app/core/secrets.py` and stored on
   the `workspaces` row.
4. They retry the lookup that was failing.

If the admin can't reach the provider's dashboard either, escalate
back to the provider — that's not our problem to fix.

### 6. If we're being rate-limited globally

This means our adapter is calling more than the provider allows. Short
term: there's no global throttle in the adapter today
(`<TODO(verify): confirm no rate-limit / circuit-breaker is implemented
in providers/digikey.py or mouser.py — if it is, document where>`).
Mitigations:

- Ask high-volume workspaces to pause bulk imports.
- File a follow-up to add adapter-level rate limiting / backoff.

Don't add `verify=False` or otherwise weaken TLS to "see if it's a
cert issue" — CI greps for that pattern (CLAUDE.md "No `verify=False`
on httpx clients", and the gate at `.github/workflows/ci.yml:222-228`).

### 7. If the provider changed schema

If the provider returned 200 but the body doesn't parse, the adapter
needs a code change:

1. Capture a sample response (sanitised — no API key values) by
   adding a temporary log line in the adapter, redeploy, capture, and
   revert. Or reproduce locally with the workspace's own key.
2. Update the schema parser in `digikey.py` / `mouser.py`.
3. If the new schema affects the catalog-vs-spec key split, update
   **both** `web/src/lib/providerCatalog.ts` and
   `backend/app/domain/parts/services/provider.py` (CLAUDE.md
   "Provider catalog vs spec keys").
4. PR + deploy.

## TrustedParts outage

TrustedParts sourcing is separate from the Mouser/DigiKey catalog providers. It backs `POST /api/sourcing/search`, the workspace sourcing connection test, and sourcing UI surfaces; catalog lookup, manual part creation, stock movements, and existing parts remain available. The low-stock report degrades gracefully: `GET /api/reports/low-stock?include_sourcing=true` still returns `200 OK` rows with a `sourcing_status` flag when TrustedParts is unavailable or budget-blocked.

Integration entry points:

- `backend/app/domain/sourcing/client.py` — TrustedParts API v2 client.
- `backend/app/domain/sourcing/factory.py:12` — decrypts the current workspace's sourcing credentials and builds the client.
- `backend/app/domain/sourcing/service.py:39` — cache, budget, and response attribution facade.
- `backend/app/api/routes/sourcing.py:87` — member search route and error mapping.

### TrustedParts triage

| Symptom | Likely cause | Action |
|---|---|---|
| `409 sourcing not configured` | Workspace has no TrustedParts API key | Workspace admin configures sourcing credentials in workspace settings. |
| `502 TrustedParts rejected sourcing credentials` | TrustedParts API key is invalid/revoked | Workspace admin re-issues credentials in TrustedParts and saves them again. |
| `502 TrustedParts rate limit reached` | TrustedParts throttled the key | Ask the workspace to pause high-volume sourcing; retry after the provider window clears. |
| `502 TrustedParts request timed out` or upstream failure | TrustedParts or network outage | Use manual entry/fallback below; retry when provider status recovers. |
| `503 sourcing budget exhausted` | Local hard parts-count budget blocked live calls | Wait for the rolling window to expire; cached hits still avoid additional budget consumption. |

### Rate-limit recovery

1. Check whether the response is our local `429` route limit, local `503` budget block, or an upstream `502` TrustedParts rate-limit mapping.
2. For local `429`, wait for the 60/minute workspace route window.
3. For local `503`, wait for the rolling budget window. The budget is process-local and assumes one uvicorn worker (`backend/app/domain/sourcing/budget.py:104`).
4. For upstream TrustedParts throttling, reduce sourcing calls from the affected workspace and retry later. Do not bypass the budget, add `SourceIp`, or remove attribution to work around throttling.

### Credential issues

1. Confirm only one workspace is affected.
2. Ask a workspace admin to update the TrustedParts API key in workspace settings.
3. Use `POST /api/workspaces/current/sourcing/test` to confirm the credentials probe returns `{ "ok": true }`.
4. If the test route succeeds but search still fails, inspect `backend/app/domain/sourcing/service.py:39` and `backend/app/api/routes/sourcing.py:87` for regression in defaults, cache, or error mapping.

### Fallback

Users can still create and edit parts manually, set storage/stock, and use catalog providers. TrustedParts sourcing results are convenience procurement data; they must not be replaced by scraped data or mixed public distributor data during an outage without a new approval path. Existing cached results can be served until their short TTL expires, but the cache is not a permanent price-history store.

## Verification

- A test lookup for a known-good MPN against the affected provider
  succeeds and populates expected fields.
- A TrustedParts search for a known-good MPN returns `powered_by="TrustedParts"` and attribution links, or the workspace sourcing test returns `{ "ok": true }` after credential repair.
- Sentry: `httpx.*` from the provider modules drops back to baseline
  rate.
- A workspace that reported issues confirms autofill is back.
- For schema-change fixes: a part created via lookup has the correct
  catalog metadata in PartSpecs / PartSourcing tabs.

## Rollback

- Provider down: there's nothing to roll back. The workaround is the
  state we're in until the provider is back.
- Adapter code change made it worse: standard `prod-rollback.md`
  procedure (revert + redeploy).
- Workspace admin pasted the wrong API key: same admin pastes the
  right one. The plaintext is never logged
  (`backend/app/main.py:23-44` Sentry scrubber explicitly covers
  `POST /api/parts/lookup-mpn` and `PATCH /api/workspaces/current`).

## Post-mortem prompts

- Did the outage trigger an alert, or only user reports?
- Is the failure mode the same for both providers? (If so: maybe
  it's our network, not theirs.)
- Were the per-workspace impact and the global impact distinguishable
  quickly?
- Should we add adapter-level circuit-breaking / rate-limiting? File
  a separate issue — don't bolt it on during the incident.
- Did the catalog-vs-spec key split survive intact? (Schema changes
  often quietly move a field across the boundary.)
