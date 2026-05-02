from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_engine = create_engine(settings().DATABASE_URL, future=True, pool_pre_ping=True)
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
