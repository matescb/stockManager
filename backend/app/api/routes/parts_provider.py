"""Provider-agnostic MPN lookup. Reads the workspace's configured
provider + API key and dispatches."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import CurrentWorkspace
from app.core.responses import ok
from app.domain.parts.providers import make_provider

router = APIRouter()


class LookupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str = Field(min_length=1, max_length=200)


@router.post("/lookup-mpn")
def lookup_mpn(payload: LookupIn, ws: CurrentWorkspace):
    provider = make_provider(
        ws.parts_provider,
        ws.parts_provider_api_key,
        ws.parts_provider_api_secret,
    )
    if provider is None:
        return ok({
            "found": False,
            "result": None,
            "message": "no provider configured (set one in Workspace settings)",
            "provider": ws.parts_provider or "none",
        })
    out = provider.lookup_mpn(payload.mpn.strip())
    # Tag the response with the provider name so the UI can label its
    # success/failure note.
    return ok({**out, "provider": provider.name})
