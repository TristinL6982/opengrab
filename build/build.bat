@echo off
setlocal
cd /d "%~dp0"
"%~dp0..\.venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm --distpath release OpenGrab.spec
if errorlevel 1 exit /b %errorlevel%
echo Build complete: build\release\OpenGrab\OpenGrab.exe
