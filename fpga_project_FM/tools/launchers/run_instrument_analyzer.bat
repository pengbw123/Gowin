@echo off
cd /d "%~dp0.."
python -c "import numpy, scipy, soundfile, sounddevice, matplotlib" >nul 2>&1
if errorlevel 1 (
    echo The spectrum analyser dependencies are not installed in this Python.
    echo Run launchers\install_python_requirements.bat once, then reopen this tool.
    pause
    exit /b 1
)
python -m apps.instrument_spectrum_analyzer
pause
