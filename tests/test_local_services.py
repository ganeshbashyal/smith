import asyncio
import signal

import httpx
import pytest

from assistant_api.local_services import LocalServiceController


class FakeProcess:
    pid = 4242

    def __init__(self, *, fail_signal=False):
        self.returncode = None
        self.signals = []
        self.terminated = False
        self.fail_signal = fail_signal

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        if self.fail_signal:
            raise OSError("control event unavailable")
        self.signals.append(value)
        self.returncode = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            raise TimeoutError
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9


def test_service_stop_uses_graceful_control_event_first(monkeypatch):
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    controller = LocalServiceController("http://127.0.0.1:11434")
    process = FakeProcess()
    controller.process = process

    result = asyncio.run(controller.stop())

    assert result == {
        "status": "stopped", "managed_by_mr_smith": True, "pid": 4242,
        "shutdown_method": "ctrl_break", "graceful": True,
    }
    assert process.signals == [1]
    assert not process.terminated


def test_service_stop_reports_fallback_when_graceful_signal_fails():
    controller = LocalServiceController("http://127.0.0.1:11434")
    process = FakeProcess(fail_signal=True)
    controller.process = process

    result = asyncio.run(controller.stop())

    assert result["shutdown_method"] == "terminate_fallback"
    assert not result["graceful"]
    assert process.terminated


def test_start_ollama_launches_only_after_local_service_unavailable(monkeypatch):
    controller = LocalServiceController("http://127.0.0.1:11434")

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Client:
        healthy = False

        async def get(self, path):
            if not self.healthy:
                raise httpx.ConnectError("not running")
            if path == "/api/tags":
                return Response({"models": [{"name": "llama3.1:8b"}]})
            return Response({"models": []})

    client = Client()
    process = FakeProcess()
    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        client.healthy = True
        return process

    monkeypatch.setattr("assistant_api.local_services.shutil.which", lambda name: "C:\\Ollama\\ollama.exe")
    monkeypatch.setattr("assistant_api.local_services.subprocess.Popen", popen)

    async def start_and_wait():
        return await controller.start(client)

    result = asyncio.run(start_and_wait())

    assert result["status"] == "running"
    assert result["managed_by_mr_smith"]
    assert result["installed_models"] == ["llama3.1:8b"]
    assert calls[0][0] == ["C:\\Ollama\\ollama.exe", "serve"]
    assert controller.can_stop


def test_service_controller_disables_nonlocal_ollama_hosts():
    controller = LocalServiceController("http://192.0.2.5:11434")
    assert not controller.supported
    with pytest.raises(ValueError, match="local Ollama"):
        asyncio.run(controller.start(object()))
