# sourcing

Audience: engineer

TrustedParts sourcing domain scaffold for live authorized-distributor offers.

## Purpose

This module will own TrustedParts sourcing, separate from catalog enrichment providers under `backend/app/domain/parts/providers/`.

## Boundary

- Workspace-scoped credentials and lookups.
- Live authorized-distributor offers, not persisted catalog specs.
- Cache entries capped at 7 days.
- Parts-count request budget tracking.

## Files

| File | Planned role |
|---|---|
| `client.py`, `schemas.py`, `models.py` | Client, DTOs, models |
| `cache.py`, `budget.py` | Cache helpers, request budget tracker |
| `service.py`, `factory.py` | Route facade, workspace-scoped provider construction |

## See also

- [ADR-0019 — TrustedParts sourcing provider split](../../../../docs/adr/0019-trustedparts-sourcing-provider-split.md)
