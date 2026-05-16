from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FORBIDDEN_COMMENT_VALUES = (
    "plaintext-token-aud-124",
    "raw-bag-code-aud-124",
    "credential-aud-124",
)


Operation = dict[str, object]
Setup = Callable[[object, object], Operation]


def _create_part(client, name: str, part_type: str = "local") -> str:
    r = client.post("/api/parts", json={"name": name, "part_type": part_type})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _create_storage(client, name: str) -> str:
    r = client.post("/api/storage", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _create_lot(client, part_id: str) -> str:
    r = client.post(
        "/api/stock/add",
        json={
            "part_id": part_id,
            "quantity": 1,
            "lot": {"name": "Audit lot"},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["lot_id"]


def _audit_count(db) -> int:
    return db.execute(select(func.count()).select_from(AuditLog)).scalar_one()


def _latest_action(db, action: str) -> AuditLog:
    return db.execute(
        select(AuditLog)
        .where(AuditLog.action == action)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).scalars().first()


def _created_part_id(response) -> list[UUID]:
    return [UUID(response.json()["data"]["id"])]


def _target_id(context: Operation) -> list[UUID]:
    return [UUID(str(context["target_id"]))]


def _target_ids(context: Operation) -> list[UUID]:
    return [UUID(str(value)) for value in context["target_ids"]]


def _expected_target_ids(target_ids, context: Operation, response) -> list[UUID] | None:
    if target_ids is None:
        return None
    if target_ids is _created_part_id:
        return target_ids(response)
    return target_ids(context)


def _setup_part_create(_client, _db) -> Operation:
    return {
        "method": "post",
        "path": "/api/parts",
        "json": {
            "name": "AUD-124 part create",
            "part_type": "local",
            "description": "plaintext-token-aud-124",
        },
        "expected_status": 201,
        "target_ids": _created_part_id,
    }


def _setup_part_patch(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 patch part")
    return {
        "method": "patch",
        "path": f"/api/parts/{part_id}",
        "json": {"notes_markdown": "credential-aud-124"},
        "expected_status": 200,
        "target_id": part_id,
    }


def _setup_part_substitute_add(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 substitute primary")
    substitute_id = _create_part(client, "AUD-124 substitute alternate")
    return {
        "method": "post",
        "path": f"/api/parts/{part_id}/substitutes",
        "json": {"substitute_part_id": substitute_id, "direction": "one_way"},
        "expected_status": 200,
        "target_ids": [part_id, substitute_id],
    }


def _setup_part_substitute_delete(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 substitute delete primary")
    substitute_id = _create_part(client, "AUD-124 substitute delete alternate")
    r = client.post(
        f"/api/parts/{part_id}/substitutes",
        json={"substitute_part_id": substitute_id, "direction": "bidirectional"},
    )
    assert r.status_code == 200, r.text
    return {
        "method": "delete",
        "path": f"/api/parts/{part_id}/substitutes/{substitute_id}",
        "expected_status": 200,
        "target_ids": [part_id, substitute_id],
    }


def _setup_part_member_add(client, _db) -> Operation:
    meta_id = _create_part(client, "AUD-124 meta add", "meta")
    member_id = _create_part(client, "AUD-124 member add")
    return {
        "method": "post",
        "path": f"/api/parts/{meta_id}/members",
        "json": {"member_part_id": member_id},
        "expected_status": 201,
        "target_ids": [meta_id, member_id],
    }


def _setup_part_member_delete(client, _db) -> Operation:
    meta_id = _create_part(client, "AUD-124 meta delete", "meta")
    member_id = _create_part(client, "AUD-124 member delete")
    r = client.post(f"/api/parts/{meta_id}/members", json={"member_part_id": member_id})
    assert r.status_code == 201, r.text
    return {
        "method": "delete",
        "path": f"/api/parts/{meta_id}/members/{member_id}",
        "expected_status": 200,
        "target_ids": [meta_id, member_id],
    }


def _setup_tag_create(_client, _db) -> Operation:
    return {
        "method": "post",
        "path": "/api/tags",
        "json": {"name": "AUD-124 tag", "color": "#124840"},
        "expected_status": 201,
        "target_ids": _created_part_id,
    }


def _setup_tag_link(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 tag target")
    tag_id = client.post("/api/tags", json={"name": "AUD-124 link"}).json()["data"]["id"]
    return {
        "method": "post",
        "path": "/api/tags/links",
        "json": {"tag_id": tag_id, "object_type": "part", "object_id": part_id},
        "expected_status": 201,
    }


def _setup_tag_unlink(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 tag unlink target")
    tag_id = client.post("/api/tags", json={"name": "AUD-124 unlink"}).json()["data"]["id"]
    r = client.post(
        "/api/tags/links",
        json={"tag_id": tag_id, "object_type": "part", "object_id": part_id},
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["data"]["id"]
    return {
        "method": "delete",
        "path": f"/api/tags/links/{link_id}",
        "expected_status": 200,
        "target_id": link_id,
    }


def _setup_custom_field_create(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 custom field target")
    return {
        "method": "post",
        "path": "/api/custom-fields",
        "json": {
            "object_type": "part",
            "object_id": part_id,
            "key": "token_hint",
            "value": "plaintext-token-aud-124",
        },
        "expected_status": 201,
    }


def _setup_custom_field_update(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 custom field update target")
    r = client.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "note", "value": "old"},
    )
    assert r.status_code == 201, r.text
    field_id = r.json()["data"]["id"]
    return {
        "method": "post",
        "path": "/api/custom-fields",
        "json": {
            "object_type": "part",
            "object_id": part_id,
            "key": "note",
            "value": "raw-bag-code-aud-124",
        },
        "expected_status": 201,
        "target_id": field_id,
    }


def _setup_custom_field_delete(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 custom field delete target")
    r = client.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "delete_me", "value": "old"},
    )
    assert r.status_code == 201, r.text
    field_id = r.json()["data"]["id"]
    return {
        "method": "delete",
        "path": f"/api/custom-fields/{field_id}",
        "expected_status": 200,
        "target_id": field_id,
    }


def _setup_custom_field_restore(client, db) -> Operation:
    part_id = _create_part(client, "AUD-124 custom field restore target")
    part = db.get(Part, UUID(part_id))
    field = CustomField(
        workspace_id=part.workspace_id,
        object_type="part",
        object_id=part.id,
        key="provider_note",
        value="local",
        source="override",
        original_value="credential-aud-124",
        created_by=part.created_by,
        updated_by=part.updated_by,
    )
    db.add(field)
    db.flush()
    return {
        "method": "delete",
        "path": f"/api/custom-fields/{field.id}/override",
        "expected_status": 200,
        "target_id": str(field.id),
    }


def _setup_attachment_delete(client, _db) -> Operation:
    part_id = _create_part(client, "AUD-124 attachment target")
    r = client.post(
        "/api/attachments",
        files={"file": ("credential-aud-124.png", PNG_MAGIC + b"body", "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 201, r.text
    attachment_id = r.json()["data"]["id"]
    return {
        "method": "delete",
        "path": f"/api/attachments/{attachment_id}",
        "expected_status": 200,
        "target_id": attachment_id,
    }


@pytest.mark.parametrize(
    ("route_name", "setup", "method", "path", "body", "action", "target_type"),
    [
        (
            "lots.patch_lot",
            lambda client: _create_lot(client, _create_part(client, "Audit Lot Part")),
            "patch",
            "/api/lots/{target_id}",
            {"comments": "cycle counted"},
            "lot.updated",
            "lot",
        ),
        (
            "storage.patch_storage",
            lambda client: _create_storage(client, "Audit Shelf"),
            "patch",
            "/api/storage/{target_id}",
            {"description": "controlled storage"},
            "storage.updated",
            "storage_location",
        ),
    ],
)
def test_each_mutator_writes_audit_row(
    authed_client,
    db,
    route_name,
    setup,
    method,
    path,
    body,
    action,
    target_type,
):
    target_id = setup(authed_client)

    r = getattr(authed_client, method)(path.format(target_id=target_id), json=body)

    assert r.status_code == 200, f"{route_name}: {r.text}"
    row = db.execute(
        select(AuditLog)
        .where(AuditLog.action == action)
        .where(AuditLog.target_type == target_type)
        .order_by(AuditLog.created_at.desc())
    ).scalar_one()
    assert row.target_ids == [UUID(target_id)]
    assert row.comment == "fields=" + ",".join(sorted(body))


@pytest.mark.parametrize(
    ("route_name", "setup", "action", "target_type", "target_ids"),
    [
        ("parts_core.create_part", _setup_part_create, "part.created", "part", _created_part_id),
        ("parts_core.patch_part", _setup_part_patch, "part.updated", "part", _target_id),
        (
            "parts_core.add_substitute",
            _setup_part_substitute_add,
            "part.substitute_added",
            "part_substitute",
            _target_ids,
        ),
        (
            "parts_core.del_substitute",
            _setup_part_substitute_delete,
            "part.substitute_removed",
            "part_substitute",
            _target_ids,
        ),
        (
            "parts_core.add_member",
            _setup_part_member_add,
            "part.member_added",
            "part_meta_member",
            _target_ids,
        ),
        (
            "parts_core.del_member",
            _setup_part_member_delete,
            "part.member_removed",
            "part_meta_member",
            _target_ids,
        ),
        ("tags.create", _setup_tag_create, "tag.created", "tag", _created_part_id),
        ("tags.link", _setup_tag_link, "tag.linked", "tag_link", None),
        ("tags.unlink", _setup_tag_unlink, "tag.unlinked", "tag_link", _target_id),
        (
            "custom_fields.create_or_update:create",
            _setup_custom_field_create,
            "custom_field.created",
            "custom_field",
            None,
        ),
        (
            "custom_fields.create_or_update:update",
            _setup_custom_field_update,
            "custom_field.updated",
            "custom_field",
            _target_id,
        ),
        (
            "custom_fields.delete",
            _setup_custom_field_delete,
            "custom_field.deleted",
            "custom_field",
            _target_id,
        ),
        (
            "custom_fields.restore_override",
            _setup_custom_field_restore,
            "custom_field.override_restored",
            "custom_field",
            _target_id,
        ),
        (
            "attachments.delete",
            _setup_attachment_delete,
            "attachment.deleted",
            "attachment",
            _target_id,
        ),
    ],
)
def test_aud_124_mutators_write_exactly_one_sanitized_audit_row(
    authed_client,
    db,
    route_name: str,
    setup: Setup,
    action: str,
    target_type: str,
    target_ids: Callable[[Operation], list[UUID]] | Callable[[object], list[UUID]] | None,
):
    context = setup(authed_client, db)
    before = _audit_count(db)

    request_kwargs = {}
    if "json" in context:
        request_kwargs["json"] = context["json"]
    if "files" in context:
        request_kwargs["files"] = context["files"]
    if "data" in context:
        request_kwargs["data"] = context["data"]
    response = getattr(authed_client, str(context["method"]))(
        str(context["path"]),
        **request_kwargs,
    )

    assert response.status_code == context["expected_status"], f"{route_name}: {response.text}"
    assert _audit_count(db) == before + 1

    row = _latest_action(db, action)
    assert row is not None
    assert row.target_type == target_type
    expected_target_ids = _expected_target_ids(target_ids, context, response)
    if expected_target_ids is not None:
        assert row.target_ids == expected_target_ids
    assert row.workspace_id is not None
    assert row.user_id is not None
    assert row.comment is None or all(
        forbidden not in row.comment
        for forbidden in FORBIDDEN_COMMENT_VALUES
    )
