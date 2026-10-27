import subprocess

from scripts import start_mr_smith


def test_app_identity_requires_mr_smith_openapi_title(monkeypatch):
    monkeypatch.setattr(
        start_mr_smith, "request_json",
        lambda url: {"info": {"title": "Mr Smith"}, "paths": {}},
    )
    assert start_mr_smith.app_identity("http://127.0.0.1:8000")

    monkeypatch.setattr(
        start_mr_smith, "request_json",
        lambda url: {"info": {"title": "Other service"}},
    )
    assert not start_mr_smith.app_identity("http://127.0.0.1:8000")


def test_app_identity_rejects_malformed_openapi_info(monkeypatch):
    monkeypatch.setattr(start_mr_smith, "request_json", lambda url: {"info": "invalid"})
    assert not start_mr_smith.app_identity("http://127.0.0.1:8000")


def test_ollama_models_validates_response_and_reports_unavailable(monkeypatch):
    monkeypatch.setattr(
        start_mr_smith, "request_json",
        lambda url: {"models": [{"name": "llama3.2:latest"}, {"invalid": True}]},
    )
    assert start_mr_smith.ollama_models() == ["llama3.2:latest"]

    monkeypatch.setattr(start_mr_smith, "request_json", lambda url: {})
    try:
        start_mr_smith.ollama_models()
    except start_mr_smith.StartupError as exc:
        assert "invalid installed-model list" in str(exc)
    else:
        raise AssertionError("Invalid model payload should fail explicitly")


def test_launcher_rejects_remote_ollama_url(monkeypatch):
    monkeypatch.setattr(start_mr_smith, "OLLAMA_URL", "http://192.0.2.10:11434")
    try:
        start_mr_smith.validate_ollama_url()
    except start_mr_smith.StartupError as exc:
        assert "only starts a local Ollama" in str(exc)
    else:
        raise AssertionError("Remote Ollama URL should not trigger a local service start")


def test_windows_detached_process_flags_are_used_for_ollama(monkeypatch):
    calls = []
    monkeypatch.setattr(start_mr_smith.os, "name", "nt")
    monkeypatch.setattr(start_mr_smith, "find_ollama_executable", lambda: "C:\\Ollama\\ollama.exe")
    monkeypatch.setattr(
        start_mr_smith.subprocess, "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)) or object(),
    )

    start_mr_smith.start_ollama()

    assert calls[0][0] == ["C:\\Ollama\\ollama.exe", "serve"]
    assert calls[0][1]["creationflags"] == (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
