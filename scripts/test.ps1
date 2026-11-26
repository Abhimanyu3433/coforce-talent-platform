# Run all backend tests:   .\scripts\test.ps1
Set-Location (Join-Path $PSScriptRoot "..\backend")
& ..\.venv\Scripts\python.exe -m pytest @args
