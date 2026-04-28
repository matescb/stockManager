from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


def ok(data: Any = None, message: str = "OK") -> dict[str, Any]:
    return {"data": data, "status": {"category": "ok", "message": message}}


def err(category: str, message: str, errors: list[dict] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"data": None, "status": {"category": category, "message": message}}
    if errors is not None:
        body["errors"] = errors
    return body


async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=err(_category_for_status(exc.status_code), str(exc.detail)),
    )


def _category_for_status(code: int) -> str:
    if code == 401:
        return "unauthenticated"
    if code == 403:
        return "forbidden"
    if code == 404:
        return "not_found"
    if code == 409:
        return "conflict"
    if 400 <= code < 500:
        return "validation_error"
    if code >= 500:
        return "server_error"
    return "ok"


async def validation_exception_handler(_: Request, exc):  # pydantic ValidationError
    fields = []
    try:
        for e in exc.errors():
            fields.append({"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg", "")})
    except Exception:
        pass
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=err("validation_error", "validation failed", fields),
    )
