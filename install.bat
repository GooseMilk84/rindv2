@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Rind installer
echo.
echo   Rind  -  drag a file, tap Shift, drop it on a slice
echo   Everything installs into this folder and runs locally.
echo.

set "PY="
for %%V in (3.12 3.13 3.11) do call :trypy %%V
if not defined PY call :trypath
if not defined PY goto nopython
echo   Using %PY%

if not exist ".venv\Scripts\python.exe" (
  echo   Creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
set "VPY=.venv\Scripts\python.exe"
echo   Installing packages (first run takes a few minutes)...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo   Optional extras (RAW photos, QR codes, .zst)...
for %%P in (rawpy zxing-cpp zstandard) do "%VPY%" -m pip install %%P >nul 2>&1
"%VPY%" -m rind.setup_shortcuts
echo.
echo   Done. Rind is starting - look for the orange wheel in the system tray.
echo   Drag any file in Explorer, tap Shift (Ctrl = tools, Alt = actions), drop on a slice.
echo.
start "" ".venv\Scripts\pythonw.exe" "%~dp0run_rind.pyw"
timeout /t 6 >nul
exit /b 0

:trypy
if defined PY exit /b 0
py -%1 -c "import sys" >nul 2>&1 && set "PY=py -%1"
exit /b 0

:trypath
python -c "import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
exit /b 0

:nopython
echo   Python 3.11 - 3.13 wasn't found.
echo   Easiest fix - open a terminal and run:   winget install Python.Python.3.12
echo   or grab it from python.org - tick "Add python.exe to PATH" - then run this again.
pause
exit /b 1

:fail
echo.
echo   Something went wrong above. Screenshot it and send it over.
pause
exit /b 1
