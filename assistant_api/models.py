import asyncio
from contextlib import asynccontextmanager
import time
from typing import Literal

import httpx

from assistant_api.provider import ModelProvider, OllamaProvider, ProviderError
from assistant_api.storage import Store


class ModelManager:
    """Serialize every model load, unload, selection and generation in this process."""

    def __init__(self, client: httpx.AsyncClient, store: Store, daily_model: str,
                 provider: ModelProvider | None = None):
        self.client, self.provider, self.store, self.daily_model = (
            client, provider or OllamaProvider(client), store, daily_model,
        )
        self.lock = asyncio.Lock()
        self._waiting = 0
        self._operation: str | None = None
        self.active_model: str | None = None
        self.last_latency_ms: float | None = None
        self.last_error: str | None = None

    @property
    def operation_status(self) -> dict:
        return {
            "in_flight": self._operation is not None,
            "operation": self._operation,
            "queued_requests": self._waiting,
        }

    @asynccontextmanager
    async def _serialized(self, operation: str):
        self._waiting += 1
        waiting = True
        try:
            async with self.lock:
                self._waiting -= 1
                waiting = False
                self._operation = operation
                try:
                    yield
                finally:
                    self._operation = None
        finally:
            if waiting:
                self._waiting -= 1

    @property
    def selected_model(self) -> str:
        return self.store.get_model(self.daily_model)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        return await self.provider.request(method, path, **kwargs)

    @staticmethod
    def _names(result: dict) -> list[str]:
        models = result.get("models")
        if not isinstance(models, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]
            for item in models
        ):
            raise ValueError("Ollama returned an invalid model list")
        return [item["name"] for item in models]

    async def _activate(self, model: str):
        installed = await self._request("GET", "/api/tags")
        if model not in self._names(installed):
            raise ValueError(f"Model is not installed: {model}")
        loaded = await self._request("GET", "/api/ps")
        self.active_model = None
        for name in self._names(loaded):
            if name != model:
                result = await self._request("POST", "/api/generate", json={
                    "model": name, "keep_alive": 0, "stream": False,
                })
                if result.get("done") is not True:
                    raise ValueError("Ollama did not acknowledge model unload")
        remaining = await self._request("GET", "/api/ps")
        remaining_names = self._names(remaining)
        if any(name != model for name in remaining_names):
            raise ValueError("Another Ollama model remains loaded")
        # Loading only follows successful unload acknowledgments.
        if not remaining_names:
            result = await self._request("POST", "/api/generate", json={
                "model": model, "keep_alive": "5m", "stream": False,
            })
            if result.get("done") is not True:
                raise ValueError("Ollama did not acknowledge model load")
        self.active_model = model

    async def select(self, model: str):
        async with self._serialized("select_model"):
            try:
                await self._activate(model)
                self.store.set_model(model)
                self.last_error = None
            except (ProviderError, httpx.HTTPError, ValueError, KeyError, TypeError):
                self.last_error = "Ollama model selection failed"
                raise

    async def unload(self) -> list[str]:
        """Gracefully unload every currently loaded model without changing preference."""
        async with self._serialized("unload_models"):
            try:
                loaded = self._names(await self._request("GET", "/api/ps"))
                for name in loaded:
                    result = await self._request("POST", "/api/generate", json={
                        "model": name, "keep_alive": 0, "stream": False,
                    })
                    if result.get("done") is not True:
                        raise ValueError("Ollama did not acknowledge model unload")
                remaining = self._names(await self._request("GET", "/api/ps"))
                if remaining:
                    raise ValueError("Ollama still has models loaded")
                self.active_model = None
                self.last_error = None
                return loaded
            except (ProviderError, httpx.HTTPError, ValueError, KeyError, TypeError):
                self.last_error = "Ollama model unload failed"
                raise

    async def generate(self, system: str, prompt: str, research_model: str | None = None,
                       *, output_tokens: Literal[1024, 3072] = 1024) -> tuple[str, str]:
        async with self._serialized("generate"):
            model = research_model or self.selected_model
            started = time.perf_counter()
            try:
                await self._activate(model)
                result = await self._request("POST", "/api/chat", json={
                    "model": model, "stream": False, "keep_alive": "5m",
                    "options": {"num_ctx": 16384, "num_predict": output_tokens},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                })
                answer = result["message"]["content"]
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Ollama returned an empty answer")
                self.last_error = None
                return answer, model
            except (ProviderError, httpx.HTTPError, ValueError, KeyError, TypeError):
                self.last_error = "Ollama request failed"
                raise
            finally:
                self.last_latency_ms = round((time.perf_counter() - started) * 1000, 1)

    async def status(self) -> dict:
        queue_at_request = self.operation_status
        async with self._serialized("status"):
            tags = await self._request("GET", "/api/tags")
            loaded = await self._request("GET", "/api/ps")
            names = self._names(loaded)
            self.active_model = names[0] if len(names) == 1 else None
            return {
                "selected_model": self.selected_model, "active_model": self.active_model,
                "loaded_models": names, "installed_models": self._names(tags),
                "healthy": len(names) <= 1, "last_latency_ms": self.last_latency_ms,
                "last_error": self.last_error, "operation_queue": queue_at_request,
            }
