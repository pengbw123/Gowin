@echo off
setlocal
cd /d "%~dp0"
python -c "import numpy, scipy, matplotlib, soundfile, sounddevice" >nul 2>nul
if errorlevel 1 (
  echo Python dependencies are missing. Run install_python_requirements.bat first.
  pause
  exit /b 1
)
python instrument_timbre_builder.py
if errorlevel 1 pause
endlocal
