param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { throw 'Install Node.js and ensure npm is available on PATH.' }
if (-not (Test-Path '.venv\Scripts\python.exe')) { throw 'Create .venv and install the project before starting.' }
if (-not (Test-Path 'frontend\node_modules')) { & $npm.Source --prefix frontend ci; if ($LASTEXITCODE -ne 0) { throw 'Frontend install failed.' } }
& $npm.Source --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
# FastAPI loads .env and applies LIVE defaults only where no Provider is configured.
Write-Host "Starting Seldon at http://127.0.0.1:$Port"
& '.venv\Scripts\python.exe' -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
