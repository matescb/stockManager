# FX

ECB daily reference-rate snapshots for display-only currency conversion.

- `models.py` owns the global `fx_rate_snapshots` table.
- `rates.py` fetches the ECB daily XML, caches one JSONB snapshot per UTC date, and converts through EUR cross-rates.
- `_apply.py` annotates sourcing offers with converted display fields while preserving native prices.
- Snapshots are not workspace-owned because ECB rates are public reference data; see `docs/domain/sourcing.md`.
