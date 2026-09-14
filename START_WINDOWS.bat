@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" goto deps
py -3 --version >nul 2>&1
if errorlevel 1 (
    python --version >nul 2>&1
    if errorlevel 1 (
        echo Установите Python 3.12 или 3.13 с опцией Add Python to PATH.
        pause
        exit /b 1
    )
    python -m venv .venv
) else (
    py -3 -m venv .venv
)
if errorlevel 1 goto fail
:deps
.venv\Scripts\python.exe -c "import fastapi,uvicorn,sqlalchemy,pydantic,defusedxml" >nul 2>&1
if errorlevel 1 (
    echo Устанавливаем зависимости. Интернет нужен только при первой установке.
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe start.py %*
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Запуск остановлен из-за ошибки выше. Скопируйте её текст, не закрывая окно.
pause
exit /b 1
