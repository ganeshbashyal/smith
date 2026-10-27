"""Run a mock-only application, SQLite, auth and shutdown smoke check."""

import argparse
import json
import multiprocessing
import os
from pathlib import Path
import tempfile

import httpx
from fastapi.testclient import TestClient

from assistant_api.config import Settings
from assistant_api.main import create_app
from assistant_api.storage import Store


class FakeOllama:
    def __init__(self):
        self.loaded: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "smoke-model"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": name} for name in self.loaded]})
        if request.url.path == "/api/generate":
            model = body["model"]
            if body.get("keep_alive") == 0:
                if model in self.loaded:
                    self.loaded.remove(model)
            elif model not in self.loaded:
                self.loaded.append(model)
            return httpx.Response(200, json={"done": True})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "Mock smoke answer"}})
        return httpx.Response(404)


def _exercise_app(data_dir: str) -> None:
    path = Path(data_dir)
    ollama = FakeOllama()
    settings = Settings(
        path, "http://127.0.0.1:11434", "smoke-model", "smoke-model",
        "smoke-admin-token-not-for-production", False,
    )
    app = create_app(settings, ollama_transport=httpx.MockTransport(ollama))
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/morning").status_code == 200
        assert client.get("/admin").status_code == 200
        assert client.post("/api/admin/model", json={"model": "smoke-model"}).status_code == 401
        assert client.post(
            "/api/admin/model",
            headers={"Authorization": "Bearer smoke-admin-token-not-for-production"},
            json={"model": "smoke-model"},
        ).status_code == 200
        chat = client.post("/api/chat", json={"question": "Explain a stable concept"})
        assert chat.status_code == 200 and chat.json()["answer"] == "Mock smoke answer"
        status = client.get("/api/models")
        assert status.status_code == 200 and status.json()["active_model"] == "smoke-model"
        assert Store(path).path.is_file()
    assert app.state.feeds.is_closed
    assert app.state.models.client.is_closed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=Path(os.environ["MR_SMITH_SMOKE_TEMP"]) if os.environ.get("MR_SMITH_SMOKE_TEMP")
        else Path(tempfile.gettempdir()),
        help="Writable parent for an isolated disposable smoke database (default: system temp).",
    )
    arguments = parser.parse_args()
    arguments.temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mr-smith-smoke-", dir=arguments.temp_dir) as scratch:
        data_dir = Path(scratch) / "data"
        data_dir.mkdir()
        process = multiprocessing.get_context("spawn").Process(
            target=_exercise_app, args=(str(data_dir),),
        )
        try:
            process.start()
            process.join(timeout=60)
            if process.is_alive():
                process.terminate()
                process.join()
                raise SystemExit("Smoke check timed out after 60 seconds")
            if process.exitcode != 0:
                raise SystemExit(process.exitcode or 1)
        finally:
            if process.is_alive():
                process.terminate()
                process.join()
    print("Smoke check passed: app, OpenAPI, SQLite, admin auth, mocked chat, and clean shutdown.")


if __name__ == "__main__":
    main()
