@echo off
chcp 65001 >nul
setlocal
title Local Video Downloader
python -X utf8 "%~dp0web_app.py" --launch %*
if errorlevel 1 pause
endlocal
