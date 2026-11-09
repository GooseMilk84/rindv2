@echo off
cd /d "%~dp0"
echo Removing Rind's right-click entry, Send To, startup and Start Menu shortcut...
".venv\Scripts\python.exe" run_rind.pyw --quit >nul 2>&1
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m rind.setup_shortcuts --remove
echo.
echo Done. Delete this folder to finish (settings live in %%APPDATA%%\Rind).
pause
