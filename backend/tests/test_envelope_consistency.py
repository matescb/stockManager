from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core.errors import ErrorCodes, raise_http
from app.main import app


def _route_modules() -> list[Path]:
    files: set[Path] = set()
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        try:
            source = Path(inspect.getsourcefile(endpoint) or "").resolve()
        except TypeError:
            continue
        if source.parts[-4:-1] == ("app", "api", "routes"):
            files.add(source)
    return sorted(files)


@pytest.mark.parametrize("route_module", _route_modules(), ids=lambda p: p.name)
def test_all_error_paths_return_detail_dict(route_module: Path) -> None:
    tree = ast.parse(route_module.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "HTTPException":
            offenders.append(f"{route_module}:{node.lineno}")

    assert not offenders, (
        "Route modules must use raise_http(...) so HTTPException.detail is "
        f"a dict with code/message keys. Raw call sites: {', '.join(offenders)}"
    )


def test_raise_http_detail_contract() -> None:
    with pytest.raises(HTTPException) as raised:
        raise_http(400, ErrorCodes.STOCK_OPERATION_ERROR, "bad stock request", field="quantity")

    detail = raised.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == ErrorCodes.STOCK_OPERATION_ERROR
    assert detail["message"] == "bad stock request"
    assert detail["field"] == "quantity"
