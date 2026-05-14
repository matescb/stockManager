from __future__ import annotations

PROVIDER_RESERVED_CUSTOM_FIELD_KEYS: tuple[str, ...] = (
    "image_url",
    "datasheet_url",
    "source_url",
)

PROVIDER_ASSET_CUSTOM_FIELD_KINDS: dict[str, str] = {
    "image_url": "image",
    "datasheet_url": "datasheet",
}


def is_provider_reserved_custom_field_key(key: str) -> bool:
    return key in PROVIDER_RESERVED_CUSTOM_FIELD_KEYS
