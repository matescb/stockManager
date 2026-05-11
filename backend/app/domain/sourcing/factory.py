"""Provider factory for workspace-scoped sourcing."""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.secrets import decrypt
from app.core.version import git_sha
from app.domain.sourcing.client import TrustedPartsClient


def _workspace_user_agent_fragment(workspace_id: Any) -> str:
    digest = hashlib.sha256(str(workspace_id).encode("utf-8")).hexdigest()[:12]
    return f"workspace_sha={digest}"


def make_sourcing_provider(workspace: Any) -> TrustedPartsClient | None:
    """Return a TrustedParts client for a configured workspace."""
    if workspace.sourcing_provider != "trustedparts" or not workspace.sourcing_api_key_enc:
        return None

    api_key = decrypt(workspace.sourcing_api_key_enc)
    if not api_key:
        return None

    return TrustedPartsClient(
        company_id="",
        api_key=api_key,
        country_code=workspace.sourcing_country_code,
        currency_code=workspace.sourcing_currency_code,
        language_code=workspace.sourcing_language_code,
        user_agent=f"stockManager/{git_sha()} {_workspace_user_agent_fragment(workspace.id)}",
    )
