@echo off
title BOQ ^& Store app - convert a BOQ for import
rem Drag a contract BOQ .xlsx file onto this file. It writes
rem "<name> - import.xlsx" next to it and checks every bill total.
cd /d "%~dp0.."
if "%~1"=="" (
    echo Drag your BOQ Excel file onto 4_convert_boq.bat to convert it.
    goto :end
)
if not exist venv\Scripts\python.exe (
    echo Setup hasn't been run yet. Double-click 1_setup.bat first.
    goto :end
)
venv\Scripts\python.exe scripts\convert_boq_layout.py "%~1"
:end
echo.
pause
