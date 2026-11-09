# One-time setup: Python venv + dependencies, models, frontend dependencies + production build.
# Windows PowerShell. If your project path is very long (>~150 chars) set VP_VENV to a short path first:
#   $env:VP_VENV = "C:\vpenv"
$root = $PSScriptRoot
$venv = if ($env:VP_VENV) { $env:VP_VENV } else { Join-Path $root "backend\.venv" }
$py = Join-Path $venv "Scripts\python.exe"

function Step([string]$name, [scriptblock]$cmd) {
  Write-Host "`n=== $name" -ForegroundColor Cyan
  & $cmd
  if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $name" -ForegroundColor Red; exit 1 }
}

if (-not (Test-Path $py)) { Step "create venv at $venv" { python -m venv $venv } }
Step "install Python dependencies" { & $py -m pip install -r (Join-Path $root "backend\requirements-dev.txt") }
Step "download models (MediaPipe face landmarker + local Whisper small.en)" {
  & $py (Join-Path $root "backend\scripts\download_models.py") --whisper
}
Push-Location (Join-Path $root "frontend")
Step "install frontend dependencies" { npm install --no-audit --no-fund }
Step "build frontend" { npm run build }
Pop-Location
Write-Host "`nSetup complete. Start everything with .\start-all.ps1" -ForegroundColor Green
