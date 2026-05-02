from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


# Connection pool sizing — BE2-023
#
# Prod runs --workers 1 (see CLAUDE.md).  With a single process the
# effective ceiling is pool_size + max_overflow = 30 connections, well
# within Postgres's default max_connections=100.  If we ever bump
# --workers we must revisit these numbers (or switch to PgBouncer).
#
#   pool_size=10       headroom over the implicit default of 5
#   max_overflow=20    burst capacity; connections above pool_size are
#                      closed when released rather than returned to pool
#   pool_recycle=1800  recycle connections after 30 min — below most
#                      cloud-provider idle-TCP-kill defaults (~60 min)
#   pool_timeout=30    raise PoolTimeout rather than hanging forever
#                      when all connections are checked out
#   pool_pre_ping=True round-trip SELECT 1 on checkout; drops silently
#                      recycled connections instead of surfacing an error
_engine = create_engine(
    settings().DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
    pool_timeout=30,
)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """Per-request session with implicit transaction boundaries.

    The dep yields the session, then commits on clean exit and rolls
    back on any raised exception. Routes must NOT call `db.commit()`
    themselves — the dep owns the boundary. A route that committed
    halfway through and then raised would leave a partial-write state
    that the dep can no longer roll back (BE2-010).

    The single exception is `bulk_import_from_scan`: it uses per-row
    `db.begin_nested()` savepoints so a single bad row doesn't take
    out the rest of the batch. The OUTER commit still flows through
    this dep.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_engine():
    return _engine
