# One-time setup on Windows (PowerShell):   .\scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$v = (python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([version]$v -lt [version]"3.10") { throw "Python 3.10+ needed (found $v). Install 3.11 or 3.12 from python.org." }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js 18+ needed — install the LTS from nodejs.org." }
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if (-not (Test-Path data\coforce.db)) {
  Write-Host "Generating synthetic data (about 30 s)..."
  & .\.venv\Scripts\python.exe -m data_generator.generate --out data
}
Push-Location frontend; npm install; Pop-Location
if (-not (Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env; Write-Host "Created backend\.env — add GEMINI_API_KEY there when you have it." }
Write-Host "`nSetup done. Start the app with:  .\scripts\dev.ps1"
