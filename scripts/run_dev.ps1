# Dev: webhook server with auto-reload. Use your DEV Page/App tokens in .env.
Set-Location (Split-Path $PSScriptRoot -Parent)
$port = if ($env:PORT) { $env:PORT } else { 8000 }
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $port
