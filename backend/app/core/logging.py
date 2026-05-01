"""Structured logging foundation.

The 2026-04-30 review's BE MED-5: zero `logging.getLogger` calls
anywhere in the codebase. Sentry catches errors but the running app
had no log line for login/logout/provider failure/build consume/order
receive, which made every "what happened five minutes ago" question
require reading the database.

This module configures the stdlib `logging` package once at FastAPI
startup so the rest of the codebase can `logging.getLogger(__name__)`
and call `.info(...)` / `.warning(...)` / `.error(...)` without
worrying about handler setup. Logs go to stdout (Docker captures
them as the container's stream); format depends on `APP_ENV`:

- `prod`  → JSON-per-line, easy to grep / ship to a log aggregator.
- `dev`/anything else → human-readable single line with timestamp.

This is a minimum-viable foundation, NOT a complete observability
solution. The follow-up is to add `logger.info(...)` lines at every
state-change boundary (login, logout, build consume, order receive,
attachment upload, etc.). This module makes that follow-up zero-config.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any


_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """Compact JSON-per-line formatter. No multi-line stack traces in
    the body; tracebacks land in `traceback` as a single string."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Custom fields attached via `logger.info("...", extra={...})`.
        for k, v in record.__dict__.items():
            if k in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "asctime", "taskName",
            ):
                continue
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                out[k] = repr(v)
        if record.exc_info:
            out["traceback"] = self.formatException(record.exc_info)
        return json.dumps(out, separators=(",", ":"))


def configure_logging() -> None:
    """Idempotent. Called once from main.py at app construction."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    from app.core.config import settings

    cfg = settings()
    is_prod = cfg.APP_ENV == "prod"

    handler = logging.StreamHandler(sys.stdout)
    if is_prod:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Don't double-up if uvicorn / pytest has already attached handlers
    # — rip them out and replace with ours so format is consistent.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    # Quiet down noisy stdlib / lib loggers — these are too chatty at
    # INFO and we don't normally need their per-request lines.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper. Equivalent to `logging.getLogger(name)`,
    but keeps the import surface stable if we ever swap out the
    foundation (e.g., to structlog)."""
    return logging.getLogger(name)
