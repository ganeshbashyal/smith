# Start Mr Smith on Windows

Use **Windows PowerShell**. The application, Python environment and runtime
database are under `D:\Mr_Smith`. Ollama must be running locally before Mr
Smith can generate answers.

## 1. Start Ollama

Open the [official Ollama download page](https://ollama.com/download/windows)
and install/start Ollama for Windows. In a PowerShell window, check that the
local service responds:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

If the check says the connection was refused, start the Ollama desktop
application. Alternatively, run `ollama serve` in its own PowerShell window
and leave that window open. Do not start a second Ollama server if one is
already running.

Mr Smith's default models are `llama3.1:8b` for daily chat and `gemma4:26b`
for research. Install them once if they are not already present:

```powershell
ollama pull llama3.1:8b
ollama pull gemma4:26b
ollama list
```

Model downloads can be large. You can choose other installed models from the
Admin page.

## 2. Start Mr Smith from D:

Open a **second** PowerShell window and run:

```powershell
Set-Location D:\Mr_Smith
```

To enable admin functions (model selection, documents and history), create a
private token for this PowerShell session. It is not written into the
application or browser storage:

```powershell
$secure = Read-Host "Choose a private Mr Smith admin token" -AsSecureString
$env:MR_SMITH_ADMIN_TOKEN = (New-Object Net.NetworkCredential('', $secure)).Password
```

Start the app in the foreground:

```powershell
.\scripts\start-mr-smith.ps1
```

If PowerShell blocks scripts because of execution policy, use this one-run
bypass from the same directory; it does not change the machine's policy and
inherits the token set in the current PowerShell window:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\Mr_Smith\scripts\start-mr-smith.ps1"
```

The launcher reuses `D:\Mr_Smith\.venv`, installs the local package if needed,
checks whether Ollama is reachable, and serves Mr Smith on `127.0.0.1:8000`.
Keep this PowerShell window open while using the app. Press **Ctrl+C** in this
window to stop the foreground server.

External news and market feeds are off by default. To enable the approved feeds
for this server run, start it with:

```powershell
.\scripts\start-mr-smith.ps1 -ExternalFeeds
```

If admin features are not needed, skip the private-token setup; the chat still
works, but admin mutations will be unavailable. For advanced launcher options,
run:

```powershell
Get-Help .\scripts\start-mr-smith.ps1 -Full
```

## 3. Open the interaction and configuration pages

Once the server is running, open any of these links:

| Page | Link |
|---|---|
| Mr Smith interaction — Morning Brief, Financial Analysis and Research | [http://127.0.0.1:8000/morning](http://127.0.0.1:8000/morning) |
| Configuration and model selection | [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin) |
| API documentation | [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) |
| OpenAPI schema | [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json) |
| Installed/selected model status | [http://127.0.0.1:8000/api/models](http://127.0.0.1:8000/api/models) |
| Sports source coverage | [http://127.0.0.1:8000/api/coverage/sports](http://127.0.0.1:8000/api/coverage/sports) |

The Admin page asks for the same token set in the server's PowerShell window.
If that PowerShell window is closed, its token is cleared; set it again before
restarting the app if you need Admin access.

The Admin page's **Local services and resource use** panel shows the Ollama
server and Mr Smith process, installed/loaded models, memory usage and
cumulative CPU time. It can start local Ollama. It can gracefully stop Ollama
only when that server was started by the panel: active model requests finish,
models are unloaded, and then a shutdown signal is sent. You can also unload
models while leaving Ollama running. Independently started Ollama processes
are shown but are never stopped by the panel.

## 4. Quick health checks

In another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags # Ollama and installed models
Invoke-RestMethod http://127.0.0.1:8000/api/models # Mr Smith and active model
```

The Mr Smith model status should report the selected model, installed models,
loaded model and health. The first answer can take longer while Ollama loads a
model. If a model is missing, install it with `ollama pull <model-name>`.

## Keep it local

Mr Smith is configured for local use. Do not expose port 8000 to the network.
External feeds, when explicitly enabled, make requests to the application's
approved public sources; they do not send private documents or conversation
history. The app's local database is `D:\Mr_Smith\data\mr_smith.sqlite3`.

Source code and changes are tracked in the
[Mr Smith GitHub repository](https://github.com/ganeshbashyal/smith), branch
[`deepdive-personas`](https://github.com/ganeshbashyal/smith/tree/deepdive-personas).
