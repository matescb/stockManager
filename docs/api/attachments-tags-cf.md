# Attachments, Tags & Custom Fields API

Audience: engineer

The three polymorphic surfaces — file attachments, tag links, and key/value custom fields — that hang off registered first-class entities (`part`, `project`, `order`, `build`, `lot`, `storage_location`). Each shares an `(object_type, object_id)` pair pinned by `assert_polymorphic_in_workspace` to the caller's workspace.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Three routers:

| Router | Mount |
|---|---|
| `attachments` | `/api/attachments` (`backend/app/main.py:383`) |
| `custom_fields` | `/api/custom-fields` (`backend/app/main.py:384`) |
| `tags` | `/api/tags` (`backend/app/main.py:385`) |

## Attachments (`/api/attachments`)

Upload allow-list (CLAUDE.md "Hard invariants" — content-addressed assets are a separate feature). Both the declared MIME and the magic bytes must agree (`attachments.py:36-64`):

| MIME | Stored extension |
|---|---|
| `image/png` | `png` |
| `image/jpeg` | `jpg` |
| `image/webp` | `webp` |
| `application/pdf` | `pdf` |

SVG is intentionally absent (`attachments.py:30-35`).

### `POST /api/attachments`

Multipart upload. Validates polymorphic target, magic bytes, declared MIME, size cap; sanitises filename; writes to `{UPLOAD_DIR}/{ws_id}/{uuid}-{safe_name}`.

**Request** — multipart/form-data

| Field | Type | Required | Notes |
|---|---|---|---|
| `object_type` | string | yes | Polymorphic target type. |
| `object_id` | UUID | yes | Must belong to the current workspace. |
| `file_type` | string | no | Caller-supplied tag (e.g. `"image"`, `"datasheet"`). Defaults to `"other"`. |
| `file` | UploadFile | yes | |

**Response** — `201 Created`

```json
{ "data": { "id": "…", "object_type": "…", "object_id": "…",
            "file_name": "…", "file_type": "…", "mime_type": "…",
            "size_bytes": 12345, "created_at": "…" }, "status": { … } }
```

**Errors**

- `404` — polymorphic target missing or wrong workspace (via `assert_polymorphic_in_workspace`) (`attachments.py:116`).
- `413 attachment.too_large` — exceeds `MAX_UPLOAD_BYTES` (`attachments.py:122-129`).
- `400 attachment.empty` — zero bytes (`attachments.py:130-131`).
- `415 attachment.unsupported_type` — magic bytes don't match the allow-list (`attachments.py:138-143`).
- `415 attachment.content_type_mismatch` — declared `Content-Type` differs from the sniffed MIME (`attachments.py:144-152`).

**Notes**

- Storage key: `{ws.id}/{uuid}-{safe_name}` (`attachments.py:155-158`).
- The stored `file_name` is server-sanitised; the client's filename is never echoed back (`attachments.py:154`, `_safe_filename` at `:70-87`).
- Source: `backend/app/api/routes/attachments.py:103-177`.

### `GET /api/attachments/by-object/{object_type}/{object_id}`

List attachments for a target.

**Response** — `200 OK` — array of attachment metadata, sorted `created_at DESC`.

**Notes**

- Source: `backend/app/api/routes/attachments.py:180-191`.

### `GET /api/attachments/{attachment_id}/download`

Stream the bytes. Always served `Content-Disposition: attachment` regardless of MIME, so even allow-listed images don't render inline (`attachments.py:208-217`).

**Errors** — `404 attachment.not_found` via `assert_in_workspace`.

**Notes**

- Legacy attachments uploaded before the allow-list landed are served as `application/octet-stream` (`attachments.py:198-207`).
- Source: `backend/app/api/routes/attachments.py:194-217`.

### `DELETE /api/attachments/{attachment_id}`

Delete the row and best-effort `os.remove` the file (`FileNotFoundError` is swallowed).

**Notes**

- Source: `backend/app/api/routes/attachments.py:220-229`.

## Tags (`/api/tags`)

### `GET /api/tags`

List workspace tags.

**Query** — `limit` (default 200, max 1000).

**Response** — `200 OK` — `[ { id, name, color } ]`. Sorted by `name`.

**Notes**

- Source: `backend/app/api/routes/tags.py:17-31`.

### `POST /api/tags`

Create a tag.

**Request** — `TagIn`: `name`, `color`.

**Response** — `201 Created` — `{ id, name, color }`.

**Notes**

- Source: `backend/app/api/routes/tags.py:34-39`.

### `POST /api/tags/links`

Bind a tag to an object. Idempotent — re-binding the same `(tag_id, object_type, object_id)` returns the existing link (`tags.py:49-61`).

**Request** — `TagLinkIn`: `tag_id`, `object_type`, `object_id`.

**Response** — `201 Created` — `{ id }`.

**Errors**

- `404 tag.not_found` — wrong workspace (via `assert_in_workspace`) (`tags.py:44`).
- `404` — polymorphic target wrong / missing (via `assert_polymorphic_in_workspace`) (`tags.py:48`).

**Notes**

- Source: `backend/app/api/routes/tags.py:42-72`.

### `DELETE /api/tags/links/{link_id}`

Unlink. No-op if the row doesn't exist or belongs to another workspace (still returns `200 "deleted"`) (`tags.py:75-84`).

**Notes**

- Source: `backend/app/api/routes/tags.py:75-84`.

### `GET /api/tags/by-object/{object_type}/{object_id}`

List the tag links for an object, joined with the tag row.

**Response** — `200 OK`

```json
{ "data": [ { "id": "<link_id>", "tag": { "id": "…", "name": "…", "color": "…" } } ], "status": { … } }
```

**Notes**

- Source: `backend/app/api/routes/tags.py:87-98`.

## Custom fields (`/api/custom-fields`)

### `GET /api/custom-fields/by-object/{object_type}/{object_id}`

List custom fields for an object, sorted by `key`.

**Response** — `200 OK`

```json
{ "data": [ { "id": "…", "key": "…", "value": "…",
              "source": "manual" | "provider" | "override",
              "original_value": "…" | null } ], "status": { … } }
```

**Notes**

- Source: `backend/app/api/routes/custom_fields.py:28-39`.

### `POST /api/custom-fields`

Upsert a (`object_type`, `object_id`, `key`) row. The handler manages the `source` transitions — see CLAUDE.md "Provider catalog vs spec keys" and the in-source comment at `custom_fields.py:44-58`:

| Existing source | New value | Result |
|---|---|---|
| (none) | any | insert as `source="manual"` (`:99-110`). |
| `manual` | any | update value (`:93-95`). |
| `provider` | matches | update value (no source change). |
| `provider` | differs | move existing into `original_value`, set `source="override"`, store new value (`:78-83`). |
| `override` | matches `original_value` | revert: `source="provider"`, clear `original_value` (`:85-90`). |
| `override` | other | update value (`:91-92`). |

`source` is server-controlled; any caller-supplied value is ignored (the handler always writes `manual` on insert) (`:54-58`).

**Request** — `CustomFieldIn`: `object_type`, `object_id`, `key`, `value`.

**Response** — `200 OK` (existing) or `201 Created` (new) — serialised row.

**Errors** — `404` — polymorphic target wrong / missing (via `assert_polymorphic_in_workspace`) (`custom_fields.py:63`).

**Notes**

- Source: `backend/app/api/routes/custom_fields.py:42-111`.

### `DELETE /api/custom-fields/{cf_id}`

Delete a row. No-op if missing or wrong workspace.

**Notes**

- Source: `backend/app/api/routes/custom_fields.py:114-123`.

### `DELETE /api/custom-fields/{cf_id}/override`

Restore an override back to its provider value.

**Errors**

- `404` — wrong workspace (via `assert_in_workspace`).
- `400 custom_field.not_override` — `source != "override"` (`custom_fields.py:131-136`).

**Notes**

- Source: `backend/app/api/routes/custom_fields.py:126-141`.
