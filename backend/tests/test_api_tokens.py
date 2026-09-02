"""API tokens (PATs) — mint/list/revoke, header auth, CSRF, isolation.

The security-critical surface of this file is the CSRF matrix and the
"no cookie fallback" rule: `main.py::CsrfOriginMiddleware` skips the
Origin check whenever an `Authorization` header is present, which is
only sound because `core/deps.py::get_current_user` refuses to fall
back to the session cookie once that header exists. Both halves are
pinned here — breaking either one alone makes a test fail.

NOTE on Origin: `conftest.py` patches `TestClient.__init__` so every
client sends `Origin: http://testserver`. The CSRF cases need clients
that send no Origin at all, hence `_strip_origin()` below.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import deps
from app.core.time import utcnow
from app.domain.audit.models import AuditLog
from app.domain.tokens import service as tokens_service
from app.domain.tokens.models import ApiToken
from app.domain.workspaces.models import WorkspaceMember
from app.main import app
from tests._factories import signup_user

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_origin(client: TestClient) -> TestClient:
    """Remove the conftest-injected `Origin` header from a client.

    Returns the same client for chaining. Cookies already set on the
    client survive — that's what the cookie-vs-token CSRF cases need.
    """
    if "origin" in client.headers:
        del client.headers["origin"]
    return client


def _signup(email_prefix: str = "t") -> tuple[TestClient, str]:
    """Fresh client + workspace_id for a brand-new user."""
    c = TestClient(app)
    body = signup_user(c, email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@x.com").json()["data"]
    return c, body["workspace_id"]


def _invite_and_join(host: TestClient, role: str) -> tuple[TestClient, str]:
    """Invite a brand-new user into the host's workspace at `role`.

    Returns their client and email. The invitation is email-bound, so
    the invitee has to sign up under exactly the invited address.
    """
    email = f"{role}-{uuid.uuid4().hex[:8]}@x.com"
    invite = host.post("/api/invitations", json={"email": email, "role": role})
    assert invite.status_code in (200, 201), invite.text

    joiner = TestClient(app)
    signup_user(joiner, email=email)
    accepted = joiner.post(
        "/api/invitations/accept", json={"token": invite.json()["data"]["token"]}
    )
    assert accepted.status_code == 200, accepted.text
    return joiner, email


def _mint(client: TestClient, **body) -> dict:
    body.setdefault("label", "ci token")
    r = client.post("/api/tokens", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Token {token}"}


def _code(response) -> str | None:
    return response.json().get("code")


# ---------------------------------------------------------------------------
# Mint / list / revoke walk
# ---------------------------------------------------------------------------


def test_mint_returns_composite_plaintext_once(authed_client, db):
    data = _mint(authed_client, label="kicad laptop", read_only=True)

    plaintext = data["token"]
    assert plaintext.startswith("smk_")
    prefix, _, rest = plaintext.partition("_")
    id_hex, sep, secret = rest.partition(".")
    assert prefix == "smk"
    assert sep == "."
    # The id half is the row PK, so lookup is a PK equality check.
    assert uuid.UUID(id_hex) == uuid.UUID(data["id"])
    assert len(secret) >= 40
    assert data["read_only"] is True
    assert data["label"] == "kicad laptop"

    row = db.get(ApiToken, uuid.UUID(data["id"]))
    assert row is not None
    # The plaintext secret is never stored, in any form.
    assert secret not in (row.token_hmac or "")
    assert plaintext not in (row.token_hmac or "")


def test_list_never_re_exposes_plaintext_or_hmac(authed_client, db):
    data = _mint(authed_client, label="listable")
    plaintext = data["token"]
    row = db.get(ApiToken, uuid.UUID(data["id"]))

    r = authed_client.get("/api/tokens")
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert len(rows) == 1
    assert "token" not in rows[0]
    assert "token_hmac" not in rows[0]
    assert plaintext not in r.text
    assert row.token_hmac not in r.text
    assert rows[0]["label"] == "listable"
    assert rows[0]["revoked_at"] is None


def test_revoke_is_idempotent_and_kills_the_token(authed_client):
    data = _mint(authed_client)
    plaintext = data["token"]

    assert authed_client.get("/api/parts", headers=_auth(plaintext)).status_code == 200

    r = authed_client.post(f"/api/tokens/{data['id']}/revoke")
    assert r.status_code == 200, r.text
    # Second revoke is a no-op, not a 404/409.
    assert authed_client.post(f"/api/tokens/{data['id']}/revoke").status_code == 200

    listed = authed_client.get("/api/tokens").json()["data"][0]
    assert listed["revoked_at"] is not None

    dead = authed_client.get("/api/parts", headers=_auth(plaintext))
    assert dead.status_code == 401
    assert _code(dead) == "auth.invalid_token"


def test_create_rejects_unknown_field(authed_client):
    r = authed_client.post("/api/tokens", json={"label": "x", "scopes": ["*"]})
    assert r.status_code == 422, r.text
    assert "scopes" in r.text


@pytest.mark.parametrize("days", [0, 366, -1])
def test_expiry_days_are_bounded(authed_client, days):
    r = authed_client.post("/api/tokens", json={"label": "x", "expires_in_days": days})
    assert r.status_code == 422, r.text


def test_expires_in_days_sets_expiry(authed_client, db):
    data = _mint(authed_client, expires_in_days=30)
    row = db.get(ApiToken, uuid.UUID(data["id"]))
    delta = row.expires_at - utcnow()
    assert timedelta(days=29) < delta <= timedelta(days=30)


# ---------------------------------------------------------------------------
# Header auth against real routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["Token", "token", "Bearer", "bEaReR"])
def test_header_auth_accepts_both_schemes_case_insensitively(authed_client, scheme):
    plaintext = _mint(authed_client)["token"]
    anon = TestClient(app)
    r = anon.get("/api/parts", headers={"Authorization": f"{scheme} {plaintext}"})
    assert r.status_code == 200, r.text


def test_full_token_can_write(authed_client):
    plaintext = _mint(authed_client)["token"]
    anon = TestClient(app)
    r = anon.post(
        "/api/parts",
        json={"name": "Token-made part", "part_type": "local"},
        headers=_auth(plaintext),
    )
    assert r.status_code in (200, 201), r.text


def test_read_only_token_refuses_writes_but_allows_reads(authed_client):
    plaintext = _mint(authed_client, read_only=True)["token"]
    anon = TestClient(app)

    assert anon.get("/api/parts", headers=_auth(plaintext)).status_code == 200

    r = anon.post(
        "/api/parts",
        json={"name": "nope", "part_type": "local"},
        headers=_auth(plaintext),
    )
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_read_only"


def test_viewer_role_beats_read_only_false(authed_client, db):
    """A viewer's full-access token still can't write — the membership
    role is applied after token auth, exactly as for cookie sessions."""
    owner_ws = authed_client.get("/api/workspaces/current").json()["data"]["id"]
    viewer, _ = _invite_and_join(authed_client, "viewer")

    # Mint inside the invited workspace (viewers may mint their own tokens).
    minted = viewer.post(
        "/api/tokens",
        json={"label": "viewer token", "read_only": False},
        headers={"X-Workspace-Id": owner_ws},
    )
    assert minted.status_code == 201, minted.text
    plaintext = minted.json()["data"]["token"]

    anon = TestClient(app)
    r = anon.post(
        "/api/parts",
        json={"name": "viewer write", "part_type": "local"},
        headers=_auth(plaintext),
    )
    assert r.status_code == 403, r.text
    assert _code(r) == "resource.insufficient_role"


@pytest.mark.parametrize(
    "header",
    [
        "Token garbage",
        "Token smk_not-a-uuid.secret",
        "Token smk_" + uuid.uuid4().hex + ".wrong-secret",
        "Token smk_no-dot-separator",
        "Token ",
        "Basic YWxhZGRpbjpvcGVuc2VzYW1l",
        "Token " + uuid.uuid4().hex + ".secret",  # missing smk_ prefix
    ],
)
def test_bad_tokens_all_return_one_401_code(header):
    anon = TestClient(app)
    r = anon.get("/api/parts", headers={"Authorization": header})
    assert r.status_code == 401, r.text
    assert _code(r) == "auth.invalid_token"


def test_wrong_secret_for_real_id_is_401(authed_client):
    data = _mint(authed_client)
    forged = f"smk_{uuid.UUID(data['id']).hex}.definitely-not-the-secret"
    anon = TestClient(app)
    r = anon.get("/api/parts", headers=_auth(forged))
    assert r.status_code == 401
    assert _code(r) == "auth.invalid_token"


def test_expired_token_is_401(authed_client, db):
    data = _mint(authed_client, expires_in_days=1)
    row = db.get(ApiToken, uuid.UUID(data["id"]))
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.flush()

    r = authed_client.get("/api/parts", headers=_auth(data["token"]))
    assert r.status_code == 401
    assert _code(r) == "auth.invalid_token"


def test_token_whose_owner_lost_membership_is_401(authed_client, db):
    data = _mint(authed_client)
    row = db.get(ApiToken, uuid.UUID(data["id"]))
    membership = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == row.user_id,
            WorkspaceMember.workspace_id == row.workspace_id,
        )
    ).scalar_one()
    db.delete(membership)
    db.flush()

    anon = TestClient(app)
    r = anon.get("/api/parts", headers=_auth(data["token"]))
    assert r.status_code == 401
    assert _code(r) == "auth.invalid_token"


# ---------------------------------------------------------------------------
# Workspace pinning
# ---------------------------------------------------------------------------


def test_token_pins_the_workspace_and_beats_the_cookie(authed_client):
    """Cookie says workspace A, token says workspace B → B wins."""
    part_a = authed_client.post(
        "/api/parts", json={"name": "A-only part", "part_type": "local"}
    ).json()["data"]["id"]

    b, _ = _signup("b")
    part_b = b.post(
        "/api/parts", json={"name": "B-only part", "part_type": "local"}
    ).json()["data"]["id"]
    plaintext = _mint(b)["token"]

    # authed_client carries A's session cookie; the header carries B's token.
    r = authed_client.get("/api/parts", headers=_auth(plaintext))
    assert r.status_code == 200, r.text
    ids = {p["id"] for p in r.json()["data"]}
    assert part_b in ids
    assert part_a not in ids


def test_workspace_header_mismatch_is_403(authed_client):
    plaintext = _mint(authed_client)["token"]
    own_ws = authed_client.get("/api/workspaces/current").json()["data"]["id"]
    _, other_ws = _signup("other")

    anon = TestClient(app)
    same = anon.get(
        "/api/parts", headers={**_auth(plaintext), "X-Workspace-Id": own_ws}
    )
    assert same.status_code == 200, same.text

    r = anon.get("/api/parts", headers={**_auth(plaintext), "X-Workspace-Id": other_ws})
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_workspace_mismatch"

    garbage = anon.get(
        "/api/parts", headers={**_auth(plaintext), "X-Workspace-Id": "not-a-uuid"}
    )
    assert garbage.status_code == 403
    assert _code(garbage) == "auth.token_workspace_mismatch"


# ---------------------------------------------------------------------------
# CSRF matrix — the load-bearing trio
# ---------------------------------------------------------------------------


def test_csrf_token_authed_write_passes_without_origin(authed_client):
    plaintext = _mint(authed_client)["token"]
    anon = _strip_origin(TestClient(app))
    assert "origin" not in anon.headers

    r = anon.post(
        "/api/parts",
        json={"name": "no-origin token write", "part_type": "local"},
        headers=_auth(plaintext),
    )
    assert r.status_code in (200, 201), r.text


def test_csrf_cookie_plus_garbage_authorization_is_401_not_cookie_auth(authed_client):
    """The CSRF skip is only sound if a present Authorization header
    disables the cookie path entirely. If this ever returns 201 the
    exemption has become a real CSRF hole."""
    _strip_origin(authed_client)

    r = authed_client.post(
        "/api/parts",
        json={"name": "forged", "part_type": "local"},
        headers={"Authorization": "Token smk_deadbeef.nope"},
    )
    assert r.status_code == 401, r.text
    assert _code(r) == "auth.invalid_token"


def test_csrf_cookie_without_authorization_and_without_origin_is_403(authed_client):
    _strip_origin(authed_client)

    r = authed_client.post("/api/parts", json={"name": "csrf", "part_type": "local"})
    assert r.status_code == 403, r.text
    assert r.json()["status"]["message"] == "cross-origin request blocked"


def test_csrf_empty_authorization_header_does_not_open_the_gate(authed_client):
    """An empty header value must behave exactly like no header at all —
    on BOTH sides (middleware skip and deps fallback), or the two
    disagree and the skip stops being backed by the no-fallback rule."""
    _strip_origin(authed_client)

    r = authed_client.post(
        "/api/parts",
        json={"name": "empty auth", "part_type": "local"},
        headers={"Authorization": ""},
    )
    assert r.status_code == 403, r.text
    assert r.json()["status"]["message"] == "cross-origin request blocked"


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def test_last_used_telemetry_is_recorded(authed_client, db):
    data = _mint(authed_client)
    assert authed_client.get("/api/parts", headers=_auth(data["token"])).status_code == 200

    row = db.get(ApiToken, uuid.UUID(data["id"]))
    db.refresh(row)
    assert row.last_used_at is not None
    assert row.last_used_ip == "testclient"


def test_telemetry_failure_never_fails_the_request(authed_client, monkeypatch):
    plaintext = _mint(authed_client)["token"]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("telemetry exploded")

    monkeypatch.setattr("app.core.deps._record_token_use", _boom)

    r = authed_client.get("/api/parts", headers=_auth(plaintext))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Token-management self-lockout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/tokens", None),
        ("post", "/api/tokens", {"label": "second"}),
    ],
)
def test_token_authed_requests_cannot_touch_token_management(
    authed_client, method, path, body
):
    plaintext = _mint(authed_client)["token"]
    anon = TestClient(app)
    kwargs = {"headers": _auth(plaintext)}
    if body is not None:
        kwargs["json"] = body

    r = getattr(anon, method)(path, **kwargs)
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_no_token_management"


def test_token_authed_revoke_is_refused(authed_client):
    data = _mint(authed_client)
    anon = TestClient(app)
    r = anon.post(f"/api/tokens/{data['id']}/revoke", headers=_auth(data["token"]))
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_no_token_management"


# ---------------------------------------------------------------------------
# Workspace isolation + admin listing
# ---------------------------------------------------------------------------


def test_tokens_are_workspace_isolated():
    a, _ = _signup("a")
    b, _ = _signup("b")

    data = _mint(a, label="a's token")

    listed = b.get("/api/tokens")
    assert listed.status_code == 200, listed.text
    assert all(row["id"] != data["id"] for row in listed.json()["data"])

    # Cross-workspace revoke is a 404, never a 403 (no existence oracle).
    r = b.post(f"/api/tokens/{data['id']}/revoke")
    assert r.status_code == 404, r.text


def test_revoke_of_unknown_id_is_404(authed_client):
    r = authed_client.post(f"/api/tokens/{uuid.uuid4()}/revoke")
    assert r.status_code == 404, r.text


def test_admin_can_list_and_revoke_every_workspace_token(authed_client):
    owner_ws = authed_client.get("/api/workspaces/current").json()["data"]["id"]
    member, member_email = _invite_and_join(authed_client, "member")
    minted = member.post(
        "/api/tokens",
        json={"label": "departing teammate"},
        headers={"X-Workspace-Id": owner_ws},
    )
    assert minted.status_code == 201, minted.text
    other_id = minted.json()["data"]["id"]

    own = authed_client.get("/api/tokens").json()["data"]
    assert all(row["id"] != other_id for row in own)

    every = authed_client.get("/api/tokens?all=true")
    assert every.status_code == 200, every.text
    rows = {row["id"]: row for row in every.json()["data"]}
    assert other_id in rows
    assert rows[other_id]["user_email"] == member_email

    assert authed_client.post(f"/api/tokens/{other_id}/revoke").status_code == 200


def test_non_admin_cannot_list_all_or_revoke_someone_elses_token(authed_client):
    owner_ws = authed_client.get("/api/workspaces/current").json()["data"]["id"]
    owner_token_id = _mint(authed_client, label="owner token")["id"]

    member, _ = _invite_and_join(authed_client, "member")

    denied = member.get("/api/tokens?all=true", headers={"X-Workspace-Id": owner_ws})
    assert denied.status_code == 403, denied.text
    assert _code(denied) == "resource.insufficient_role"

    # The row exists in the member's workspace, so this is the
    # resource-first 403 (house convention), not a 404.
    r = member.post(
        f"/api/tokens/{owner_token_id}/revoke", headers={"X-Workspace-Id": owner_ws}
    )
    assert r.status_code == 403, r.text
    assert _code(r) == "resource.insufficient_role"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_mint_and_revoke_write_sanitized_audit_rows(authed_client, db):
    data = _mint(authed_client, label="audited", read_only=True)
    authed_client.post(f"/api/tokens/{data['id']}/revoke")

    created = db.execute(
        select(AuditLog).where(AuditLog.action == "api_token.created")
    ).scalar_one()
    assert created.target_type == "api_token"
    assert created.target_ids == [uuid.UUID(data["id"])]
    assert created.comment == "label=audited,read_only=True"
    assert data["token"] not in (created.comment or "")

    revoked = db.execute(
        select(AuditLog).where(AuditLog.action == "api_token.revoked")
    ).scalar_one()
    assert revoked.target_type == "api_token"
    assert revoked.target_ids == [uuid.UUID(data["id"])]


# ---------------------------------------------------------------------------
# Routes that take only CurrentUser (regression: the HIGH from code review)
#
# Workspace pinning and the membership re-check used to live exclusively in
# `get_current_workspace`. These five routes never depend on it, so a token
# sailed straight past both: it could enumerate every workspace its owner
# belonged to, create new ones, switch tenants, accept a stranger's
# invitation, and keep doing all of it after the owner's seat was deleted.
# ---------------------------------------------------------------------------


def _two_workspace_user(authed_client) -> tuple[TestClient, str, str]:
    """A user who belongs to TWO workspaces: their own, plus the host's.

    Returns (client, own_workspace_id, host_workspace_id).
    """
    host_ws = authed_client.get("/api/workspaces/current").json()["data"]["id"]
    joiner, _ = _invite_and_join(authed_client, "member")
    own_ws = next(
        w["id"] for w in joiner.get("/api/workspaces").json()["data"] if w["id"] != host_ws
    )
    return joiner, own_ws, host_ws


@pytest.mark.parametrize("read_only", [False, True])
def test_token_scoped_reads_hide_the_owners_other_workspaces(authed_client, read_only):
    joiner, own_ws, host_ws = _two_workspace_user(authed_client)

    # Sanity: the cookie session really does see both workspaces.
    assert {w["id"] for w in joiner.get("/api/workspaces").json()["data"]} == {
        own_ws,
        host_ws,
    }

    minted = joiner.post(
        "/api/tokens",
        json={"label": "pinned", "read_only": read_only},
        headers={"X-Workspace-Id": host_ws},
    )
    assert minted.status_code == 201, minted.text
    plaintext = minted.json()["data"]["token"]

    anon = TestClient(app)
    listed = anon.get("/api/workspaces", headers=_auth(plaintext))
    assert listed.status_code == 200, listed.text
    assert [w["id"] for w in listed.json()["data"]] == [host_ws]

    me = anon.get("/api/auth/me", headers=_auth(plaintext))
    assert me.status_code == 200, me.text
    assert [w["id"] for w in me.json()["data"]["workspaces"]] == [host_ws]


@pytest.mark.parametrize("read_only", [False, True])
def test_token_cannot_create_switch_or_accept_into_a_tenant(authed_client, read_only):
    """The three CurrentUser-only WRITES are refused for both token kinds.

    A read-only token is stopped one step earlier (`auth.token_read_only`,
    in deps) than a full one (`auth.token_no_token_management`, by the
    route guard) — both are 403 and neither reaches the handler.
    """
    joiner, own_ws, host_ws = _two_workspace_user(authed_client)
    plaintext = joiner.post(
        "/api/tokens",
        json={"label": "tenancy probe", "read_only": read_only},
        headers={"X-Workspace-Id": host_ws},
    ).json()["data"]["token"]
    expected = "auth.token_read_only" if read_only else "auth.token_no_token_management"

    # A real, currently-valid invitation into a THIRD workspace — so the
    # accept is refused by the guard, not by an invalid token.
    outsider, _ = _signup("outsider")
    invite = outsider.post(
        "/api/invitations", json={"email": f"x-{uuid.uuid4().hex[:8]}@x.com", "role": "admin"}
    ).json()["data"]

    anon = TestClient(app)
    probes = [
        ("post", "/api/workspaces", {"name": "smuggled org"}),
        ("post", f"/api/workspaces/{own_ws}/switch", None),
        ("post", "/api/invitations/accept", {"token": invite["token"]}),
    ]
    for method, path, body in probes:
        kwargs = {"headers": _auth(plaintext)}
        if body is not None:
            kwargs["json"] = body
        r = getattr(anon, method)(path, **kwargs)
        assert r.status_code == 403, f"{path}: {r.text}"
        assert _code(r) == expected, f"{path}: {r.text}"


def test_token_cannot_administer_credentials_or_membership(authed_client):
    """Catalog tokens and member administration are session-only too — a
    leaked token must not mint a credential that outlives its own
    revocation, nor change roles / remove seats."""
    plaintext = _mint(authed_client, label="admin probe")["token"]
    members = authed_client.get("/api/workspaces/members").json()["data"]
    member_id = members[0]["id"]

    anon = TestClient(app)
    probes = [
        ("post", "/api/workspaces/current/catalog/tokens", {"label": "smuggled"}),
        ("delete", f"/api/workspaces/current/catalog/tokens/{uuid.uuid4()}", None),
        ("patch", f"/api/workspaces/members/{member_id}", {"role": "admin"}),
        ("delete", f"/api/workspaces/members/{member_id}", None),
        ("post", "/api/invitations", {"email": "a@x.com", "role": "admin"}),
        ("delete", f"/api/invitations/{uuid.uuid4()}", None),
        # Writes the workspace's encrypted provider credentials.
        ("patch", "/api/workspaces/current", {"name": "renamed by a token"}),
    ]
    for method, path, body in probes:
        kwargs = {"headers": _auth(plaintext)}
        if body is not None:
            kwargs["json"] = body
        r = getattr(anon, method)(path, **kwargs)
        assert r.status_code == 403, f"{path}: {r.text}"
        assert _code(r) == "auth.token_no_token_management", f"{path}: {r.text}"


def test_seat_removal_401s_every_route_including_currentuser_only(authed_client, db):
    """The membership re-check must run at AUTHENTICATION, not only in
    `get_current_workspace` — otherwise these three routes keep working
    for a token whose owner was already removed."""
    data = _mint(authed_client)
    row = db.get(ApiToken, uuid.UUID(data["id"]))
    membership = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == row.user_id,
            WorkspaceMember.workspace_id == row.workspace_id,
        )
    ).scalar_one()
    db.delete(membership)
    # commit(), not flush(): the first probe below 401s, and `get_db`'s
    # error path rolls the session back — which would resurrect the seat
    # before the second probe ran.
    db.commit()

    anon = TestClient(app)
    for path in ("/api/auth/me", "/api/workspaces", "/api/parts"):
        r = anon.get(path, headers=_auth(data["token"]))
        assert r.status_code == 401, f"{path}: {r.text}"
        assert _code(r) == "auth.invalid_token", f"{path}: {r.text}"


def test_removing_a_member_revokes_their_tokens_permanently(authed_client):
    """Revocation at removal, so a later re-invite at a LOWER role can't
    reanimate a credential minted under the old one."""
    host_ws = authed_client.get("/api/workspaces/current").json()["data"]["id"]
    member, email = _invite_and_join(authed_client, "member")
    plaintext = member.post(
        "/api/tokens", json={"label": "departing"}, headers={"X-Workspace-Id": host_ws}
    ).json()["data"]["token"]

    anon = TestClient(app)
    assert anon.get("/api/parts", headers=_auth(plaintext)).status_code == 200

    members = authed_client.get("/api/workspaces/members").json()["data"]
    member_id = next(m["id"] for m in members if m["email"] == email)
    assert authed_client.delete(f"/api/workspaces/members/{member_id}").status_code == 200

    assert anon.get("/api/parts", headers=_auth(plaintext)).status_code == 401

    # Re-invite the same person, this time as a viewer. The old token must
    # stay dead — it was revoked, not merely orphaned by the missing seat.
    re_invite = authed_client.post(
        "/api/invitations", json={"email": email, "role": "viewer"}
    ).json()["data"]
    assert member.post(
        "/api/invitations/accept", json={"token": re_invite["token"]}
    ).status_code == 200

    revived = anon.get("/api/parts", headers=_auth(plaintext))
    assert revived.status_code == 401, revived.text
    assert _code(revived) == "auth.invalid_token"


def test_last_used_write_is_throttled(authed_client, db):
    """KiCad's chooser polls on a 60s cadence; one UPDATE per request would
    make every read a contended write on a single row."""
    data = _mint(authed_client)
    token_id = uuid.UUID(data["id"])

    assert authed_client.get("/api/parts", headers=_auth(data["token"])).status_code == 200
    first = db.get(ApiToken, token_id).last_used_at
    assert first is not None

    assert authed_client.get("/api/parts", headers=_auth(data["token"])).status_code == 200
    row = db.get(ApiToken, token_id)
    db.refresh(row)
    assert row.last_used_at == first, "second immediate request must not re-write"

    # Once the interval has elapsed the next request does record.
    row.last_used_at = first - timedelta(
        seconds=tokens_service.TELEMETRY_MIN_INTERVAL_SECONDS + 1
    )
    db.flush()
    stale = row.last_used_at
    assert authed_client.get("/api/parts", headers=_auth(data["token"])).status_code == 200
    db.refresh(row)
    assert row.last_used_at > stale


def test_mint_rate_limit_buckets_per_user_not_per_workspace(authed_client):
    """Two members of one workspace must not share a minting budget.

    A token is personal, so `create_token` keys its limiter on `user_key`
    rather than `workspace_key`. Both members here sit in the SAME
    workspace, so `workspace_key` would collapse them into one bucket and
    let either lock the other out.
    """
    from app.core.ratelimit import user_key, workspace_key

    host_ws = authed_client.get("/api/workspaces/current").json()["data"]["id"]
    member, _ = _invite_and_join(authed_client, "member")

    owner_id = authed_client.get("/api/auth/me").json()["data"]["user"]["id"]
    member_id = member.get("/api/auth/me").json()["data"]["user"]["id"]
    assert owner_id != member_id

    # Both mint successfully in the shared workspace.
    _mint(authed_client, label="owner mint")
    minted = member.post(
        "/api/tokens", json={"label": "member mint"}, headers={"X-Workspace-Id": host_ws}
    )
    assert minted.status_code == 201, minted.text

    class _Req:
        def __init__(self, **state):
            self.state = type("S", (), state)()

    owner_req = _Req(user_id=owner_id, workspace_id=host_ws)
    member_req = _Req(user_id=member_id, workspace_id=host_ws)

    assert user_key(owner_req) != user_key(member_req)
    # The bucket the route deliberately does NOT use would have merged them.
    assert workspace_key(owner_req) == workspace_key(member_req)


# ---------------------------------------------------------------------------
# The CSRF exemption is global; its compensating control is not.
#
# `get_current_user` refusing to fall back to the cookie is what makes the
# Origin skip safe — but that only covers routes that authenticate THROUGH
# it. `/api/auth/logout` reads `request.cookies` directly, so an
# Authorization header does not disable cookie auth there. The middleware
# therefore never applies the skip under /api/auth/.
# ---------------------------------------------------------------------------


_AUTH_ROUTES_THAT_READ_COOKIES_OR_MUTATE = [
    ("/api/auth/logout", None),
    ("/api/auth/request-password-reset", {"email": "victim@x.com"}),
    ("/api/auth/reset-password", {"token": "whatever", "password": "TestPass-2026-Stronk"}),
]


@pytest.mark.parametrize(("path", "body"), _AUTH_ROUTES_THAT_READ_COOKIES_OR_MUTATE)
def test_auth_routes_never_lose_csrf_to_an_authorization_header(path, body):
    """An attacker-origin POST carrying a junk Authorization header must
    still be blocked. Before this fix the header alone bought a CSRF
    bypass on routes whose only defence was the Origin check."""
    evil = TestClient(app, headers={"Origin": "http://evil.example"})
    signup_user(evil, email=f"victim-{uuid.uuid4().hex[:8]}@x.com")

    kwargs = {"headers": {"Authorization": "Basic YWxhZGRpbjpvcGVuc2VzYW1l"}}
    if body is not None:
        kwargs["json"] = body
    r = evil.post(path, **kwargs)

    assert r.status_code == 403, f"{path}: {r.text}"
    assert r.json()["status"]["message"] == "cross-origin request blocked"


def test_forced_logout_via_the_csrf_exemption_is_blocked():
    """The concrete repro: the session must survive the attempt."""
    evil = TestClient(app, headers={"Origin": "http://evil.example"})
    signup_user(evil, email=f"victim-{uuid.uuid4().hex[:8]}@x.com")
    assert evil.get("/api/auth/me").status_code == 200

    blocked = evil.post(
        "/api/auth/logout", headers={"Authorization": "Basic YWxhZGRpbjpvcGVuc2VzYW1l"}
    )
    assert blocked.status_code == 403, blocked.text

    # Still logged in — the session cookie was never revoked.
    assert evil.get("/api/auth/me").status_code == 200


def test_the_auth_path_carve_out_does_not_break_token_writes_elsewhere(authed_client):
    """The narrowed skip must still exempt genuine token traffic."""
    plaintext = _mint(authed_client)["token"]
    anon = _strip_origin(TestClient(app))
    assert "origin" not in anon.headers

    r = anon.post(
        "/api/parts",
        json={"name": "still works", "part_type": "local"},
        headers=_auth(plaintext),
    )
    assert r.status_code in (200, 201), r.text


@pytest.mark.parametrize("header", [" ", "  ", "\t"])
def test_whitespace_only_authorization_is_a_401_not_a_cookie_login(authed_client, header):
    """ASGI does not strip header values even though most parsers do, so a
    whitespace-only header is `truthy` — it takes the token path (401) on
    both sides rather than quietly falling back to the cookie."""
    _strip_origin(authed_client)

    r = authed_client.post(
        "/api/parts",
        json={"name": "whitespace auth", "part_type": "local"},
        headers={"Authorization": header},
    )
    assert r.status_code == 401, r.text
    assert _code(r) == "auth.invalid_token"


def test_read_only_refusal_still_stamps_telemetry(authed_client, monkeypatch):
    """Telemetry runs BEFORE the read-only 403, so probing a stolen
    read-only token with writes reaches `record_use`.

    Asserts BOTH halves: that `record_use` runs before the refusal, and
    that the write survives it. The refusal raises and `get_db` rolls the
    request transaction back, so the telemetry is committed on its own at
    auth time — without that commit the row write would vanish with the
    403 and probing would stay invisible.
    """
    minted = _mint(authed_client, read_only=True)
    plaintext, token_id = minted["token"], minted["id"]

    calls: list[str] = []
    real = deps._record_token_use

    def _spy(db, row, request):
        calls.append(request.method)
        return real(db, row, request)

    monkeypatch.setattr(deps, "_record_token_use", _spy)

    anon = TestClient(app)
    r = anon.post(
        "/api/parts",
        json={"name": "probe", "part_type": "local"},
        headers=_auth(plaintext),
    )
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_read_only"
    assert calls == ["POST"], "telemetry must run before the read-only refusal"

    # Survives the rollback that the 403 triggers. Read through a fresh
    # session so the assertion cannot be satisfied by an uncommitted
    # in-identity-map value.
    from app.infra.db import SessionLocal

    probe = SessionLocal()
    try:
        persisted = probe.get(ApiToken, uuid.UUID(token_id))
        assert persisted is not None
        assert persisted.last_used_at is not None, (
            "refused write must still leave a last_used_at trail"
        )
        assert persisted.last_used_ip == "testclient"
    finally:
        probe.close()


def test_api_token_plaintext_is_scrubbed_from_sentry_text():
    """A bare `smk_…` in an exception message carries no `token=` prefix,
    so the generic key/value scrubber would miss it entirely."""
    from app.main import _scrub_sensitive_text

    plaintext = f"smk_{uuid.uuid4().hex}.S3cr3t-Val_ue"
    scrubbed = _scrub_sensitive_text(f"upstream rejected {plaintext} while syncing")

    assert plaintext not in scrubbed
    assert "S3cr3t-Val_ue" not in scrubbed
    assert "smk_[Filtered]" in scrubbed
