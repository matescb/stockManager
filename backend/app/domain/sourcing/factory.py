"""Provider factory for workspace-scoped sourcing."""

from __future__ import annotations

from typing import Any

from app.core.secrets import decrypt
from app.core.version import git_sha
from app.domain.sourcing.client import TrustedPartsClient


def make_sourcing_provider(workspace: Any) -> TrustedPartsClient | None:
    """Return a TrustedParts client for a configured workspace."""
    if workspace.sourcing_provider != "trustedparts" or not workspace.sourcing_api_key_enc:
        return None

    company_id = (
        decrypt(workspace.sourcing_company_id_enc) if workspace.sourcing_company_id_enc else None
    )
    api_key = decrypt(workspace.sourcing_api_key_enc)
    if not api_key:
        return None

    return TrustedPartsClient(
        company_id=company_id or "",
        api_key=api_key,
        country_code=workspace.sourcing_country_code,
        currency_code=workspace.sourcing_currency_code,
        user_agent=f"stockManager/{git_sha()} workspace={workspace.id}",
    )
