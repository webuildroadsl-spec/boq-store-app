@echo off
title BOQ ^& Store app - setup
rem One-time setup: libraries, database, settings, admin login.
rem Safe to run again; it skips what is already done.
cd /d "%~dp0.."
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set PY="%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\anaconda3\python.exe" set PY="%LOCALAPPDATA%\anaconda3\python.exe"
if not defined PY (
    echo Could not find Python on this computer.
    echo Install it from python.org ^(tick "Add python.exe to PATH"^) and try again.
    goto :end
)
%PY% scripts\setup_local.py
:end
echo.
pause
