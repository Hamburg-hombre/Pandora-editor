@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 ge_modtool.py) || (python ge_modtool.py)
if errorlevel 1 (
  echo.
  echo The tool did not start. Read the message above - you may need:  pip install pillow numpy
  pause
)
