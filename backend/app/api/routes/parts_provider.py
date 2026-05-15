"""Provider-agnostic MPN lookup. Reads the workspace's configured
provider + API key and dispatches."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.deps import CurrentWorkspace
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.secrets import decrypt
from app.domain.parts.providers import make_provider
from app.domain.parts.providers.base import ProviderUpstreamError
from app.domain.parts.schemas import LookupIn
from app.domain.parts.services.provider_cache import lookup_with_cache

router = APIRouter()


@router.post("/lookup-mpn")
@limiter.limit("60/minute", key_func=workspace_key)
def lookup_mpn(request: Request, payload: LookupIn, ws: CurrentWorkspace):
    # Decrypt credentials at the boundary (Sec HIGH-9). Columns store
    # Fernet ciphertext post-0016; provider classes get the plaintext.
    provider = make_provider(
        ws.parts_provider,
        decrypt(ws.parts_provider_api_key),
        decrypt(ws.parts_provider_api_secret),
    )
    if provider is None:
        return ok({
            "found": False,
            "result": None,
            "message": "no provider configured (set one in Workspace settings)",
            "provider": ws.parts_provider or "none",
        })
    try:
        out = lookup_with_cache(provider, payload.mpn.strip())
    except ProviderUpstreamError as exc:
        raise_http(
            exc.status_code,
            ErrorCodes.PROVIDER_UPSTREAM_ERROR,
            exc.message,
            provider=exc.provider,
        )
    # Tag the response with the provider name so the UI can label its
    # success/failure note.
    return ok({**out, "provider": provider.name})
