import asyncio
import time
from typing import Literal

import httpx

from assistant_api.storage import Store


class ModelManager:
    """Serialize every model load, unload, selection and generation in this process."""

    def __init__(self, client: httpx.AsyncClient, store: Store, daily_model: str):
        self.client, self.store, self.daily_model = client, store, daily_model
        self.lock = asyncio.Lock()
        self.active_model: str | None = None
        self.last_latency_ms: float | None = None
        self.last_error: str | None = None

    @property
    def selected_model(self) -> str:
        return self.store.get_model(self.daily_model)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Ollama response must be an object")
        return result

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
        async with self.lock:
            try:
                await self._activate(model)
                self.store.set_model(model)
                self.last_error = None
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self.last_error = "Ollama model selection failed"
                raise

    async def unload(self) -> list[str]:
        """Gracefully unload every currently loaded model without changing preference."""
        async with self.lock:
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
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self.last_error = "Ollama model unload failed"
                raise

    async def generate(self, system: str, prompt: str, research_model: str | None = None,
                       *, output_tokens: Literal[1024, 3072] = 1024) -> tuple[str, str]:
        async with self.lock:
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
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self.last_error = "Ollama request failed"
                raise
            finally:
                self.last_latency_ms = round((time.perf_counter() - started) * 1000, 1)

    async def status(self) -> dict:
        async with self.lock:
            tags = await self._request("GET", "/api/tags")
            loaded = await self._request("GET", "/api/ps")
            names = self._names(loaded)
            self.active_model = names[0] if len(names) == 1 else None
            return {
                "selected_model": self.selected_model, "active_model": self.active_model,
                "loaded_models": names, "installed_models": self._names(tags),
                "healthy": len(names) <= 1, "last_latency_ms": self.last_latency_ms,
                "last_error": self.last_error,
            }
