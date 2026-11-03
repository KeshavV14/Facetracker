@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo HeadNav isn't set up yet. Double-click setup.bat first.
    pause
    exit /b 1
)
echo Target test - measures click speed and accuracy.
echo Use a label like: mouse, headnav, headnav_ema, headnav_nofilter
echo.
set "LABEL="
set /p LABEL=Label for this run:
if "%LABEL%"=="" set "LABEL=run"
".venv\Scripts\python.exe" target_test.py "%LABEL%"
pause
