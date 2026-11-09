# Opens the backend and frontend in two PowerShell windows, then opens the app.
$root = $PSScriptRoot
Start-Process powershell -ArgumentList "-NoExit", "-File", (Join-Path $root "start-backend.ps1")
Start-Process powershell -ArgumentList "-NoExit", "-File", (Join-Path $root "start-frontend.ps1")
Write-Host "Waiting for servers..."
for ($i = 0; $i -lt 60; $i++) {
  try {
    Invoke-RestMethod http://localhost:8000/api/health -TimeoutSec 2 | Out-Null
    Invoke-WebRequest http://localhost:3000 -UseBasicParsing -TimeoutSec 2 | Out-Null
    Start-Process "http://localhost:3000"
    Write-Host "VisionPath AI is running at http://localhost:3000 (API: http://localhost:8000/docs)"
    exit 0
  } catch { Start-Sleep 2 }
}
Write-Warning "Servers did not become ready in time; check the two PowerShell windows for errors."
