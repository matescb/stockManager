"""The contract every MCP tool is held to.

`@tool` is the only way a function becomes a tool. It supplies the five
things that would otherwise be copy-pasted nineteen times and drift:

1. a session whose transaction is committed on success and rolled back
   on failure (`principal.unit_of_work`),
2. the write gate — read-only token, then viewer role — for tools
   declared `writes=True`,
3. a per-tool, per-workspace rate limit,
4. translation of the app's `HTTPException`s into MCP tool errors that
   carry the app's own error-code string,
5. the runtime assertion that a `writes=False` tool did not, in fact,
   write.

(5) is the interesting one. A tool that mutates and forgets
`writes=True` accepts a read-only token and a viewer, silently — and a
structural test cannot catch it, because a structural test compares one
declaration against another and both sides come from the same
declaration. `unit_of_work(writes=False)` compares the declaration
against what the tool actually did to the database, and refuses to
commit if they disagree.

Anything the SDK does with an unhandled exception is the SDK's business:
it wraps a crash as `UnexpectedToolError`, returns `Error executing tool
<name>` to the client and logs the traceback server-side. This module
does not add a firewall on top of that; what it adds is the translation
in (4), so that an *anticipated* failure reads as an answer rather than
as a crash.
"""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import UUID

from fastapi import HTTPException
from limits import RateLimitItem, parse
from mcp.server.mcpserver.exceptions import ToolError

from app.core.errors import ErrorCodes
from app.core.ratelimit import limiter
from app.mcp.principal import Caller, UndeclaredWrite, unit_of_work

_log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Populated by the decorator at import time; `app/mcp/server.py` reads it
# to register tools, and the tests read it to check the write set.
REGISTRY: list["ToolSpec"] = []

# Re-exported so tool modules raise the SDK's error type without each
# one importing from `mcp.server.mcpserver.exceptions` directly — the
# path the SDK's v1→v2 rename moved once already.
__all__ = ["REGISTRY", "ToolError", "ToolSpec", "require_write", "tool"]

# Membership floor for any tool that writes. Same rank table and same
# floor as `core/deps.py::require_member_for_writes`, which is the rule
# this mirrors for the HTTP surface.
_WRITE_ROLE = "member"
_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}

# Ceiling for a tool that declares none. Generous, because the cheap
# tools are cheap; it exists so that "every tool has a limit" is true
# without each one having to think about it.
DEFAULT_RATE = "120/minute"


class ToolSpec:
    """One registered tool: its callable, its name, and whether it writes."""

    def __init__(
        self, fn: Callable[..., Any], name: str, writes: bool, rate: str
    ) -> None:
        self.fn = fn
        self.name = name
        self.writes = writes
        self.rate = rate


def _error(code: str, message: str) -> ToolError:
    """A tool error the model can act on.

    The app's error code leads because it is the stable half — an agent
    that sees `part.not_found` can decide to search instead of retrying,
    where the human-readable half may be reworded at any time.
    """
    return ToolError(f"{code}: {message}")


def require_write(caller: Caller) -> None:
    """Refuse a write from a credential that may not make one.

    Two checks, in the same order the HTTP surface applies them, because
    the answers differ and the caller needs to know which one they hit:

    * `read_only` is a property of the CREDENTIAL. This is the token
      pasted into a KiCad config file or a PCM URL (phases 5/6); the
      whole point of minting one is that its exposure cannot cost you
      any writes. `core/deps.py` refuses those at the HTTP layer by
      method, which is why the check has to be re-stated here rather
      than inherited — see `app/mcp/auth.py`.
    * the role is a property of the PERSON. A viewer's token is
      viewer-powered no matter how it was minted.

    Both are refusals, not crashes: the tool is still listed, and the
    model is told in a sentence what it would need to proceed.
    """
    if caller.token.read_only:
        raise _error(
            ErrorCodes.AUTH_TOKEN_READ_ONLY,
            "this tool writes and the token is read-only; mint a "
            "full-access token to use it",
        )
    if _ROLE_RANK.get(caller.principal.role, 0) < _ROLE_RANK[_WRITE_ROLE]:
        raise _error(
            ErrorCodes.RESOURCE_INSUFFICIENT_ROLE,
            f"this tool writes and requires role {_WRITE_ROLE}+ in this workspace",
        )


def enforce_rate_limit(tool_name: str, item: RateLimitItem, caller: Caller) -> None:
    """Refuse a tool call that exceeds its per-workspace ceiling.

    `/mcp` cannot use slowapi's route decorators. Those run inside
    FastAPI's endpoint wrapper, and this surface is one opaque ASGI
    mount that short-circuits before the router — so every tool was
    uncapped, including the three whose REST twins are the most
    expensive endpoints in the app (`fetch_lcsc` reaches out to
    EasyEDA, `import_vendor_zip` inflates an archive,
    `sourcing_offers` spends the workspace's paid provider quota).

    Enforced here rather than in the ASGI wrapper because the wrapper
    cannot see which tool a JSON-RPC body is asking for without parsing
    it — and by the time the tool runs, the name and the verified
    workspace are both in scope, which is exactly what the bucket key
    needs.

    Keyed per tool AND per workspace, matching `workspace_key`'s
    reasoning on the REST side: the quotas being protected are the
    tenant's, and one busy agent must not exhaust another tenant's
    budget. Two tools with different ceilings get different buckets, so
    a burst of cheap `search_parts` calls cannot consume the LCSC
    allowance.

    slowapi's limiter is disabled outside prod (`core/ratelimit.py`), so
    this is a no-op in dev and in the suite unless a test enables it —
    same as every REST route.
    """
    if not limiter.enabled:
        return
    key = f"mcp:{tool_name}:ws:{caller.ws.id}"
    if limiter.limiter.hit(item, key):
        return
    retry_after = None
    try:
        reset_at, _remaining = limiter.limiter.get_window_stats(item, key)
        retry_after = max(0, int(reset_at - time.time()))
    except Exception:  # pragma: no cover — window stats are advisory
        pass
    suffix = f"; retry in {retry_after}s" if retry_after is not None else ""
    raise _error(
        ErrorCodes.RATE_LIMITED,
        f"rate limit exceeded for {tool_name} ({item}){suffix}",
    )


def tool(
    *,
    writes: bool = False,
    rate: str = DEFAULT_RATE,
    name: str | None = None,
) -> Callable[[F], F]:
    """Register a function as an MCP tool.

    The wrapped function is called with a `Caller` as its first
    positional argument and the client's arguments as keywords. The
    signature the SDK advertises is the wrapped function's minus that
    first parameter, so the docstring and the type hints on everything
    after it ARE the agent-facing contract — write them for a reader who
    has never seen this codebase.

    Args:
        writes: Whether the tool changes anything. Gates the credential
            and role checks, and — just as importantly — arms the
            runtime assertion that a `False` here is the truth.
        rate: The per-workspace ceiling, in `limits` syntax. Declared
            next to `writes` so that a new expensive tool inherits the
            habit of naming its cost. Match the REST twin's number where
            there is one.
    """
    item = parse(rate)

    def decorate(fn: F) -> F:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(**kwargs: Any) -> Any:
            try:
                with unit_of_work(writes=writes) as caller:
                    if writes:
                        require_write(caller)
                    enforce_rate_limit(tool_name, item, caller)
                    try:
                        return fn(caller, **kwargs)
                    except ToolError:
                        raise
                    except HTTPException as exc:
                        raise _from_http_exception(exc) from None
            except UndeclaredWrite as exc:
                # Caught out here because `unit_of_work` raises it at
                # COMMIT time, on the way out of the `with` — after the
                # inner handler has already been left.
                _log.error("mcp tool %s: %s", tool_name, exc)
                raise _error(
                    ErrorCodes.MCP_UNDECLARED_WRITE,
                    f"{tool_name} is misdeclared as read-only and its changes "
                    "were discarded; this is a server bug, do not retry",
                ) from None

        _hide_caller_parameter(wrapper, fn)
        REGISTRY.append(
            ToolSpec(fn=wrapper, name=tool_name, writes=writes, rate=rate)
        )
        # The undecorated function is returned so the module-level name
        # stays directly callable as `fn(caller, ...)`. What the SDK
        # gets is `wrapper`, out of REGISTRY.
        return fn

    return decorate


def _hide_caller_parameter(wrapper: Callable[..., Any], fn: Callable[..., Any]) -> None:
    """Make `wrapper` look, to introspection, like `fn` without `caller`.

    The SDK derives each tool's JSON input schema from the callable's
    signature and annotations. `caller` is injected by this module and
    is not something a client could supply — worse, `Caller` holds a
    `Session` and has no schema — so both traces of it have to go:

    * `__signature__`, because `functools.wraps` sets `__wrapped__` and
      `inspect.signature` follows that back to the original parameter
      list;
    * `__annotations__`, because a schema builder that reads annotations
      directly rather than through the signature would otherwise still
      find it.

    `eval_str=True` is not optional. Every tool module opens with
    `from __future__ import annotations`, so each annotation is a
    STRING that only resolves against its own module's globals — and
    `wrapper` is defined here, in `_registry`, where names like
    `Literal` and `Caller` mean nothing. Resolving them eagerly, against
    `fn`'s globals where they are in scope, is what lets the SDK build a
    schema from them at all; left as strings, pydantic fails with
    "`<tool>Arguments` is not fully defined".
    """
    import inspect

    sig = inspect.signature(fn, eval_str=True)
    first, *rest = list(sig.parameters.values())
    del wrapper.__wrapped__
    wrapper.__signature__ = sig.replace(parameters=rest)  # type: ignore[attr-defined]
    wrapper.__annotations__ = {
        p.name: p.annotation
        for p in rest
        if p.annotation is not inspect.Parameter.empty
    }
    if sig.return_annotation is not inspect.Signature.empty:
        wrapper.__annotations__["return"] = sig.return_annotation
    assert first.name == "caller", (
        f"{fn.__qualname__} must take `caller` as its first parameter"
    )


def _from_http_exception(exc: HTTPException) -> ToolError:
    """Translate the app's structured `HTTPException` into a tool error.

    Domain services and `_helpers.assert_in_workspace` raise these for
    every anticipated failure — not found, cross-workspace, conflict,
    validation. Letting one escape would give the client
    `Error executing tool <name>` and a traceback in the server log for
    what is, from the model's point of view, a perfectly ordinary answer
    ("no such part"). So the code and message are lifted out and the
    status code is dropped: HTTP status has no meaning inside a
    JSON-RPC result.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or ErrorCodes.RESOURCE_NOT_FOUND)
        message = str(detail.get("message") or "request failed")
        # A few 409s carry structured extras the agent can use directly
        # (`existing_id` on an MPN collision, `constraint` on a storage
        # violation). Append them rather than dropping them.
        extras = {
            k: v for k, v in detail.items() if k not in ("code", "message")
        }
        if extras:
            rendered = ", ".join(f"{k}={_scalar(v)}" for k, v in sorted(extras.items()))
            message = f"{message} ({rendered})"
        return _error(code, message)
    return _error(ErrorCodes.RESOURCE_NOT_FOUND, str(detail))


def _scalar(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value)
