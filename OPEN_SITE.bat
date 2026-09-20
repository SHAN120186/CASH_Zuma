@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
".venv-local\Scripts\python.exe" deploy\open_site.py
if errorlevel 1 pause
