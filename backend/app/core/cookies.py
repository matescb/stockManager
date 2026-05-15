from __future__ import annotations

from starlette.responses import Response

from app.core.config import settings

LEGACY_COOKIE_PATH = "/"
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


def delete_session_cookie(response: Response, cookie_name: str) -> None:
    attrs = session_cookie_attrs()
    response.delete_cookie(cookie_name, **attrs)
    response.delete_cookie(cookie_name, **{**attrs, "path": LEGACY_COOKIE_PATH})


def delete_workspace_cookie(response: Response) -> None:
    attrs = workspace_cookie_attrs()
    response.delete_cookie(WORKSPACE_COOKIE_NAME, **attrs)
    response.delete_cookie(WORKSPACE_COOKIE_NAME, **{**attrs, "path": LEGACY_COOKIE_PATH})
