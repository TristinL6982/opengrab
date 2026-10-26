@echo off
setlocal
cd /d "%~dp0"
py -3.12 -m PyInstaller --clean --noconfirm --distpath release OpenGrab.spec
if errorlevel 1 exit /b %errorlevel%
echo Build complete: build\release\OpenGrab\OpenGrab.exe
