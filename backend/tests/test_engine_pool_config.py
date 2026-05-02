"""Assert that the SQLAlchemy engine is created with explicit pool sizing.

BE2-023: pool defaults (5+10) are too small for future worker bumps and
give no signal on exhaustion.  These tests pin the values so an accidental
revert is caught by CI rather than discovered in prod under load.
"""
from __future__ import annotations

from app.infra.db import get_engine


def test_pool_size():
    """pool_size controls the persistent connection count kept open."""
    assert get_engine().pool.size() == 10


def test_max_overflow():
    """max_overflow allows burst connections beyond pool_size."""
    assert get_engine().pool._max_overflow == 20


def test_pool_recycle():
    """pool_recycle=1800 s recycles connections after 30 min, below
    cloud-provider idle-TCP-kill defaults."""
    assert get_engine().pool._recycle == 1800


def test_pool_timeout():
    """pool_timeout=30 s raises PoolTimeout fast on exhaustion instead
    of hanging indefinitely."""
    assert get_engine().pool._timeout == 30


def test_pool_pre_ping():
    """pool_pre_ping must stay True — it drops silently recycled
    connections on checkout instead of surfacing them as errors."""
    assert get_engine().pool._pre_ping is True
