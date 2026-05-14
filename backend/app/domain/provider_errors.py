"""Shared exception taxonomy for external provider integrations."""
from __future__ import annotations


class ProviderError(Exception):
    """Base error raised by external provider clients."""

    default_status_code = 502

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.message = message
        self.status_code = status_code if status_code is not None else self.default_status_code


class ProviderAuthError(ProviderError):
    """Provider rejected configured credentials."""

    default_status_code = 401


class ProviderRateLimitError(ProviderError):
    """Provider rate-limited the request."""

    default_status_code = 429


class ProviderUpstreamError(ProviderError):
    """Provider transport/server failure."""

    default_status_code = 502


class ProviderTimeoutError(ProviderUpstreamError):
    """Provider request timed out."""

    default_status_code = 504


class ProviderValidationError(ProviderError):
    """Provider returned an unparsable or invalid response."""

    default_status_code = 502
