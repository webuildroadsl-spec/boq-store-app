@echo off
title BOQ ^& Store app - running (close this window to stop)
rem Starts the app so your phone can open it over Wi-Fi.
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
%PY% scripts\run_for_phone.py
:end
echo.
pause
