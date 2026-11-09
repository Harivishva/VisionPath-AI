# Starts the Next.js production server on http://localhost:3000 (builds first if needed)
Set-Location (Join-Path $PSScriptRoot "frontend")
if (-not (Test-Path ".next\BUILD_ID")) { npm run build }
npm run start
