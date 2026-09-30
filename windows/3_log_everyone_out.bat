@echo off
title BOQ ^& Store app - log everyone out
rem Test shortcut: ends every login session at once, exactly as the
rem 30-minute idle timeout would, so the offline test needs no waiting.
cd /d "%~dp0.."
if not exist venv\Scripts\python.exe (
    echo Setup hasn't been run yet. Double-click 1_setup.bat first.
    goto :end
)
venv\Scripts\python.exe manage.py shell -c "from django.contrib.sessions.models import Session; n = Session.objects.count(); Session.objects.all().delete(); print(n, 'session(s) ended. Everyone must log in again.')"
:end
echo.
pause
