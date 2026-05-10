"""Pydantic input schemas for the workspaces domain (#252).

Lifted out of `app/api/routes/workspaces.py` and
`app/api/routes/invitations.py` so every domain has one canonical
`domain/<x>/schemas.py`.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
`tests/test_extra_forbid.py` regression-tests this and a silent drop
would let unknown fields through.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

__all__ = [
    "WorkspaceCreateIn",
    "WorkspacePatch",
    "CatalogTokenIn",
    "MemberPatch",
    "InviteIn",
    "AcceptIn",
]

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
SourcingLanguageCode = Literal[
    "de",
    "en",
    "es",
    "fr",
    "it",
    "pt",
    "ja",
    "zh-hans",
    "zh-hant",
]
DistributorName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]


class WorkspaceCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    currency_default: str = "USD"


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency_default: str | None = Field(default=None, min_length=3, max_length=3)
    lot_control_enabled: bool | None = None
    serial_tracking_enabled: bool | None = None
    catalog_enabled: bool | None = None
    # Write-only command flag: when true (and the catalog stays enabled), the
    # route mints a fresh secrets.token_urlsafe(32) and stores it.
    regenerate_catalog_token: bool | None = None
    parts_provider: Literal["none", "mouser", "digikey"] | None = None
    # Empty string clears the stored key; any other non-None value replaces it.
    # None (omitted) leaves whatever's already stored alone.
    parts_provider_api_key: str | None = None
    # Same semantics as parts_provider_api_key. Used as DigiKey's
    # client_secret; Mouser doesn't need it.
    parts_provider_api_secret: str | None = None
    scanner: Literal["zxing", "scandit"] | None = None
    # Same '' clears / non-empty replaces / None leaves alone semantics.
    scanner_license_key: str | None = None
    sourcing_provider: Literal["none", "trustedparts"] | None = None
    sourcing_company_id: str | None = Field(default=None, max_length=256)
    sourcing_api_key: str | None = Field(default=None, max_length=256)
    sourcing_country_code: str | None = Field(default=None, min_length=2, max_length=2)
    sourcing_currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    sourcing_language_code: SourcingLanguageCode | None = None
    sourcing_preferred_distributors: list[str] | None = None
    active_currencies: list[CurrencyCode] | None = Field(default=None, min_length=1)
    active_countries: list[CountryCode] | None = Field(default=None, min_length=1)
    active_distributors: list[DistributorName] | None = Field(default=None, min_length=1)
    sourcing_use_cached_for_dashboards: bool | None = None


class CatalogTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "admin", "member", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # SEC2-013: the token field now carries a composite value of the form
    # "{invitation_id}:{plaintext_token}", produced by _serialize().  The
    # accept handler splits on the first ":" to obtain the PK (for the
    # DB lookup) and the plaintext (for HMAC comparison).  This keeps the
    # frontend interface to a single opaque string while allowing a
    # constant-time comparison path.
    token: str
