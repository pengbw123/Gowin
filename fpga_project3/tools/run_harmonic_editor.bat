@echo off
cd /d "%~dp0"
python -c "import serial" >nul 2>&1
if errorlevel 1 (
    echo The current Python does not have pyserial.
    echo Run install_python_requirements.bat once, then reopen this tool.
    pause
    exit /b 1
)
python harmonic_editor.py
pause
