@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Douyin / Bilibili Video Downloader

set "PY_CMD="
call :try_python python
if not defined PY_CMD call :try_python py -3.13
if not defined PY_CMD call :try_python py -3.12
if not defined PY_CMD call :try_python py -3.11
if not defined PY_CMD call :try_python py -3.10
if not defined PY_CMD call :try_python py -3

if not defined PY_CMD goto :missing

echo Using Python: %PY_CMD%
%PY_CMD% app.py %*
if errorlevel 1 pause
exit /b 0

:try_python
%* -c "import requests, DrissionPage, tkinter" >nul 2>nul
if not errorlevel 1 set "PY_CMD=%*"
exit /b 0

:missing
echo.
echo [ERROR] No Python environment with the required packages was found.
echo.
echo Please install dependencies first:
echo     pip install -r requirements.txt
echo.
echo If you use PyCharm, select an interpreter that has the required packages
echo installed (for example the Python from Anaconda).
echo.
pause
exit /b 1
