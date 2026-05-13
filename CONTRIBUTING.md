# Contributing

## Filing an issue

- Search existing issues first to avoid duplicates.
- Use a clear title. If the fix will need multiple PRs, say so upfront (e.g. "Step 1 of 2: …") and describe all planned steps in the body.
- Attach labels (`area:`, `complexity:`, `priority:`) if you have triage access; otherwise leave it for maintainers.

## Opening a pull request

1. Branch from `main`. Use the naming pattern `fix/issue-NNN-short-description` or `feat/issue-NNN-short-description`.
2. Fill in the PR template (`.github/PULL_REQUEST_TEMPLATE.md`). The test plan and multi-step checkbox are mandatory.
3. Run `pytest` (backend) and `npm run build` (frontend) before marking the PR ready for review.
4. Add screenshots for any visible UI changes.
5. Link the issue: use `Closes #N` if this PR fully resolves it, or `Refs #N` if further work remains.
6. Keep PRs narrowly scoped. If an umbrella/backlog issue requires bundling otherwise independent changes, state why in the PR body and list the merge-order or revert-risk notes reviewers need.

## Multi-step issues rule

When a PR title or description contains "step 1", "PR 1 of", or "part 1" (case-insensitive), you **must** do one of the following before the PR is merged:

- **Option A** — File a follow-up issue for the remaining steps and include `Refs #N` (the follow-up issue) in the PR description. The original issue stays open until all steps land.
- **Option B** — Use `Refs #N` (the original issue) instead of `Closes #N` in the PR body so the issue is not auto-closed.

Do **not** use `Closes #N` on a step-1 PR unless all remaining steps are also included in that PR. Auto-closing an issue after only the first step lands leaves the work permanently orphaned with no visible signal.

## Closes vs Refs

| Keyword | Effect on the issue |
|---------|---------------------|
| `Closes #N` | Issue is closed automatically when the PR merges |
| `Fixes #N` | Same as Closes |
| `Refs #N` | Issue is linked but stays open |

Use `Refs #N` whenever the issue requires more than one PR to fully resolve.
