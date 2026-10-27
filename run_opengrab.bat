@echo off
setlocal
cd /d "%~dp0"
if exist "build\release\OpenGrab\OpenGrab.exe" (
  start "" "build\release\OpenGrab\OpenGrab.exe" %*
) else (
  ".venv\Scripts\python.exe" -m opengrab %*
)
