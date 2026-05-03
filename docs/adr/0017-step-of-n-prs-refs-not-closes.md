# ADR-0017: Step-of-N PRs use `Refs #N`, not `Closes #N`

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

GitHub auto-closes an issue when a PR linked with `Closes #N` (or `Fixes #N`, `Resolves #N`) is merged. That's the right behaviour when one PR fully resolves the issue. It's the wrong behaviour when an issue is intentionally split into "Step 1 of 3", "Step 2 of 3", "Step 3 of 3" PRs: merging step 1 closes the issue, and steps 2 and 3 lose their tracking link. The remaining work becomes orphan tasks with no visible signal in the issues list.

This has happened in this repo. The recovery is manual (re-open the issue, re-link the remaining PRs), and the cost is permanent — the issue's auto-close timestamp predates the actual completion, breaking any "time to close" metric.

## Decision

PRs whose title or description contains "step 1", "PR 1 of", or "part 1" (case-insensitive, and analogously for any non-final step) **must not** use `Closes #N` for the parent issue. They use one of:

- **`Refs #N`** — links the issue without auto-closing it. The issue stays open until a follow-up PR closes it explicitly.
- **A follow-up issue** for the remaining work, with `Refs #<follow-up>` in the partial PR's body. The original issue stays open.

The full rule and the linking-keyword table are in `CONTRIBUTING.md:6-34`. The PR template (`.github/PULL_REQUEST_TEMPLATE.md`) carries a multi-step checkbox for this.

## Consequences

- **Good**: Multi-step work stays trackable. The issue's close timestamp matches the actual completion. Reviewers and contributors can find the remaining steps from the issue page.
- **Trade-offs**: Discipline-based — there is no automated check that the body matches the title's "Step 1 of N" claim. The PR template's checkbox is the prompt; review is the enforcement.
- **What it forbids**:
  - Don't use `Closes #N` (or `Fixes #N` / `Resolves #N`) on a PR whose title says "Step 1 of N", "PR 1 of N", or "Part 1 of N".
  - Don't merge a "Step 1 of N" PR without one of the two options above (`Refs #N` on the parent, or a follow-up issue with `Refs #<follow-up>`).
  - Don't close the parent issue manually after merging a partial PR; close it when the final step lands.

## Alternatives considered

- **CI lint** that scans the PR body for `Closes #` when the title contains "step 1" — viable as a future enhancement. Rejected for now because the rule is rare-trigger and the cost of a missed enforcement (re-open the issue) is small. If the rule gets violated more than once a quarter, automate it.
- **Disable GitHub's auto-close behaviour repository-wide** — rejected because `Closes #N` is the right tool for single-PR fixes (which are most fixes), and disabling it would shift work to manual close-on-merge for every PR.

## References

- Source: `CONTRIBUTING.md:6` (multi-step title rule), `:14-24` (multi-step issue rule), `:28-34` (linking-keyword table)
- Source: `.github/PULL_REQUEST_TEMPLATE.md` (multi-step checkbox)
- Rule: `CLAUDE.md:181-184`
