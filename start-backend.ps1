# Starts the FastAPI backend on http://localhost:8000
$root = $PSScriptRoot
$venv = if ($env:VP_VENV) { $env:VP_VENV } else { Join-Path $root "backend\.venv" }
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Python venv not found at $venv. Run .\setup.ps1 first."; exit 1 }
Set-Location (Join-Path $root "backend")
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
