# Manual start for the run machine (no service manager). Run from the repo root:  .\scripts\run_prod.ps1
# Keep this window open. Ctrl+C to stop. The bot sweeps missed jobs on every start.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".env")) { throw ".env not found - copy .env.example to .env and fill it in" }
$port = if ($env:PORT) { $env:PORT } else { 8000 }

# Ollama check (only matters if LLM_BASE_URL points to localhost)
try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null; Write-Host "Ollama: OK" }
catch { Write-Warning "Ollama is not reachable on :11434 - the bot will fall back to the rule parser." }

# Keep the PC awake while this window runs (display may still turn off)
Add-Type -Namespace Win -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[void][Win.Power]::SetThreadExecutionState([uint32]"0x80000001")  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

Write-Host "Starting on port $port. Remember to start your tunnel (see docs/USER_TODO.md)."
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $port --workers 1
