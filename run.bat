@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo HeadNav isn't set up yet. Double-click setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" headnav.py
if errorlevel 1 pause
