"""Which custom-field keys a provider owns, and whose namespace they sit in.

Two provider tiers write `custom_fields(source='provider')` rows on the
same part:

* the PRIMARY provider (`workspaces.parts_provider`) writes un-namespaced
  keys — `Resistance`, `image_url`, `source_url` — exactly as it always
  has;
* every SECONDARY provider writes keys prefixed `"{provider}:"` —
  `mouser:source_url`, `mouser:Resistance`.

Each refresh reconciles (inserts, updates, and *deletes*) the rows in its
own namespace and must be blind to every other namespace. The predicates
below are that boundary — `provider_owns_custom_field_key` is the single
place the rule is written down.
"""
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

# Every provider name `make_provider` can build. A key prefixed with one
# of these belongs to that provider's namespace and is invisible to the
# primary reconciliation; anything else is the primary's.
#
# Keep in sync with `providers/base.py::make_provider` and the
# `parts_provider` Literal in `domain/workspaces/schemas.py`.
KNOWN_PROVIDER_NAMES: tuple[str, ...] = ("digikey", "mouser")

_NAMESPACE_SEPARATOR = ":"

# Width of `custom_fields.key` (see `domain/custom_fields/models.py`).
# Namespacing adds `len(provider) + 1` characters to an upstream field
# name we do not control, so a secondary refresh has to check the result
# fits before handing it to the DB — otherwise a long enough
# ProductAttributes name is an uncaught DataError, i.e. a 500.
CUSTOM_FIELD_KEY_MAX = 256


def is_provider_reserved_custom_field_key(key: str) -> bool:
    return key in PROVIDER_RESERVED_CUSTOM_FIELD_KEYS


def namespaced_custom_field_key(provider: str, key: str) -> str:
    """`("mouser", "Resistance") -> "mouser:Resistance"`."""
    return f"{provider}{_NAMESPACE_SEPARATOR}{key}"


def is_provider_namespaced_key(key: str) -> bool:
    """True when *key* sits in some known provider's namespace.

    Only the names in `KNOWN_PROVIDER_NAMES` count. An upstream spec
    genuinely called `Vref:max` stays the primary's — the prefix has to
    name a provider we can actually build.
    """
    return any(
        key.startswith(f"{name}{_NAMESPACE_SEPARATOR}") for name in KNOWN_PROVIDER_NAMES
    )


def provider_owns_custom_field_key(provider: str, key: str, *, is_primary: bool) -> bool:
    """Is *key* inside the namespace this refresh is allowed to reconcile?

    The primary owns every key that is NOT namespaced; a secondary owns
    exactly its own prefix. The two sets are disjoint by construction,
    which is what stops a DigiKey refresh from deleting the `mouser:`
    rows (and vice versa).
    """
    if is_primary:
        return not is_provider_namespaced_key(key)
    return key.startswith(f"{provider}{_NAMESPACE_SEPARATOR}")
