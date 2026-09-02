"""Who is calling an MCP tool, and the session that tool runs in.

The MCP surface has no FastAPI dependency graph. A JSON-RPC call arrives
as one POST to `/mcp`; the tool the client asked for is dispatched by the
SDK, several frames below anything that saw the HTTP request. So the
three things every route gets for free — the authenticated user, the
workspace, and a database session whose transaction someone else commits
— have to be carried across that gap explicitly.

They are carried as a `Principal` in a contextvar, set by
`app/mcp/auth.py` once the credential has been verified and reset when
the request ends.

**Ids, never ORM objects.** The wrapper authenticates in its own
short-lived session and closes it before the tool runs. Handing the tool
a `User` bound to that dead session would give it a detached instance
that explodes on first lazy load; handing it one bound to a *live*
session would share a session across a thread boundary. So the
contextvar carries UUIDs, and `unit_of_work` re-loads the rows in the
session the tool actually uses.

That the contextvar reaches the tool at all depends on the server
running in **stateless** mode, where the SDK handles each request inline
in the caller's task rather than handing it to a task group started at
lifespan time (a child task would inherit the task group's context, not
ours). ADR-0030 records that as a reason stateless was chosen and not
merely a consequence of it; `tests/test_mcp.py` pins it with a
concurrent two-token probe.
"""
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.tokens.models import ApiToken
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace


@dataclass(frozen=True)
class Principal:
    """The verified caller behind one MCP request.

    `role` is the token owner's membership role in `workspace_id`,
    resolved once at authentication time — a snapshot, because it cannot
    change mid-request and re-querying it per tool call would be a query
    per call for an answer that is already known.

    The token's `read_only` flag is deliberately NOT snapshotted here.
    `unit_of_work` re-loads the `ApiToken` row anyway, so the write gate
    reads it live off `Caller.token`; carrying a second copy would give
    two places to read it from and eventually two answers.
    """

    user_id: UUID
    workspace_id: UUID
    token_id: UUID
    role: str
    request_id: str | None


_PRINCIPAL: contextvars.ContextVar[Principal] = contextvars.ContextVar(
    "stockmgr_mcp_principal"
)


class NoPrincipal(RuntimeError):
    """A tool ran with no authenticated caller in context.

    Unreachable through the mount — `app/mcp/auth.py` rejects an
    unauthenticated request before the SDK sees it. Raised rather than
    defaulted so that a future refactor which moves tool dispatch off
    the request's context fails loudly instead of running the tool
    against whatever workspace it finds.
    """


@contextmanager
def bind(principal: Principal) -> Iterator[None]:
    token = _PRINCIPAL.set(principal)
    try:
        yield
    finally:
        _PRINCIPAL.reset(token)


def current() -> Principal:
    try:
        return _PRINCIPAL.get()
    except LookupError as exc:  # pragma: no cover — see NoPrincipal
        raise NoPrincipal("no authenticated MCP principal in context") from exc


@dataclass(frozen=True)
class Caller:
    """A principal re-materialised as live ORM rows in one session."""

    db: Session
    user: User
    ws: Workspace
    token: ApiToken
    principal: Principal


_DML_VERBS = ("INSERT", "UPDATE", "DELETE")


class UndeclaredWrite(RuntimeError):
    """A tool declared `writes=False` tried to change the database.

    Raised by `unit_of_work` INSTEAD of committing, so the attempted
    write is rolled back rather than reaching disk. `tools/_registry.py`
    turns it into a tool error.

    This is the backstop for the failure mode the write flag exists to
    prevent, and it is not hypothetical: `sourcing_offers` shipped as a
    read tool while spending the workspace's provider quota and writing
    the sourcing cache. A structural test cannot catch that class of bug,
    because a structural test only ever compares one declaration against
    another. This compares the declaration against what the tool
    actually did.
    """


class _DmlWatcher:
    """Records whether any DML crossed a session's connection.

    Statement-level, not ORM-level, and that distinction is the whole
    point. `Session.new` / `.dirty` / `.deleted` only see the ORM's unit
    of work, so a Core `db.execute(insert(...))` is invisible to them —
    which is exactly how the sourcing cache writes
    (`domain/sourcing/cache.py::get_or_fetch` uses
    `pg_insert(...).on_conflict_do_update`). An ORM-only guard would have
    waved through the very bug that motivated this one.

    Attaching to the connection also means the guard costs one string
    split per statement and nothing else. `SAVEPOINT` / `RELEASE` — which
    the test fixture emits constantly — are not DML and do not trip it.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(
        self, _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        head = statement.lstrip()[:6].upper()
        for verb in _DML_VERBS:
            if head.startswith(verb):
                self.statements.append(verb)
                return


def _orm_pending(db: Session) -> bool:
    """Whether the session holds unflushed ORM changes.

    `Session.dirty` is documented as a best guess — it lists instances
    with any attribute set, modified or not — so it is filtered through
    `is_modified`, which compares against the loaded value.
    """
    if db.new or db.deleted:
        return True
    return any(db.is_modified(obj) for obj in db.dirty)


@contextmanager
def unit_of_work(*, writes: bool = True) -> Iterator[Caller]:
    """One session, one transaction, for one tool call.

    Mirrors `app/cli/run_job.py::run_job`'s ownership rather than
    `infra/db.py::get_db`'s: there is no FastAPI dependency to close the
    transaction here, so this context manager commits on a clean exit,
    rolls back on any exception, and closes either way. Domain services
    only ever `flush()`, so without the commit below every MCP mutation
    would be silently discarded.

    `writes=False` turns the commit into an assertion: if the tool
    changed anything, the change is rolled back and `UndeclaredWrite` is
    raised instead. Defaults to True so that a caller which forgets the
    argument gets the permissive behaviour rather than a spurious
    failure — the decorator in `tools/_registry.py` always passes it
    explicitly, and it is the only caller.

    `SessionLocal` is looked up on the module at call time, not imported
    at module scope: `tests/conftest.py` monkeypatches
    `app.infra.db.SessionLocal` onto the per-test savepoint-joined
    factory, and a name bound at import would miss the patch and write
    to a connection the test teardown never rolls back.

    The three rows re-loaded here were all verified during
    authentication. They are re-read rather than re-checked because the
    only failure mode left is a row deleted between authentication and
    dispatch — microseconds — and the tool's own workspace filters would
    return nothing anyway.
    """
    from sqlalchemy import event

    from app.infra.db import SessionLocal

    principal = current()
    db = SessionLocal()
    watcher = _DmlWatcher() if not writes else None
    connection = None
    try:
        if watcher is not None:
            # `connection()` begins the transaction now rather than on
            # first query. That is a no-op in practice — the tool is
            # about to query — and it is the only way to get a handle to
            # listen on.
            connection = db.connection()
            event.listen(connection, "before_cursor_execute", watcher)

        user = db.get(User, principal.user_id)
        ws = db.get(Workspace, principal.workspace_id)
        token = db.get(ApiToken, principal.token_id)
        if user is None or ws is None or token is None:  # pragma: no cover
            raise NoPrincipal("MCP principal no longer resolves")
        yield Caller(db=db, user=user, ws=ws, token=token, principal=principal)

        if watcher is not None and (watcher.statements or _orm_pending(db)):
            db.rollback()
            raise UndeclaredWrite(
                "tool is declared read-only but modified the database "
                f"(statements={sorted(set(watcher.statements))}, "
                f"orm_pending={_orm_pending(db)})"
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if connection is not None and watcher is not None:
            event.remove(connection, "before_cursor_execute", watcher)
        db.close()
