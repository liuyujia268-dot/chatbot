@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" goto use_venv

where py >nul 2>nul
if errorlevel 1 goto python_missing

py -3.12 -m app.main
goto finished

:use_venv
".venv\Scripts\python.exe" -m app.main
goto finished

:python_missing
echo [错误] 未找到项目虚拟环境或 Python Launcher。
echo 请先按照 README.md 中的 CMD 初始化步骤配置 Python 3.12 环境。
pause
exit /b 1

:finished
set "APP_EXIT_CODE=%ERRORLEVEL%"
if "%APP_EXIT_CODE%"=="0" exit /b 0
echo.
echo [错误] Chatbot 运行失败，退出码：%APP_EXIT_CODE%
pause
exit /b %APP_EXIT_CODE%
