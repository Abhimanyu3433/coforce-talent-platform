# Start backend (new window, port 8000) and frontend (this window, port 5173):   .\scripts\dev.ps1
Set-Location (Join-Path $PSScriptRoot "..")
$root = (Get-Location).Path
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; & '$root\.venv\Scripts\python.exe' -m uvicorn app.main:app --reload --port 8000"
Write-Host "Backend starting in a new window -> http://localhost:8000/docs"
Push-Location frontend; npm run dev; Pop-Location
