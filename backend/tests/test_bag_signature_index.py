"""Pin the partial-index predicate on `ix_stock_ws_bag_signature`
(DB-008 / alembic 0019).

The original index from `0012` was non-partial and indexed every
`stock_entries` row, even though only scan-import rows ever set
`bag_signature`. `0019` drops + recreates it with a `WHERE
bag_signature IS NOT NULL` predicate so the index doesn't pay an
insert cost on the 99% of NULL rows.

This test queries `pg_indexes` for the index def and asserts the
predicate is present. If a future migration drops the predicate,
this test fires.
"""
from __future__ import annotations

from sqlalchemy import text

from app.infra.db import SessionLocal


def test_bag_signature_index_has_partial_predicate() -> None:
    with SessionLocal() as s:
        row = s.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_stock_ws_bag_signature'"
            )
        ).fetchone()
    assert row is not None, "index ix_stock_ws_bag_signature is missing"
    indexdef = row[0]
    # Postgres normalises the WHERE clause to upper or lower depending
    # on Postgres version; case-insensitive match keeps this stable.
    lowered = indexdef.lower()
    assert "where" in lowered, f"non-partial index: {indexdef}"
    assert "bag_signature is not null" in lowered, (
        f"index predicate doesn't match expected: {indexdef}"
    )
