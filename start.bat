@echo off
setlocal EnableExtensions

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3.11+ and try again.
    pause
    exit /b 1
  )
  set "PY=py -3"
) else (
  set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo ERROR: Failed to create .venv
    pause
    exit /b 1
  )
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: pip install failed
  pause
  exit /b 1
)

set PYTHONDONTWRITEBYTECODE=1
echo Starting ShitPostGateWayBot...
".venv\Scripts\python.exe" -m bot
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo Bot exited with code %EXITCODE%.
  pause
)

exit /b %EXITCODE%
