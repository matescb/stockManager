# ADR-0008: No `verify=False` on httpx clients

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

`httpx.Client(verify=False)` (and the related escape hatches `trust_env=False`, `ssl=False`) silently disables TLS certificate validation. It's the kind of thing developers add temporarily to debug a self-signed cert in staging and then forget. Once it's in `main`, every outbound provider call from prod goes through the same no-validation path — undetectable in functional tests because the transport still works.

Provider lookups (Mouser, DigiKey) happen on the backend with API keys in the request. A MITM into one of those calls leaks the API key and can return falsified part data. The mitigation is to make `verify=False` un-mergeable.

## Decision

CI runs a grep against `backend/app/` for `verify=False`, `trust_env=False`, and `ssl=False` and fails the build if any match exists without the explicit `# noqa: tls-verify` suppression comment (`.github/workflows/ci.yml:188-194`). The annotation is the documented escape hatch — its presence in a diff is a review-stop signal.

## Consequences

- **Good**: Accidental disablement is caught at PR time, not in a post-incident audit. The annotation makes intentional disablement (e.g. test doubles against a self-signed local server) visible and grep-able.
- **Trade-offs**: A future httpx API change that introduces a new way to disable verification (a different parameter name) wouldn't be caught until the grep is updated. The grep is a denylist, not a model of the httpx API.
- **What it forbids**:
  - Don't add `verify=False`, `trust_env=False`, or `ssl=False` to any `httpx.Client(…)` or `httpx.AsyncClient(…)` call under `backend/app/` without `# noqa: tls-verify` on the same line.
  - Don't suppress with `# noqa` (bare) or `# type: ignore`; the grep matches `# noqa: tls-verify` literally.
  - Don't move the suppression off the call site (e.g. into a helper) — the grep is line-local.
  - Don't add the annotation in production code paths just to ship; the annotation is for test doubles only.

## Alternatives considered

- **Lint rule (ruff) instead of grep** — viable. Rejected for now because ruff has no built-in rule for "string match with allow-list comment" and a custom plugin is more code than the four-line grep. If the grep becomes load-bearing on more patterns, promoting it to a custom ruff rule is the next step.
- **Custom httpx client wrapper that forbids the parameter** — rejected because `httpx.Client` is constructed in many places and a wrapper layer is enforcement-by-convention; nothing stops a new caller from importing `httpx` directly.

## References

- Source: `.github/workflows/ci.yml:188-194` (CI grep gate)
- Rule: `CLAUDE.md:125-127`
- Related: `backend/app/domain/parts/providers/mouser.py`, `digikey.py` (the callers protected by this rule)
