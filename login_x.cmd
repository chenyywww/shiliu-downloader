@echo off
chcp 65001 >nul
setlocal
title X Login - Video Downloader
python -X utf8 "%~dp0download_router.py" --login-x
echo.
pause
endlocal
