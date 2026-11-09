@echo off
rem Optional: bundle Rind into a standalone folder (dist\Rind\Rind.exe) you can copy anywhere.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run install.bat first.
  pause
  exit /b 1
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install pyinstaller
"%VPY%" -m PyInstaller --noconfirm --clean --windowed --name Rind ^
  --icon rind\assets\rind.ico ^
  --add-data "rind\assets;rind\assets" ^
  --collect-all pymupdf --collect-all pillow_heif --collect-all imageio_ffmpeg ^
  --collect-all pdf2docx --collect-data docx --collect-data pptx --collect-data mammoth ^
  --hidden-import win32com.client --hidden-import pythoncom ^
  run_rind.pyw
echo.
echo Built: dist\Rind\Rind.exe
pause
