@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not defined CHATBOT_QQ_EXE exit /b 2
if not exist "%CHATBOT_QQ_EXE%" exit /b 2
set "NAPCAT_PATCH_PACKAGE=%cd%\qqnt.json"
set "NAPCAT_LOAD_PATH=%cd%\loadNapCat.js"
set "NAPCAT_INJECT_PATH=%cd%\NapCatWinBootHook.dll"
set "NAPCAT_LAUNCHER_PATH=%cd%\NapCatWinBootMain.exe"
set "NAPCAT_MAIN_PATH=%cd%\napcat.mjs"
"%NAPCAT_LAUNCHER_PATH%" "%CHATBOT_QQ_EXE%" "%NAPCAT_INJECT_PATH%" %*
exit /b %errorlevel%
