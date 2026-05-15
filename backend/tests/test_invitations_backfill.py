from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.time import utcnow
from app.domain.audit.models import AuditLog
from app.domain.workspaces.models import WorkspaceInvitation


def _utc(value):
    return value.astimezone(timezone.utc)


def _load_backfill_migration():
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "0058_stale_invitation_expiry_backfill.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0058", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_invitation(authed_client, email: str) -> uuid.UUID:
    response = authed_client.post(
        "/api/invitations",
        json={"email": email, "role": "member"},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["data"]["id"])


def test_stale_invites_expire_at_original_window(authed_client, db):
    now = utcnow()
    stale_created = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    stale_original_expiry = stale_created + timedelta(days=14)
    reactivated_expiry = now + timedelta(days=14)
    recent_created = now - timedelta(days=2)
    recent_expiry = now + timedelta(days=12)

    stale_one_id = _create_invitation(
        authed_client,
        f"stale-one-{uuid.uuid4().hex[:8]}@example.com",
    )
    stale_two_id = _create_invitation(
        authed_client,
        f"stale-two-{uuid.uuid4().hex[:8]}@example.com",
    )
    recent_id = _create_invitation(
        authed_client,
        f"recent-{uuid.uuid4().hex[:8]}@example.com",
    )

    stale_one = db.get(WorkspaceInvitation, stale_one_id)
    stale_two = db.get(WorkspaceInvitation, stale_two_id)
    recent = db.get(WorkspaceInvitation, recent_id)
    assert stale_one is not None
    assert stale_two is not None
    assert recent is not None

    stale_one.created_at = stale_created
    stale_one.expires_at = reactivated_expiry
    stale_two.created_at = stale_created
    stale_two.expires_at = reactivated_expiry
    recent.created_at = recent_created
    recent.expires_at = recent_expiry
    db.flush()

    migration = _load_backfill_migration()
    migration.expire_stale_invitations(db.connection())
    db.expire_all()

    stale_one = db.get(WorkspaceInvitation, stale_one_id)
    stale_two = db.get(WorkspaceInvitation, stale_two_id)
    recent = db.get(WorkspaceInvitation, recent_id)
    assert stale_one is not None
    assert stale_two is not None
    assert recent is not None
    assert _utc(stale_one.expires_at) == stale_original_expiry
    assert _utc(stale_two.expires_at) == stale_original_expiry
    assert _utc(recent.expires_at) == recent_expiry

    audit_row = db.execute(
        select(AuditLog).where(
            AuditLog.action == "invitation.stale_expiration_backfilled"
        )
    ).scalar_one()
    assert audit_row.workspace_id == stale_one.workspace_id
    assert set(audit_row.target_ids or []) == {stale_one_id, stale_two_id}
    assert "AUD-078 expired 2 stale pending workspace invitation" in (
        audit_row.comment or ""
    )
