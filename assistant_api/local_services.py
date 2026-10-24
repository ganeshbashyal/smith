import asyncio
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
from urllib.parse import urlsplit

import httpx


class LocalServiceController:
    """Controls only an Ollama server process launched by this app."""

    def __init__(self, ollama_url: str):
        parsed = urlsplit(ollama_url)
        self.supported = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost") and parsed.port in (None, 11434)
        self.process: subprocess.Popen | None = None

    @property
    def can_stop(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @staticmethod
    def _resources(app_pid: int) -> dict:
        if os.name != "nt":
            return {"status": "unavailable", "error": "Process resource details are supported on Windows only", "processes": []}
        command = (
            "$appPid = " + str(int(app_pid)) + "; "
            "$rows = @(Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -like 'ollama*' -or $_.ProcessId -eq $appPid } | "
            "ForEach-Object { "
            "$proc = Get-Process -Id $_.ProcessId -ErrorAction Stop; "
            "[PSCustomObject]@{ "
            "pid = [int]$_.ProcessId; name = [string]$_.Name; "
            "role = $(if ($_.ProcessId -eq $appPid) { 'mr_smith' } else { 'ollama' }); "
            "memory_mb = [math]::Round(([double]$_.WorkingSetSize / 1MB), 1); "
            "cpu_seconds = [math]::Round([double]$proc.CPU, 1) "
            "} }); "
            "ConvertTo-Json -InputObject $rows -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=8, check=True,
            )
            rows = json.loads(result.stdout or "[]")
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list):
                raise ValueError("Unexpected process resource response")
            return {"status": "ok", "processes": rows}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
            return {"status": "unavailable", "error": f"Could not read Windows process resources ({type(exc).__name__})", "processes": []}

    async def _ollama_status(self, client: httpx.AsyncClient) -> tuple[bool, list[str], list[str]]:
        if not self.supported:
            return False, [], []
        try:
            tags = await client.get("/api/tags")
            tags.raise_for_status()
            running = await client.get("/api/ps")
            running.raise_for_status()
            installed_data = tags.json().get("models", [])
            loaded_data = running.json().get("models", [])
            installed = [item["name"] for item in installed_data if isinstance(item, dict) and isinstance(item.get("name"), str)]
            loaded = [item["name"] for item in loaded_data if isinstance(item, dict) and isinstance(item.get("name"), str)]
            return True, installed, loaded
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False, [], []

    async def status(self, client: httpx.AsyncClient, app_pid: int) -> dict:
        running, installed, loaded = await self._ollama_status(client)
        process = self.process
        if process and process.poll() is not None:
            self.process = None
            process = None
        resources = await asyncio.to_thread(self._resources, app_pid)
        return {
            "supported": self.supported,
            "ollama": {
                "status": "running" if running else "stopped",
                "managed_by_mr_smith": process is not None,
                "managed_pid": process.pid if process else None,
                "installed_models": installed,
                "loaded_models": loaded,
            },
            "resources": resources,
        }

    async def start(self, client: httpx.AsyncClient) -> dict:
        if not self.supported:
            raise ValueError("Service controls are limited to local Ollama at 127.0.0.1:11434")
        if self.can_stop:
            raise RuntimeError("An Ollama server process started here is already running")
        running, _, _ = await self._ollama_status(client)
        if running:
            raise RuntimeError("Ollama is already running; this app will not take ownership of an existing service")
        executable = shutil.which("ollama")
        if not executable and os.name == "nt":
            candidates = (
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Ollama" / "ollama.exe",
            )
            executable = next((str(path) for path in candidates if path.is_file()), None)
        if not executable:
            raise FileNotFoundError("Could not find ollama.exe; install Ollama or add it to PATH")
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.process = subprocess.Popen(
            [executable, "serve"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=creationflags,
        )
        for _ in range(20):
            if self.process.poll() is not None:
                self.process = None
                raise RuntimeError("Ollama exited before its local API became ready")
            await asyncio.sleep(0.5)
            running, installed, loaded = await self._ollama_status(client)
            if running:
                return {"status": "running", "managed_by_mr_smith": True, "pid": self.process.pid,
                        "installed_models": installed, "loaded_models": loaded}
        await self._shutdown_process(self.process)
        self.process = None
        raise TimeoutError("Ollama did not become ready within 10 seconds")

    async def stop(self) -> dict:
        process = self.process
        if process is None or process.poll() is not None:
            self.process = None
            raise RuntimeError("Ollama was not started by this Admin panel; independent processes are left untouched")
        pid = process.pid
        method = "ctrl_break"
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            await asyncio.to_thread(process.wait, 8)
        except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
            method = "terminate_fallback"
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 3)
            except subprocess.TimeoutExpired:
                method = "kill_fallback"
                process.kill()
                await asyncio.to_thread(process.wait, 3)
        self.process = None
        return {
            "status": "stopped",
            "managed_by_mr_smith": True,
            "pid": pid,
            "shutdown_method": method,
            "graceful": method == "ctrl_break",
        }

    @staticmethod
    async def _shutdown_process(process: subprocess.Popen) -> None:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            await asyncio.to_thread(process.wait, 3)
        except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 3)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 3)
