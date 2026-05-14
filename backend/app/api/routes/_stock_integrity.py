from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

_STOCK_NONNEG_NAMES = {
    "ck_stock_nonneg",
    "check_stock_nonneg",
    "stock_nonneg_trigger",
}


def raise_integrity_as_409(exc: IntegrityError) -> NoReturn:
    """Map the stock non-negative trigger to the public conflict contract."""
    if _is_stock_nonneg_violation(exc):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "insufficient stock",
                "constraint": "stock_nonneg_trigger",
            },
        ) from exc
    raise exc


def _is_stock_nonneg_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    if diag is None:
        return False

    diagnostic_names = {
        getattr(diag, "constraint_name", None),
        getattr(diag, "source_function", None),
        getattr(diag, "trigger_name", None),
    }
    return bool(_STOCK_NONNEG_NAMES.intersection(diagnostic_names))
