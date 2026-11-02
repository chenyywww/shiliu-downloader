@echo off
chcp 65001 >nul
setlocal
title Video Downloader
python -X utf8 "%~dp0download_router.py" %*
echo.
echo Press any key to close this window.
pause
endlocal
