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

To enable admin functions (model selection, documents, history and private
finance features), Mr Smith needs a private admin token. It is never written
into the application or browser storage. Choose **one** option:

**Option A — set it once (recommended).** Saves the token as a Windows
user environment variable so every future PowerShell window and every Mr
Smith start picks it up automatically. Generate a private random token of
more than 32 characters and save it with this command:

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$token = [Convert]::ToBase64String($bytes)
$rng.Dispose()
[Environment]::SetEnvironmentVariable("MR_SMITH_ADMIN_TOKEN", $token, "User")
$env:MR_SMITH_ADMIN_TOKEN = $token
```

Optionally, keep the approved external news and market feeds always on:

```powershell
[Environment]::SetEnvironmentVariable("MR_SMITH_EXTERNAL_FEEDS", "1", "User")
```

Then **close and reopen PowerShell** (existing windows do not see the new
value) and continue with `Set-Location D:\Mr_Smith`. The token is stored in
plain text in your Windows user profile — do not reuse a password from
elsewhere or commit/share the token. To change it, run the command again; to
remove it:

```powershell
[Environment]::SetEnvironmentVariable("MR_SMITH_ADMIN_TOKEN", $null, "User")
```

**Option B — this PowerShell window only.** Nothing is saved; you must set it
again each time you open a new window. Generate a fresh private token:

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$env:MR_SMITH_ADMIN_TOKEN = [Convert]::ToBase64String($bytes)
$rng.Dispose()
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

External news and market feeds are off by default (unless you saved
`MR_SMITH_EXTERNAL_FEEDS=1` in Option A). To enable the approved feeds for
this server run only, start it with:

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

The Admin page asks for the same token the server was started with. The page
does not remember it, so re-enter it after each page reload. If you used
Option A, the server picks up the token automatically on every start. If you
used Option B, closing that PowerShell window clears the token; set it again
before restarting the app if you need Admin access.

The Admin page's **Local services and resource use** panel shows the Ollama
server and Mr Smith process, installed/loaded models, memory usage and
cumulative CPU time. It can start local Ollama. It can gracefully stop Ollama
only when that server was started by the panel: active model requests finish,
models are unloaded, and then a shutdown signal is sent. You can also unload
models while leaving Ollama running. Independently started Ollama processes
are shown but are never stopped by the panel. Process termination is a
fallback only if the graceful signal cannot be delivered or shutdown times out.

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
