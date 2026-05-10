# FX

ECB daily reference-rate snapshots for display-only currency conversion.

- `models.py` owns the global `fx_rate_snapshots` table.
- `rates.py` fetches the ECB daily XML, caches one JSONB snapshot per UTC date, and converts through EUR cross-rates.
- Snapshots are not workspace-owned because ECB rates are public reference data; see `docs/domain/sourcing.md`.
