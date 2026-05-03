# Runbook: incident response

Audience: engineer / on-call

How to run an incident from declaration through retro. Severity
definitions, comms cadence, and the post-mortem template. Pair this with
[`on-call-quickstart.md`](on-call-quickstart.md) (which gets you from
page → triage) and the scenario-specific runbooks.

- **When to run**: any time you escalate beyond "I can fix this in 5
  min". When in doubt, declare — small incidents are cheap; missed
  declarations are expensive.
- **Severity**: this runbook covers SEV-1 and SEV-2. SEV-3 issues use
  the lighter-weight "file a GH issue, link Sentry" pattern from
  `sentry-triage.md`.
- **Time-to-recovery target**: per severity (see matrix below).
- **Owner**: `<TODO(verify): on-call rotation>` is the **incident
  commander** by default.

## Severity matrix (quick recall)

Full definitions in `docs/runbooks/README.md`.

| Severity | TTR | Comms cadence |
|---|---|---|
| SEV-1 | < 15 min to acknowledge, < 4 h to mitigate | Update every 15 min until mitigated |
| SEV-2 | < 1 h to acknowledge, < 24 h to mitigate | Update every hour during work-day; daily otherwise |
| SEV-3 | < 1 day to acknowledge | No real-time updates; ticket-driven |

## Pre-flight

- You can write to the incident channel:
  `<TODO(verify): channel name — Slack / Discord / email list>`.
- You have the on-call quickstart open in another tab.
- You know the escalation contacts (`on-call-quickstart.md` →
  Escalation).

## Roles (for SEV-1; collapse for SEV-2)

- **Incident commander (IC)**: owns the incident. Decides severity,
  drives the timeline, owns external comms. The on-call is IC by
  default.
- **Operator**: the person actually typing commands at the VPS / in
  the repo. Often the IC for small teams; explicit handoff for SEV-1
  if more than one engineer is responding.
- **Scribe**: maintains the running timeline (timestamps + actions +
  outcomes) in the incident channel thread. The IC can scribe if
  there's no one else.

For our team size (2–3), one person typically holds all three roles.
Name them anyway in the channel post — it forces clarity.

## Steps

### 1. Declare

Post the declaration in the incident channel. Template:

```
INCIDENT DECLARED — SEV-<1|2>
Title: <short, descriptive — e.g. "DB unreachable, 5xx rate 100%">
IC: <@you>
Started: <UTC timestamp>
Symptom: <what the user sees>
Suspected cause: <one line, or "unknown">
Runbook: <link to scenario-specific runbook, if known>
```

### 2. Triage

Follow `on-call-quickstart.md` if you haven't already. Map the symptom
to a scenario-specific runbook. If nothing fits, you're investigating
from scratch — keep the channel updated.

### 3. Mitigate before fixing

For SEV-1: get the bleeding stopped, even if the fix is ugly. Examples:

- App regression → roll back to last-known-good SHA (`prod-rollback.md`
  section B).
- Migration failure → put up the maintenance page and restore from
  predeploy snapshot (`migration-recovery.md`).
- Provider outage causing 5xx (not just degradation) → flip the
  feature off.

A mitigation does not require a permanent fix. Document the mitigation
clearly so the follow-up doesn't get forgotten.

### 4. Communicate on cadence

Every cadence interval (see severity matrix), post:

```
UPDATE — T+<minutes>
Status: <investigating | mitigated | resolved>
Done since last update: <bullet list>
Next: <what you're trying now>
ETA: <if known, else "unknown">
```

Even "no progress" is useful — it tells everyone the incident is still
live.

### 5. Resolve

Resolution = the user-visible symptom is gone **and** you've
confirmed it independently (curl, click-through, Sentry quiet).

Post:

```
RESOLVED — T+<minutes>
Mitigation: <what stopped the bleeding>
Permanent fix: <PR link, or "follow-up needed">
Data loss: <yes/no — if yes, scope>
Affected users: <best estimate>
Post-mortem: scheduled for <date>
```

### 6. Capture the timeline

While the incident is fresh, paste the channel thread into a doc.
Filename:
`<TODO(verify): post-mortem location — docs/post-mortems/<date>-<slug>.md or external doc store>`

The timeline is the load-bearing artifact for the retro. Bad timeline
→ bad retro → repeated incident.

## Post-mortem template

Run within 1 week of resolution. Blameless. The point is process
improvement, not finger-pointing.

```markdown
# Post-mortem: <title>

- **Date**: <incident date>
- **Severity**: SEV-<1|2>
- **Duration**: <ack → resolve, in human time>
- **Author**: <who is writing this>
- **Reviewers**: <names>

## Summary

<2–3 sentences. What broke, what was the impact, how was it fixed.>

## Impact

- **User-visible**: <what users saw>
- **Affected workspaces**: <count or "unknown">
- **Data loss**: <yes/no, scope>
- **Window**: <UTC start → UTC end>

## Timeline

All times UTC. Lift directly from the incident channel.

| Time | Event |
|---|---|
| 14:23 | UptimeRobot alert: /api/health 503 |
| 14:24 | <@oncall> acknowledged, declared SEV-1 |
| 14:28 | … |

## Root cause

<The actual technical cause. Cite code: `path:line`. If multiple
contributing factors, list each.>

## What went well

- <e.g. "alert fired within 90s of the actual outage">

## What went badly

- <e.g. "took 12 min to find the bad SHA because release tag was empty">

## Action items

| # | Item | Owner | Issue |
|---|---|---|---|
| 1 | <e.g. "add CI check for empty SENTRY_RELEASE"> | <@handle> | #N |
| 2 | … | … | … |

Each action item must have an owner and a GitHub issue. "We should
maybe…" without an issue is not an action item.

## Lessons

<1–3 paragraphs. What we now know about the system that we didn't
before. This is the part that survives — link it from `CLAUDE.md`
"Things that have bitten us" if it changes how we write code.>
```

## Verification

- The triggering symptom is independently confirmed gone.
- The incident channel has a `RESOLVED` post.
- The timeline is captured to a doc.
- A post-mortem date is on the calendar (for SEV-1 always; SEV-2 if
  the cause is non-obvious or recurring).

## Rollback (of an incident response)

- A premature `RESOLVED` post: re-declare. Severity may have changed.
- A botched mitigation that made things worse: roll back the
  mitigation (e.g. revert the revert — see `prod-rollback.md`).
- A wrongly-assigned severity: post a correction (`SEVERITY UPDATED:
  SEV-2 → SEV-1, reason: <…>`). Don't quietly change the original
  declaration.

## Post-mortem prompts (for the incident-response process itself)

- Was the severity called correctly at declaration time?
- Did the comms cadence hold?
- Did anyone outside the response need information they didn't get?
- Did the runbooks match reality? (If not: this runbook itself is an
  action item.)
- Is the post-mortem scheduled within a week?
