"""Pluggable parts data providers.

A provider takes an MPN and returns a small canonical record of the
part's identifying fields. Providers are configured per workspace
(`workspaces.parts_provider`, `workspaces.parts_provider_api_key`).
"""
from __future__ import annotations

from typing import Literal, Optional, Protocol, TypedDict

from app.domain.provider_errors import ProviderError, ProviderUpstreamError

__all__ = [
    "MpnLookupResult",
    "PartsProvider",
    "ProviderError",
    "ProviderUpstreamError",
    "make_provider",
    "match_kind",
]


class MpnLookupResult(TypedDict, total=False):
    """The shape returned by every provider's `lookup_mpn`."""
    found: bool
    result: Optional[dict]  # see schema below
    candidates: list[dict]
    message: Optional[str]
    # How the record in `result` relates to the MPN that was asked for.
    # "exact": the provider's manufacturer part number equals the query
    # (case and surrounding whitespace ignored). "fuzzy": the provider
    # returned a DIFFERENT part number — DigiKey's keyword fallback, or a
    # Mouser partial match — and `result["mpn"]` says which. `found`
    # keeps its old meaning ("a record came back"); a caller that needs
    # the asked-for part reads this, not `found`. Set only when found.
    match: Literal["exact", "fuzzy"]
    # True when the provider refused because it has suspended this
    # application (DigiKey: HTTP 403 after a burst). Not a miss, and not
    # the day's quota: `provider_cache` opens the breaker on it.
    suspended: bool


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


def match_kind(query: str, found_mpn: str | None) -> Literal["exact", "fuzzy"]:
    """"exact" when *found_mpn* is the part number that was asked for.

    Compared case-insensitively after stripping, and nothing looser: a
    reformatted MPN (`98266-0897` vs `0982660897`) is a different string
    and therefore `fuzzy`, which is the point — the caller has to look.
    """
    a = (query or "").strip().casefold()
    b = (found_mpn or "").strip().casefold()
    return "exact" if a and a == b else "fuzzy"


class PartsProvider(Protocol):
    name: str

    def lookup_mpn(self, mpn: str) -> MpnLookupResult: ...


def make_provider(
    name: str | None,
    api_key: str | None,
    api_secret: str | None = None,
) -> PartsProvider | None:
    """Factory: returns a configured provider instance, or None when the
    workspace hasn't selected one (or hasn't supplied the credentials it
    needs). DigiKey requires both api_key (client_id) and api_secret
    (client_secret); Mouser only the api_key."""
    if not name or name == "none":
        return None
    if not api_key:
        return None
    if name == "mouser":
        from app.domain.parts.providers.mouser import MouserProvider
        return MouserProvider(api_key=api_key)
    if name == "digikey":
        if not api_secret:
            return None
        from app.domain.parts.providers.digikey import DigiKeyProvider
        return DigiKeyProvider(client_id=api_key, client_secret=api_secret)
    return None
