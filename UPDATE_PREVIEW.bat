@echo off
cd /d "%~dp0"
if exist ".venv-local\Scripts\python.exe" (
  ".venv-local\Scripts\python.exe" deploy\preview.py --update
) else (
  ".venv\Scripts\python.exe" deploy\preview.py --update
)
pause
