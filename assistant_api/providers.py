"""Compatibility exports for the retired prototype's provider import path.

Provider behavior lives in :mod:`assistant_api.provider`; keep this thin facade
so old, unintegrated modules do not maintain a second Ollama implementation.
"""

from .provider import LLMProvider, OllamaProvider, ProviderError


class CloudProvider:
    """Cloud access is intentionally unavailable in this local-first app."""

    async def complete(self, messages: list[dict]) -> str:
        raise ProviderError("cloud_provider_not_configured")


class FallbackProvider:
    """Do not convert provider failures into success-shaped fallback answers."""

    async def complete(self, messages: list[dict]) -> str:
        raise ProviderError("fallback_provider_disabled")


__all__ = [
    "CloudProvider",
    "FallbackProvider",
    "LLMProvider",
    "OllamaProvider",
    "ProviderError",
]
