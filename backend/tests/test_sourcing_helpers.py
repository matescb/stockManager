from __future__ import annotations

import pytest

from app.domain.sourcing.service import chunk_mpns, dedupe_mpns


def test_dedupe_preserves_order_and_first_casing():
    assert dedupe_mpns(["ABC", "abc", "  ABC  ", "DEF", "def", "Ghi"]) == [
        "ABC",
        "DEF",
        "Ghi",
    ]


def test_dedupe_strips_whitespace():
    assert dedupe_mpns(["  ABC  ", "\tDEF\n", " abc "]) == ["ABC", "DEF"]


def test_dedupe_drops_empty_and_none():
    assert dedupe_mpns([" ", "", None, "ABC"]) == ["ABC"]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (1, [["1"], ["2"], ["3"], ["4"], ["5"], ["6"], ["7"], ["8"]]),
        (7, [["1", "2", "3", "4", "5", "6", "7"], ["8"]]),
        (50, [["1", "2", "3", "4", "5", "6", "7", "8"]]),
    ],
)
def test_chunk_partitions_correctly_for_various_sizes(size: int, expected: list[list[str]]):
    assert chunk_mpns(["1", "2", "3", "4", "5", "6", "7", "8"], size=size) == expected


def test_chunk_rejects_size_zero():
    with pytest.raises(ValueError):
        chunk_mpns(["ABC"], size=0)


def test_chunk_rejects_size_above_50():
    with pytest.raises(ValueError):
        chunk_mpns(["ABC"], size=51)


def test_chunk_handles_empty_input():
    assert chunk_mpns([], size=50) == []
