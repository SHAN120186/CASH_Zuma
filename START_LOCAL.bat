@echo off
cd /d "%~dp0"
set "PORT=8001"
set "COOKIE_SECURE=0"
set "PUBLIC_ORIGIN=http://127.0.0.1:8001"
set "ALLOWED_HOSTS=127.0.0.1,localhost"
".venv-local\Scripts\python.exe" start_local.py %*
pause
