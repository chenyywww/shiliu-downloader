@echo off
chcp 65001 >nul
setlocal
title Build Local Release
python -X utf8 "%~dp0build_release.py"
echo.
pause
endlocal
