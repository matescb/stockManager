from __future__ import annotations

from app.core.config import settings

SESSION_COOKIE_PATH = "/api"
WORKSPACE_COOKIE_NAME = "stockmgr_workspace"
WORKSPACE_COOKIE_PATH = "/api"


def secure_cookie_enabled() -> bool:
    return settings().APP_ENV == "prod"


def session_cookie_attrs() -> dict[str, bool | str]:
    return {
        "httponly": True,
        "secure": secure_cookie_enabled(),
        "samesite": "lax",
        "path": SESSION_COOKIE_PATH,
    }


def workspace_cookie_attrs() -> dict[str, bool | str]:
    return {
        "httponly": True,
        "secure": secure_cookie_enabled(),
        "samesite": "strict",
        "path": WORKSPACE_COOKIE_PATH,
    }
