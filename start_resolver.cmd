@echo off
chcp 65001 >nul
setlocal
title Resolve Video Links Only
python -X utf8 "%~dp0web_app.py" --launch --resolve-only --port 8766 %*
if errorlevel 1 pause
endlocal
