@echo off
chcp 65001 >nul
setlocal
title YouTube Login
python -X utf8 "%~dp0download_router.py" --login-youtube %*
echo.
echo Press any key to close this window.
pause
endlocal
