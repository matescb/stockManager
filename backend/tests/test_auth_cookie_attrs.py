from __future__ import annotations

from http.cookies import Morsel, SimpleCookie
from typing import Any

from app.core.config import settings
from app.core.cookies import WORKSPACE_COOKIE_NAME
from tests._factories import signup_user


def _cookie_from(response: Any, name: str) -> Morsel[str]:
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        if name in parsed:
            return parsed[name]
    raise AssertionError(f"missing Set-Cookie for {name}: {response.headers!r}")


def _security_attrs(cookie: Morsel[str]) -> dict[str, bool | str]:
    return {
        "path": cookie["path"],
        "httponly": bool(cookie["httponly"]),
        "secure": bool(cookie["secure"]),
        "samesite": cookie["samesite"].lower(),
    }


def test_set_and_delete_attributes_match(client):
    signup = signup_user(client)
    workspace_id = signup.json()["data"]["workspace_id"]
    session_cookie_name = settings().SESSION_COOKIE_NAME

    session_set = _cookie_from(signup, session_cookie_name)
    assert _security_attrs(session_set) == {
        "path": "/api",
        "httponly": True,
        "secure": False,
        "samesite": "lax",
    }

    switch = client.post(f"/api/workspaces/{workspace_id}/switch")
    assert switch.status_code == 200, switch.text
    workspace_set = _cookie_from(switch, WORKSPACE_COOKIE_NAME)
    assert _security_attrs(workspace_set) == {
        "path": "/api",
        "httponly": True,
        "secure": False,
        "samesite": "strict",
    }

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200, logout.text

    session_delete = _cookie_from(logout, session_cookie_name)
    assert session_delete.value == ""
    assert session_delete["max-age"] == "0"
    assert _security_attrs(session_delete) == _security_attrs(session_set)

    workspace_delete = _cookie_from(logout, WORKSPACE_COOKIE_NAME)
    assert workspace_delete.value == ""
    assert workspace_delete["max-age"] == "0"
    assert _security_attrs(workspace_delete) == _security_attrs(workspace_set)
