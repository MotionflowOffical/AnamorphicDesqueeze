@echo off
setlocal
cd /d "%~dp0"
py -3 -m pip install --upgrade pyinstaller
py -3 -m PyInstaller --noconfirm --clean --windowed --name AnamorphicDNGBatch app.py
if errorlevel 1 pause

echo.
echo Built in dist\AnamorphicDNGBatch\
echo Keep the tools folder next to the EXE if you want bundled/local ExifTool.
pause
