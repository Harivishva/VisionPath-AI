# Runs lint, type checks, unit/integration tests and the production build.
#   .\run-tests.ps1          backend + frontend tests, lint, typecheck, build
#   .\run-tests.ps1 -E2E     additionally Playwright e2e (needs both servers running; uses Edge + synthetic devices)
#   .\run-tests.ps1 -E2E -RealDevices   additionally the real webcam/microphone test (turns your camera on briefly)
param([switch]$E2E, [switch]$RealDevices)
$root = $PSScriptRoot
$venv = if ($env:VP_VENV) { $env:VP_VENV } else { Join-Path $root "backend\.venv" }
$py = Join-Path $venv "Scripts\python.exe"
$failed = @()

# Native tools write progress/warnings to stderr; judge success by exit code, not by stderr output.
function Step([string]$name, [scriptblock]$cmd) {
  Write-Host "`n=== $name" -ForegroundColor Cyan
  & $cmd
  if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $name" -ForegroundColor Red; $script:failed += $name }
}

Push-Location (Join-Path $root "backend")
Step "backend lint (ruff)" { & $py -m ruff check app tests scripts --select "F,E9,B" --ignore B008 }
Step "backend tests (pytest)" { & $py -m pytest tests -q }
Pop-Location

Push-Location (Join-Path $root "frontend")
Step "frontend type check" { npx tsc --noEmit }
Step "frontend tests (vitest)" { npx vitest run }
Step "frontend production build" { npm run build }
if ($E2E) { Step "e2e (synthetic devices)" { npx playwright test --project=fake-devices } }
if ($RealDevices) { $env:E2E_REAL_DEVICES = "1"; Step "e2e (REAL webcam + microphone)" { npx playwright test --project=real-devices } }
Pop-Location

if ($failed.Count) { Write-Host "`nFailed steps: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "`nAll steps passed." -ForegroundColor Green
