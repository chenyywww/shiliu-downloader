@echo off
chcp 65001 >nul
setlocal
title Douyin Login
python -X utf8 "%~dp0download_router.py" --login-douyin %*
echo.
echo Press any key to close this window.
pause
endlocal
