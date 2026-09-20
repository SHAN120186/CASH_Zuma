@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
".venv-local\Scripts\python.exe" deploy\open_site.py --local-only
if errorlevel 1 pause
if not errorlevel 1 (
  echo.
  echo UZGERMED работает на http://127.0.0.1:8001
  echo Не закрывайте это окно во время работы. Можно свернуть его.
  pause
)
