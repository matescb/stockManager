"""Pluggable parts data providers.

A provider takes an MPN and returns a small canonical record of the
part's identifying fields. Providers are configured per workspace
(`workspaces.parts_provider`, `workspaces.parts_provider_api_key`).
"""
from __future__ import annotations

from typing import Optional, Protocol, TypedDict


class MpnLookupResult(TypedDict, total=False):
    """The shape returned by every provider's `lookup_mpn`."""
    found: bool
    result: Optional[dict]  # see schema below
    message: Optional[str]


# Canonical record shape (the `result` payload):
#   {
#     "mpn":            str,
#     "manufacturer":   str | None,
#     "description":    str | None,
#     "category":       str | None,
#     "footprint":      str | None,
#     "datasheet_url":  str | None,
#     "image_url":      str | None,
#     "source_url":     str,
#     "specs":          [{ "key": str, "value": str }, ...],
#   }
#
# `specs` is an ordered list of free-form key/value pairs; the names
# come straight from the upstream provider (e.g. Mouser's
# ProductAttributes) and vary by part type. The frontend persists
# each row as a custom_fields entry on the new part.


class PartsProvider(Protocol):
    name: str

    def lookup_mpn(self, mpn: str) -> MpnLookupResult: ...


def make_provider(name: str | None, api_key: str | None) -> PartsProvider | None:
    """Factory: returns a configured provider instance, or None when the
    workspace hasn't selected one (or has but didn't supply an API key)."""
    if not name or name == "none":
        return None
    if not api_key:
        return None
    if name == "mouser":
        from app.domain.parts.providers.mouser import MouserProvider
        return MouserProvider(api_key=api_key)
    return None
