@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist ".venv\Scripts\pythonw.exe" goto use_venv

where pyw >nul 2>nul
if errorlevel 1 goto python_missing

start "" pyw -3.12 -m app.ui
exit /b 0

:use_venv
start "" ".venv\Scripts\pythonw.exe" -m app.ui
exit /b 0

:python_missing
echo [错误] 未找到项目虚拟环境或 Python Launcher。
echo 请先按照 README.md 中的 CMD 初始化步骤配置 Python 3.12 环境。
pause
exit /b 1
