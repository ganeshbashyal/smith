from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, messages: list[dict]) -> str: ...


class ProviderError(RuntimeError):
    """Safe provider failure carrying the operation, never response contents."""

    def __init__(self, operation: str):
        self.operation = operation
        super().__init__(f"Local model provider operation failed: {operation}")


class ModelProvider(Protocol):
    async def request(self, method: str, path: str, **kwargs) -> dict: ...


class OllamaProvider:
    """One Ollama adapter for model management and legacy chat consumers."""

    def __init__(self, client: httpx.AsyncClient | str, model: str | None = None,
                 timeout: float = 30, *, transport: httpx.AsyncBaseTransport | None = None):
        self.client = client if isinstance(client, httpx.AsyncClient) else None
        self.base_url = client.rstrip("/") if isinstance(client, str) else None
        self.model = model
        self.timeout = timeout
        self.transport = transport

    async def request(self, method: str, path: str, **kwargs) -> dict:
        operation = path.rsplit("/", 1)[-1]
        try:
            if self.client is not None:
                response = await self.client.request(method, path, **kwargs)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object")
                return payload
            if not self.base_url:
                raise ValueError("Provider client is not configured")
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, transport=self.transport,
            ) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object")
                return payload
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ProviderError(operation) from exc

    async def complete(self, messages: list[dict]) -> str:
        if not self.model:
            raise ProviderError("model_not_configured")
        payload = await self.request("POST", "/api/chat", json={
            "model": self.model, "messages": messages, "stream": False,
        })
        try:
            answer = payload["message"]["content"]
            if isinstance(answer, str) and answer.strip():
                return answer
        except (KeyError, TypeError):
            pass
        raise ProviderError("chat_response_invalid")
