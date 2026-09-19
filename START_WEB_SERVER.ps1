# Temporary HTTPS preview of the committed project. Keeps the LAN server running.
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectPath
$pythonPath = Join-Path $projectPath '.venv-local/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = Join-Path $projectPath '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run START_LOCAL.bat to prepare Python first.' }
& $pythonPath 'deploy/preview.py'
exit $LASTEXITCODE
