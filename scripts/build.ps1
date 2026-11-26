# Build the frontend so the backend serves everything at http://localhost:8000:   .\scripts\build.ps1
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm run build
Set-Location ..\backend
& ..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
