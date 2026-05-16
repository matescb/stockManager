from __future__ import annotations

from sqlalchemy import func, select

from app.domain.audit.models import AuditLog


def _audit_count(db) -> int:
    return db.execute(select(func.count()).select_from(AuditLog)).scalar_one()


def _create_part(client, name: str, part_type: str = "local") -> str:
    r = client.post("/api/parts", json={"name": name, "part_type": part_type})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _latest_comment(db, action: str) -> str | None:
    row = db.execute(
        select(AuditLog)
        .where(AuditLog.action == action)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).scalar_one()
    return row.comment


def test_create_and_patch_part_write_one_sanitized_audit_row(authed_client, db):
    before = _audit_count(db)
    r = authed_client.post(
        "/api/parts",
        json={
            "name": "AUD-124 core",
            "part_type": "local",
            "description": "plaintext-token-aud-124",
        },
    )
    assert r.status_code == 201, r.text
    assert _audit_count(db) == before + 1
    assert "plaintext-token-aud-124" not in (_latest_comment(db, "part.created") or "")

    part_id = r.json()["data"]["id"]
    before = _audit_count(db)
    r = authed_client.patch(
        f"/api/parts/{part_id}",
        json={"notes_markdown": "credential-aud-124"},
    )
    assert r.status_code == 200, r.text
    assert _audit_count(db) == before + 1
    assert "credential-aud-124" not in (_latest_comment(db, "part.updated") or "")


def test_substitute_and_member_mutations_write_one_audit_row(authed_client, db):
    part_id = _create_part(authed_client, "AUD-124 substitute primary")
    substitute_id = _create_part(authed_client, "AUD-124 substitute alternate")

    before = _audit_count(db)
    r = authed_client.post(
        f"/api/parts/{part_id}/substitutes",
        json={"substitute_part_id": substitute_id},
    )
    assert r.status_code == 200, r.text
    assert _audit_count(db) == before + 1

    before = _audit_count(db)
    r = authed_client.delete(f"/api/parts/{part_id}/substitutes/{substitute_id}")
    assert r.status_code == 200, r.text
    assert _audit_count(db) == before + 1

    meta_id = _create_part(authed_client, "AUD-124 meta", "meta")
    member_id = _create_part(authed_client, "AUD-124 member")

    before = _audit_count(db)
    r = authed_client.post(f"/api/parts/{meta_id}/members", json={"member_part_id": member_id})
    assert r.status_code == 201, r.text
    assert _audit_count(db) == before + 1

    before = _audit_count(db)
    r = authed_client.delete(f"/api/parts/{meta_id}/members/{member_id}")
    assert r.status_code == 200, r.text
    assert _audit_count(db) == before + 1
