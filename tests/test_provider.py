import asyncio
import json

import httpx
import pytest

from assistant_api.provider import LLMProvider, OllamaProvider, ProviderError
from assistant_api.providers import (
    CloudProvider,
    FallbackProvider,
    OllamaProvider as CompatibilityOllamaProvider,
)


def test_legacy_provider_module_reexports_one_ollama_implementation():
    assert CompatibilityOllamaProvider is OllamaProvider


def test_unified_ollama_provider_supports_request_and_completion():
    calls = []

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, body))
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "Local response"}})
        return httpx.Response(200, json={"models": []})

    async def run():
        async with httpx.AsyncClient(
            base_url="http://ollama", transport=httpx.MockTransport(handler),
        ) as client:
            provider = OllamaProvider(client, model="llama3.2:latest")
            assert isinstance(provider, LLMProvider)
            assert await provider.request("GET", "/api/tags") == {"models": []}
            assert await provider.complete([{"role": "user", "content": "Hi"}]) == "Local response"
            assert await OllamaProvider(
                "http://ollama", "legacy-model", transport=httpx.MockTransport(handler),
            ).complete([
                {"role": "user", "content": "Legacy call"},
            ]) == "Local response"

    asyncio.run(run())
    assert [path for path, _ in calls] == ["/api/tags", "/api/chat", "/api/chat"]
    assert calls[1][1]["model"] == "llama3.2:latest"
    assert calls[2][1]["model"] == "legacy-model"


@pytest.mark.parametrize("provider", [CloudProvider(), FallbackProvider()])
def test_unconfigured_provider_implementations_fail_explicitly(provider):
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete([]))
