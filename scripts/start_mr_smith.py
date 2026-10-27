"""Start Ollama and Mr Smith when needed, wait for readiness, and open the UI.

Run from any directory:
    .\\.venv\\Scripts\\python.exe .\\scripts\\start_mr_smith.py

The existing Morning page contains both chat and the user-triggered morning
brief. This launcher opens the page but does not send a prompt, fetch headlines,
download models, or set an admin token.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
APP_TITLE = "Mr Smith"
DEFAULT_PORT = 8000
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


class StartupError(RuntimeError):
    """A required local service could not be started or verified."""


def request_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object from {url}")
    return payload


def ollama_models() -> list[str] | None:
    try:
        payload = request_json(f"{OLLAMA_URL}/api/tags")
    except (OSError, HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    models = payload.get("models")
    if not isinstance(models, list):
        raise StartupError("Ollama returned an invalid installed-model list")
    names = [
        item["name"] for item in models
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    return names


def app_identity(base_url: str) -> bool:
    try:
        payload = request_json(f"{base_url}/openapi.json")
    except (OSError, HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return False
    info = payload.get("info")
    return isinstance(info, dict) and info.get("title") == APP_TITLE


def port_is_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def validate_ollama_url() -> None:
    parsed = urlsplit(OLLAMA_URL)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port not in (None, 11434):
        raise StartupError("This launcher only starts a local Ollama service at 127.0.0.1:11434")


def find_ollama_executable() -> str | None:
    executable = shutil.which("ollama")
    if executable:
        return executable
    if os.name == "nt":
        candidates = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe",
        )
        return next((str(path) for path in candidates if path.is_file()), None)
    return None


def start_ollama() -> subprocess.Popen:
    executable = find_ollama_executable()
    if not executable:
        raise StartupError("Ollama is not responding and its executable was not found. Install Ollama or add it to PATH.")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        return subprocess.Popen(
            [executable, "serve"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        raise StartupError(f"Could not start Ollama: {exc}") from exc


def wait_for_ollama(timeout: float) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        names = ollama_models()
        if names is not None:
            return names
        time.sleep(0.5)
    raise StartupError(f"Ollama did not become ready at {OLLAMA_URL} within {timeout:g} seconds")


def wait_for_app(base_url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app_identity(base_url):
            try:
                with urlopen(f"{base_url}/morning", timeout=3) as response:
                    return response.status == 200
            except (OSError, HTTPError, URLError, TimeoutError):
                pass
        time.sleep(0.5)
    return False


def start_app(port: int, log_path: Path) -> subprocess.Popen:
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise StartupError(f"Project virtual environment not found: {python}. Set it up with `python -m venv .venv` and `pip install -e .`.")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        with log_path.open("ab") as log:
            return subprocess.Popen(
                [
                    str(python), "-m", "uvicorn", "assistant_api.main:app",
                    "--host", "127.0.0.1", "--port", str(port),
                ],
                cwd=ROOT,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=flags,
            )
    except OSError as exc:
        raise StartupError(f"Could not start the Mr Smith server: {exc}") from exc


def selected_model(base_url: str) -> str:
    try:
        status = request_json(f"{base_url}/api/models")
    except (OSError, HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise StartupError(f"Mr Smith is responding, but model status could not be read: {exc}") from exc
    model = status.get("selected_model")
    if not isinstance(model, str) or not model:
        raise StartupError("Mr Smith did not report a selected model")
    if status.get("healthy") is not True:
        raise StartupError("Ollama responded, but Mr Smith reports unhealthy model status")
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Local Mr Smith port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Start and verify services without opening a browser")
    parser.add_argument("--ollama-timeout", type=float, default=30, help="Seconds to wait for Ollama readiness")
    parser.add_argument("--app-timeout", type=float, default=45, help="Seconds to wait for Mr Smith readiness")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.ollama_timeout <= 0 or args.app_timeout <= 0:
        parser.error("startup timeouts must be positive")

    base_url = f"http://127.0.0.1:{args.port}"
    app_process = None
    try:
        validate_ollama_url()
        names = ollama_models()
        if names is None:
            print(f"Starting Ollama at {OLLAMA_URL}...")
            start_ollama()
            names = wait_for_ollama(args.ollama_timeout)
        else:
            print(f"Reusing Ollama at {OLLAMA_URL}.")

        if app_identity(base_url):
            print(f"Reusing Mr Smith at {base_url}.")
            if not wait_for_app(base_url, 5):
                raise StartupError("Mr Smith answered its API identity check but its Morning page is unavailable")
        else:
            if port_is_in_use(args.port):
                raise StartupError(
                    f"Port {args.port} is occupied by a service that is not responding as Mr Smith; "
                    "it was left untouched. Choose another port with --port."
                )
            log_path = ROOT / "data" / "logs" / "mr-smith-server.log"
            print(f"Starting Mr Smith on {base_url}...")
            app_process = start_app(args.port, log_path)
            if not wait_for_app(base_url, args.app_timeout):
                if app_process.poll() is not None:
                    raise StartupError(
                        f"Mr Smith exited during startup (code {app_process.returncode}); see {log_path}"
                    )
                raise StartupError(f"Mr Smith did not become ready within {args.app_timeout:g} seconds; see {log_path}")

        model = selected_model(base_url)
        if model not in names:
            raise StartupError(
                f"Mr Smith selected model {model!r}, but Ollama does not have it installed. "
                "Install the model explicitly before chatting; this launcher does not download models."
            )
        page = f"{base_url}/morning"
        print(f"Ready: {page}")
        print(f"Selected model: {model} (installed; it loads on the first chat request)")
        print("Chat and the user-triggered Morning Brief are both available on this page.")
        if not args.no_browser:
            if not webbrowser.open_new_tab(page):
                print("Could not open a browser automatically; open the URL above.")
        return 0
    except StartupError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
