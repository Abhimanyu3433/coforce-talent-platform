# Throw away all changes and regenerate the synthetic data (stop the backend first):   .\scripts\reset-data.ps1
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path data) { Remove-Item -Recurse -Force data }
& .\.venv\Scripts\python.exe -m data_generator.generate --out data
