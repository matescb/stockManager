"""Shared SlowAPI Limiter instance.

slowapi's decorators carry a reference to the Limiter, and the per-request
middleware reads `request.app.state.limiter`. Both must be the same object
for the bucket store to be consistent — putting the instance here means the
import order in main.py / auth.py doesn't matter and there's no risk of two
parallel buckets.

Disabled outside prod so the test suite (and local dev) can hammer endpoints
without burning through the limit. The decorators stay in place either way
so the wiring is always exercised.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings().APP_ENV == "prod",
)
