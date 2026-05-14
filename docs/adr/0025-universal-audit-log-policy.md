# ADR-0025: Universal `audit_log` coverage for workspace mutations

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-14
- **Supersedes**: —
- **Superseded by**: —

## Context

`audit_log` is the forensic trail for user-facing changes inside a workspace:
who changed an object, which workspace it belonged to, and which object IDs
were affected. Before AUD-004, only a few routers wrote audit rows even though
`CLAUDE.md` already said every mutation should be auditable. That left common
operator actions such as lot metadata edits and storage-flag changes without a
queryable trail.

The competing policy options were:

- **Universal**: every successful workspace-scoped mutation writes one
  `audit_log` row in the same transaction.
- **Selective**: only specific high-risk routers write rows, and docs plus CI
  enumerate the covered set.

## Decision

Use the universal policy. Every successful workspace-scoped API mutation must
write an `audit_log` row through `backend/app/domain/audit/service.py::log` in
the same database session as the business change. If the business transaction
rolls back, the audit row rolls back with it.

The row must identify the workspace, acting user when known, action name,
target type, and affected object IDs when the route has stable IDs. Comments
may contain low-sensitivity summaries such as changed field names, but must not
contain plaintext credentials, provider API keys, verification tokens, raw bag
codes, or other secrets.

## Consequences

- **Good**: Operators and incident responders can query a consistent
  workspace-scoped trail for successful user-facing mutations.
- **Good**: The policy is reviewable by convention and regression tests can pin
  high-risk mutators as they are brought under coverage.
- **Trade-offs**: Audit volume grows with normal write traffic. The table is
  indexed for workspace/time queries and target-ID lookup, but retention and
  export policy may need a separate ADR if volume becomes operationally
  significant.
- **What it forbids**:
  - Do not add a successful workspace-scoped API mutation without an
    `audit_log` row unless a later ADR narrows this policy.
  - Do not write audit rows in a separate transaction from the business change.
  - Do not put secrets or raw credential material in `audit_log.comment`.
  - Do not treat `audit_log` as the source of truth for current business state;
    it is evidence of changes, not a replacement for domain tables.

## Alternatives considered

- **Selective coverage by router** — rejected because the product already
  exposes many equally forensic-relevant mutations across lots, storage,
  projects, orders, tags, and attachments. A selective list would become a
  policy exception ledger and make missing rows look intentional.
- **Database triggers** — rejected because the API has the request context
  needed for user ID, route-level action name, and safe comment summaries.
  Trigger-only rows would either omit that context or require fragile session
  variables.
- **Use the stock ledger as the audit log for stock routes** — rejected as a
  general policy because the append-only stock ledger answers inventory state
  history, while `audit_log` answers user-facing mutation history across all
  domains. They can reference the same action, but neither replaces the other.

## References

- Source: `backend/app/domain/audit/service.py`
- Source: `backend/app/api/routes/lots.py`
- Source: `backend/app/api/routes/storage.py`
- Test: `backend/tests/test_audit_log_coverage.py`
- Rule: `CLAUDE.md`
