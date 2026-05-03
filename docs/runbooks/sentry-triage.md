# Runbook: Sentry issue triage

Audience: engineer / on-call

Workflow for taking a Sentry issue from notification → understood →
assigned to a release. Covers the release-tag pipeline, sourcemap
workflow, the `before_send` scrubber's behaviour (so you know what
context **isn't** there), and the `/api/sentry-tunnel` route caveat.

- **When to run**:
  - Sentry alert fires (new issue, regression, frequency spike).
  - Sentry release-comparison shows new issues attributed to the latest
    deploy.
  - User reports a 500 with no obvious cause in app logs.
- **Severity**: SEV-2 for active error rate spike; SEV-3 for one-off
  issues / noise spikes.
- **Time-to-recovery target**: 30 min to identify owning code; longer
  for actual fix.
- **Owner**: `<TODO(verify): on-call rotation>`

## Pre-flight

- Sentry account with access to the backend project and the React
  project. URLs:
  - Backend project: `<TODO(verify): URL from SENTRY_PROJECT in .env.prod>`
  - Frontend project: `<TODO(verify): URL from VITE_SENTRY_DSN project page>`
- Local checkout of stockManager so you can grep for the failing module.
- Read access to the GitHub Actions logs (to confirm sourcemap upload
  ran).

## What context is in the event (and what isn't)

Sentry init lives in `backend/app/main.py:67-103`. The notable
configuration is in `_scrub_event` at `backend/app/main.py:45-63`:

- **Headers**: `cookie`, `authorization`, `x-workspace-id` are stripped
  on every event, every method.
- **Request body** (`request.data`): default-deny on any non-GET
  method. If a POST/PATCH/PUT/DELETE event has a body, you'll see
  `"body_redacted": true` instead of the body itself. This is
  load-bearing — see the comment at `backend/app/main.py:23-44` for
  the full list of routes that handle secrets in bodies.
- **Frame-local variables**: disabled
  (`include_local_variables=False`, `backend/app/main.py:88`). Stack
  traces show the line, not the values of locals at that line.
- **PII**: `send_default_pii=True` (`backend/app/main.py:85`) gives
  you user IP and headers; combined with the scrubber above, plaintext
  credentials never reach Sentry.

If you need a value that the scrubber removed, you have to reproduce
locally — Sentry alone won't tell you the request body that triggered
the failure.

## Steps

### 1. Read the event

1. Open the issue in Sentry.
2. Note the **Release** tag — short SHA, exported as `SENTRY_RELEASE`
   by the deploy job (`.github/workflows/ci.yml:647-648`). Cross-check against
   `git log --oneline` on `main`. If the tag is empty or doesn't
   match a SHA you recognise, the deploy didn't export
   `SENTRY_RELEASE` correctly — flag that as a separate issue.
3. Read the stack trace top-down. Frames are unminified for backend
   (Python source); frontend frames are unminified **only if**
   sourcemaps uploaded successfully for that release.

### 2. If frontend frames are minified

The sourcemap upload runs in CI on push-to-main only
(short-SHA derivation `.github/workflows/ci.yml:274`, upload step
`.github/workflows/ci.yml:347`). If you see things like
`a.b.c at https://parts.matescb.cz/assets/index-abcd1234.js:1:12345`:

1. Open the GitHub Actions run for the deploy that introduced the
   release SHA.
2. Find the **Upload sourcemaps to Sentry** step in the `web-build`
   job.
3. If it ran successfully but Sentry still can't resolve frames:
   - Confirm the release name in the Sentry "Releases" page matches
     the 12-char short SHA. CI derives this once in the "Set release
     name" step of the `web-build` job and reuses it for both
     `VITE_APP_VERSION` (baked into the runtime bundle) and the
     `npx @sentry/cli sourcemaps upload --release <sha>` call —
     keeping bundle and sourcemap pinned to the same release tag
     (PR #293, issue #283).
   - Confirm `web/vite.config.ts` is still emitting sourcemaps when
     `SENTRY_AUTH_TOKEN` is present (CLAUDE.md "Sourcemaps are only
     emitted in CI").
4. If it didn't run: `SENTRY_AUTH_TOKEN` is missing or expired — see
   `secret-rotation.md` section 2.4.

### 3. Find the owning code

1. From the stack trace, identify the file and line.
2. Confirm the line exists at the release SHA:
   ```bash
   git show <release-sha>:<path/to/file>.py | sed -n '<line-1>,<line+5>p'
   ```
3. `git log --follow <path/to/file>` to find recent changes to that
   region.

### 4. Compare against the previous release

Sentry's **Release comparison** view shows new issues introduced by a
release. Use it to scope: is this a new bug, a regression, or
pre-existing background noise?

### 5. Decide the response

| Finding | Response |
|---|---|
| Bug introduced by the latest deploy, easy fix | Forward-fix PR |
| Bug introduced by the latest deploy, large blast radius | Roll back — see `prod-rollback.md` |
| Pre-existing bug, low-frequency | File a GH issue, link the Sentry issue, leave Sentry issue open |
| Noise (e.g. user disconnected mid-request) | Mark Resolved → Until Next Release; or add an `ignore_errors` rule |
| Spam from a single bad client | Sentry rate-limit rule or `before_send` filter (PR to `_scrub_event`) |

### 6. Tag and assign

1. Set **Assignee** to the owning team / engineer.
2. Set **Linked issue** to the GitHub issue if you filed one.
3. If you rolled back: mark **Resolved in <next-release-sha>** so
   regressions on the rolled-back code re-open it.

## The `/api/sentry-tunnel` caveat

The frontend SDK does **not** talk to Sentry directly — it tunnels
through `/api/sentry-tunnel` (`backend/app/api/routes/sentry_tunnel.py`,
configured at `web/src/instrument.ts:34`). Implications when triaging:

- If frontend Sentry events stop arriving, the cause may be the tunnel
  route, not the SDK. Test it directly:
  ```bash
  curl -X POST https://parts.matescb.cz/api/sentry-tunnel \
    -H 'Content-Type: application/x-sentry-envelope' \
    --data-binary @- <<< 'fake'
  ```
  Expect a 4xx with one of `SENTRY_TUNNEL_*` error codes from
  `backend/app/core/errors.py:122-126` — confirms the route is alive.
- The tunnel allow-lists envelopes against `VITE_SENTRY_DSN` (see
  `backend/app/core/config.py:96` and `errors.py:126
  SENTRY_TUNNEL_DSN_MISMATCH`). If you rotate the frontend DSN, both
  the SPA bundle (rebuild) **and** the backend env must update —
  otherwise valid events get rejected with `dsn_mismatch`.
- The tunnel has a body-size cap (default 200 KiB,
  `backend/app/core/config.py:49`). A flood of `SENTRY_TUNNEL_TOO_LARGE`
  errors in your own backend logs suggests an SDK config emitting
  oversized envelopes (e.g. session replay enabled by accident).
- The tunnel route is exempt from CSRF / auth checks
  (`backend/app/main.py:259-265` comment). Don't add an auth dependency
  to it — the SDK is unauthenticated by design.

## Verification

- The Sentry issue you triaged is in a terminal state (Resolved,
  Ignored, or assigned to a forward-fix PR).
- If you rolled back: no new events with the bad SHA's release tag
  arrive after the rollback.
- If you forward-fixed: the next release shows the issue resolved /
  not regressed.

## Rollback

Sentry actions are reversible:

- Re-open a resolved issue from the issue page.
- Remove an `ignore_errors` rule from the Sentry project settings.
- Revoke a `before_send` filter PR if it suppressed real signal.

The only non-reversible action here is shipping a code rollback for
the wrong cause — see `prod-rollback.md` "Rollback (of the rollback)".

## Post-mortem prompts

- Was the alert actionable, or did you have to dig for 10 min before
  it told you anything?
- Did the release tag attribute the issue to the correct deploy?
- Was a sourcemap available? If not, why?
- Did the scrubber strip context you actually needed? If so, document
  why the missing field was load-bearing — don't loosen the scrubber
  reflexively (SEC2-005).
- Should this issue have been caught by a test? Add one before
  closing.
