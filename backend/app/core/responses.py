from __future__ import annotations

from typing import Any, Generic, TypedDict, TypeVar, cast

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Typed envelope (CQ-007 / issue #123).
#
# The `{data, status}` API envelope is a hard invariant — see CLAUDE.md
# and `docs/ARCHITECTURE.md` § API conventions. Before this change the
# helpers returned `dict[str, Any]`, which meant route signatures lost
# all information about *what* `data` actually was. The new generic
# `Envelope[T]` annotation is purely a typing aid: at runtime the
# helpers still return a plain ``dict`` so error paths can spread
# unknown keys (e.g. ``existing_id`` on 409) onto the top level without
# tripping a strict schema.
#
# This is intentionally a `TypedDict` rather than a Pydantic
# `BaseModel`:
#  * runtime cost stays at the dict level — no validation, no
#    serialization round-trip,
#  * OpenAPI doesn't render an opaque `Envelope` schema across every
#    untyped route (we'd lose the existing per-endpoint shape info),
#  * the ``message`` extension on `Status` lets us extend the error
#    payload with arbitrary keys (the http exception handler already
#    spreads them).
# ---------------------------------------------------------------------------


T = TypeVar("T")


class Status(TypedDict):
    category: str
    message: str


class Envelope(TypedDict, Generic[T]):
    data: T | None
    status: Status


# Concrete error-side envelope. Routes that surface structured 4xx
# extras (e.g. ``existing_id`` on a 409 conflict) keep doing so via
# ``raise HTTPException(detail={...})``; the http exception handler
# spreads them onto the top level so we don't need to enumerate them
# here. Annotated as ``dict[str, Any]`` to make that explicit.
ErrorEnvelope = dict[str, Any]


def ok(data: T | None = None, message: str = "OK") -> Envelope[T]:
    """Wrap a payload in the standard `{data, status}` envelope.

    Returns a plain dict at runtime; the `Envelope[T]` annotation is
    purely for static analysis. Route handlers that already declare a
    Pydantic ``*Out`` schema can annotate their return type as
    ``Envelope[PartOut]`` (or similar) to propagate that shape.
    """
    return cast(
        Envelope[T],
        {"data": data, "status": {"category": "ok", "message": message}},
    )


def err(
    category: str,
    message: str,
    errors: list[dict] | None = None,
    *,
    request_id: str | None = None,
) -> ErrorEnvelope:
    """Build an error envelope. Mirrors :func:`ok` but allows arbitrary
    extra keys (the FastAPI exception handler spreads structured
    ``detail`` dicts onto the top level).

    Pass ``request_id`` (from ``request.state.request_id``) to surface the
    correlation id in the response body alongside the ``X-Request-Id`` header
    (BE2-012 / issue #61).
    """
    body: ErrorEnvelope = {"data": None, "status": {"category": category, "message": message}}
    if errors is not None:
        body["errors"] = errors
    if request_id is not None:
        body["request_id"] = request_id
    return body


async def http_exception_handler(request: Request, exc: HTTPException):
    # Routes can raise HTTPException(detail={"message": "...", ...extras})
    # to surface structured context alongside the human-readable message.
    # The "message" key (or stringified detail) goes into status.message;
    # any remaining dict keys are spread onto the top-level response so
    # the frontend can act on them (e.g. existing_id from a 409 conflict).
    rid: str | None = getattr(request.state, "request_id", None)
    if isinstance(exc.detail, dict):
        message = exc.detail.get("message") or str(exc.detail)
        body = err(_category_for_status(exc.status_code), message, request_id=rid)
        for k, v in exc.detail.items():
            if k != "message":
                body[k] = v
    else:
        body = err(_category_for_status(exc.status_code), str(exc.detail), request_id=rid)
    # Log 4xx at INFO (useful "did the FE just regress?" signal) and 5xx at
    # ERROR (matched by Sentry but preserved independently of it).
    # BE2-012 / issue #61: request_id is injected by RequestIdFilter via the
    # contextvar, so it appears in the log record automatically.
    log_extra = {
        "status": exc.status_code,
        "path": request.url.path,
        "method": request.method,
        "detail": str(exc.detail)[:500],
        "request_id": rid,
    }
    if exc.status_code >= 500:
        _log.error("http error", extra=log_extra)
    else:
        _log.info("http %d", exc.status_code, extra=log_extra)
    return JSONResponse(status_code=exc.status_code, content=body)


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


async def validation_exception_handler(request: Request, exc):  # pydantic ValidationError
    rid: str | None = getattr(request.state, "request_id", None)
    fields = []
    try:
        for e in exc.errors():
            fields.append({"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg", "")})
    except Exception:
        pass
    # Log validation failures at INFO so the journal captures "did the
    # frontend send a malformed body?" signals without requiring Sentry.
    # BE2-012 / issue #61.
    _log.info(
        "validation error",
        extra={
            "path": request.url.path,
            "fields": fields,
            "request_id": rid,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=err("validation_error", "validation failed", fields, request_id=rid),
    )
