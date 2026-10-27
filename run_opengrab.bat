@echo off
REM OpenGrab Launcher Batch File - Fixed Version
REM Usage: run_opengrab.bat [optional arguments]

echo Launching OpenGrab...
echo.

REM Change to project directory
cd /d "C:\Users\trist\opengrab"

REM Set Python path to ensure we're using the right one
set PYTHONPATH=C:\Users\trist\opengrab\src;%PYTHONPATH%

REM Run the CLI with arguments passed through
echo Starting OpenGrab...
python -c "import sys; sys.path.insert(0, 'src'); from opengrab.cli import main; main()" %*

echo.
echo OpenGrab has finished running.
pause