@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Prepare o ambiente seguindo o README.md.
    exit /b 1
)
".venv\Scripts\python.exe" app.py %*
