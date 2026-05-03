# ADR-0004: MPN uniqueness per workspace

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

A part has an MPN (Manufacturer Part Number) when it's a real catalog item. Sub-assemblies, BOM-only line items, and hand-fabricated parts have no MPN. Within a workspace, an MPN is a strong identity claim — two parts with the same MPN are the same physical part and should be merged, not duplicated. Across workspaces, MPNs collide naturally (every workspace has its own copy of "STM32F103C8T6").

A naive `UNIQUE(workspace_id, mpn)` rejects multiple null MPNs, which breaks every workspace that has more than one MPN-less part. A composite non-unique index (the original `ix_parts_ws_mpn` on `(workspace_id, manufacturer, mpn)`) is too permissive — it allows `(WS1, "ST", "abc")` and `(WS1, "STMicroelectronics", "abc")` to coexist as duplicates of the same canonical part.

Archival also matters: once a part is `archived_at`, the MPN slot frees up so a new part can take it.

## Decision

Migration `0011_parts_mpn_unique.py` drops `ix_parts_ws_mpn` and replaces it with a partial unique index `uq_parts_ws_mpn` on `(workspace_id, mpn)` `WHERE mpn IS NOT NULL AND archived_at IS NULL`.

Create-part returns HTTP 409 on collision, with a structured body `{ existing_id, existing_name }` so the client can offer "open the existing part" instead of failing with a bare validation error.

## Consequences

- **Good**: The DB enforces the invariant; no service-layer race condition can produce two live parts with the same MPN in one workspace. The 409 carries the existing part's id, so the UI deep-links rather than just blocking.
- **Trade-offs**: The index is partial, which means `EXPLAIN` plans don't show it for queries that don't include the predicate. Lookups by MPN must be written as `WHERE mpn = ? AND archived_at IS NULL` to use it.
- **What it forbids**:
  - Don't promote the index to an unconditional `UNIQUE(workspace_id, mpn)` — that would reject multiple null-MPN parts and break sub-assembly workflows.
  - Don't return a bare 400 from create-part on collision. The 409 with `{ existing_id, existing_name }` is the contract `extractMpnConflict` (`web/src/lib/api.ts`) consumes.
  - Don't add `manufacturer` back into the unique key — manufacturer is free-text and inconsistent ("ST" vs "STMicroelectronics"), so it can't be the dedup discriminant.

## Alternatives considered

- **Application-layer uniqueness check before insert** — rejected because two concurrent inserts would both pass the check and both succeed, creating the duplicate the index is supposed to prevent. The DB constraint is the only race-free location.
- **Cross-workspace uniqueness on MPN** — rejected because workspaces are tenant-isolated; one workspace's part list shouldn't constrain another's. Different operators may even spell or normalise the MPN differently.

## References

- Source: `backend/alembic/versions/0011_parts_mpn_unique.py`
- Source: `web/src/lib/api.ts:223-240` (`extractMpnConflict`)
- Rule: `CLAUDE.md:105-108`
- Related: ADR-0003 (envelope-spread of `HTTPException(detail=…)`)
