from __future__ import annotations

from app.domain.parts.providers.base import ProviderUpstreamError as PartsProviderUpstreamError
from app.domain.provider_errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUpstreamError,
    ProviderValidationError,
)
from app.domain.sourcing.client import (
    SourcingAuthError,
    SourcingClientError,
    SourcingRateLimitError,
    SourcingTimeoutError,
    SourcingUpstreamError,
    SourcingValidationError,
)


def test_all_providers_use_same_base():
    assert issubclass(PartsProviderUpstreamError, ProviderUpstreamError)
    assert issubclass(SourcingClientError, ProviderError)
    assert issubclass(SourcingAuthError, ProviderAuthError)
    assert issubclass(SourcingRateLimitError, ProviderRateLimitError)
    assert issubclass(SourcingTimeoutError, ProviderTimeoutError)
    assert issubclass(SourcingUpstreamError, ProviderUpstreamError)
    assert issubclass(SourcingValidationError, ProviderValidationError)

    provider_errors = [
        PartsProviderUpstreamError("mouser", "mouser unavailable"),
        PartsProviderUpstreamError("digikey", "digikey unavailable"),
        SourcingClientError("trustedparts failed"),
        SourcingAuthError("trustedparts rejected credentials"),
        SourcingRateLimitError("trustedparts rate limit reached"),
        SourcingTimeoutError("trustedparts timed out"),
        SourcingUpstreamError("trustedparts returned 500"),
        SourcingValidationError("trustedparts returned invalid data"),
    ]

    assert all(isinstance(error, ProviderError) for error in provider_errors)
    assert [error.provider for error in provider_errors[:3]] == [
        "mouser",
        "digikey",
        "trustedparts",
    ]
