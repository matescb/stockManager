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

from app.core.time import utcnow
from app.domain.audit.models import AuditLog
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
