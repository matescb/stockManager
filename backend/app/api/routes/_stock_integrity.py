from __future__ import annotations

from typing import NoReturn

from fastapi import status
from sqlalchemy.exc import DBAPIError

from app.core.errors import ErrorCodes, raise_http

_STOCK_NONNEG_NAMES = {
    "ck_stock_nonneg",
    "check_stock_nonneg",
    "stock_nonneg_trigger",
}
_WORKSPACE_ISOLATION_SQLSTATE = "WS001"


def raise_integrity_as_409(exc: DBAPIError) -> NoReturn:
    """Map the stock non-negative trigger to the public conflict contract."""
    if _is_workspace_isolation_violation(exc):
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.WORKSPACE_ISOLATION,
            message="workspace isolation violation",
            sqlstate=_WORKSPACE_ISOLATION_SQLSTATE,
        )
    if _is_stock_nonneg_violation(exc):
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.STOCK_INSUFFICIENT,
            message="insufficient stock",
            constraint="stock_nonneg_trigger",
        )
    raise exc


def _is_workspace_isolation_violation(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    return _sqlstate(orig) == _WORKSPACE_ISOLATION_SQLSTATE


def _sqlstate(orig: object | None) -> str | None:
    if orig is None:
        return None
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _is_stock_nonneg_violation(exc: DBAPIError) -> bool:
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
