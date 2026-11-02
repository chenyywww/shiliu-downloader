@echo off
chcp 65001 >nul
setlocal
title Download Resolved Plan Locally
python -X utf8 "%~dp0download_plan.py" %*
echo.
pause
endlocal
