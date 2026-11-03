@echo off
setlocal
cd /d "%~dp0"
echo ==================================================
echo   HeadNav setup  (only needs to run once)
echo ==================================================
echo.

rem ---- find a Python version MediaPipe supports (3.10 - 3.13) ----
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Could not find Python 3.10 - 3.13.
    echo.
    echo Install Python 3.12 from https://www.python.org/downloads/
    echo IMPORTANT: on the first installer screen tick "Add python.exe to PATH".
    echo Then run setup.bat again.
    echo.
    pause
    exit /b 1
)
echo Using: %PY%
%PY% --version

rem ---- virtual environment ----
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Creating virtual environment...
    %PY% -m venv .venv || goto :fail
)

echo.
echo Installing libraries (this can take a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

rem ---- face model ----
if not exist "face_landmarker.task" (
    echo.
    echo Downloading the face model...
    curl.exe -L --fail -o face_landmarker.task "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
    if not exist "face_landmarker.task" (
        powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task' -OutFile 'face_landmarker.task'"
    )
    if not exist "face_landmarker.task" goto :fail
)

echo.
echo Running self-tests...
".venv\Scripts\python.exe" -m unittest test_core || goto :fail

echo.
echo ==================================================
echo   Setup complete!  Double-click run.bat to start.
echo ==================================================
pause
exit /b 0

:fail
echo.
echo Setup failed - scroll up to see the error message.
echo Copy the error and ask for help.
pause
exit /b 1
