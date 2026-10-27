"""Create a strong local admin token, start Mr Smith, and open System/Admin.

Run from any directory:
    .\\.venv\\Scripts\\python.exe .\\scripts\\start_mr_smith_admin.py

The token is saved in the current Windows user's environment registry and
passed directly to the new server process. It is printed once so it can be
pasted into the Admin page; it is never written to an application file or log.
"""

import argparse
import os
from pathlib import Path
import secrets
import socket
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import start_mr_smith


def persist_user_token(token: str) -> None:
    """Persist the token for future Windows-launched processes."""
    if os.name != "nt":
        raise start_mr_smith.StartupError(
            "Persistent user-token setup is supported on Windows only; "
            "set MR_SMITH_ADMIN_TOKEN in the server environment manually."
        )
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "MR_SMITH_ADMIN_TOKEN", 0, winreg.REG_SZ, token)


def available_port(start: int, attempts: int = 100) -> int:
    for port in range(start, min(start + attempts, 65536)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise start_mr_smith.StartupError(
        f"No free local port found between {start} and {min(start + attempts - 1, 65535)}"
    )


def admin_token_works(base_url: str, token: str) -> bool:
    request = urllib.request.Request(
        f"{base_url}/api/admin/services",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=start_mr_smith.DEFAULT_PORT,
                        help="Preferred local app port; uses the next free port if occupied")
    parser.add_argument("--no-browser", action="store_true",
                        help="Start and verify services without opening a browser")
    parser.add_argument("--ollama-timeout", type=float, default=30)
    parser.add_argument("--app-timeout", type=float, default=45)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.ollama_timeout <= 0 or args.app_timeout <= 0:
        parser.error("startup timeouts must be positive")

    token = secrets.token_urlsafe(32)
    if len(token) < 32:
        raise start_mr_smith.StartupError("Generated admin token did not meet the minimum length")

    app_process = None
    try:
        persist_user_token(token)
        os.environ["MR_SMITH_ADMIN_TOKEN"] = token
        start_mr_smith.validate_ollama_url()

        names = start_mr_smith.ollama_models()
        if names is None:
            print(f"Starting Ollama at {start_mr_smith.OLLAMA_URL}...")
            start_mr_smith.start_ollama()
            names = start_mr_smith.wait_for_ollama(args.ollama_timeout)
        else:
            print(f"Reusing Ollama at {start_mr_smith.OLLAMA_URL}.")

        port = available_port(args.port)
        if port != args.port:
            print(
                f"Port {args.port} is already occupied; leaving its service untouched "
                f"and using port {port}."
            )
        base_url = f"http://127.0.0.1:{port}"
        log_path = start_mr_smith.ROOT / "data" / "logs" / "mr-smith-server.log"
        print(f"Starting Mr Smith on {base_url}...")
        app_process = start_mr_smith.start_app(port, log_path)
        if not start_mr_smith.wait_for_app(base_url, args.app_timeout):
            if app_process.poll() is not None:
                raise start_mr_smith.StartupError(
                    f"Mr Smith exited during startup (code {app_process.returncode}); see {log_path}"
                )
            raise start_mr_smith.StartupError(
                f"Mr Smith did not become ready within {args.app_timeout:g} seconds; see {log_path}"
            )

        model = start_mr_smith.selected_model(base_url)
        if model not in names:
            raise start_mr_smith.StartupError(
                f"Mr Smith selected model {model!r}, but Ollama does not have it installed. "
                "Install the model explicitly before chatting; this launcher does not download models."
            )
        if not admin_token_works(base_url, token):
            raise start_mr_smith.StartupError(
                "Mr Smith started, but rejected the generated admin token. "
                "Check the server log and do not use the token shown below."
            )

        page = f"{base_url}/admin"
        print(f"Ready: {page}")
        print(f"Selected model: {model} (installed; it loads on the first chat request)")
        print("Copy this token into the Admin token field. Keep it private:")
        print(token)
        if not args.no_browser and not webbrowser.open_new_tab(page):
            print("Could not open a browser automatically; open the URL above.")
        return 0
    except start_mr_smith.StartupError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
