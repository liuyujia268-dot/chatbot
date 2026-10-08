@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Project Python environment not found.
    exit /b 1
)
".venv\Scripts\python.exe" -B -X utf8 "packaging\build_portable.py" %*
exit /b %errorlevel%
