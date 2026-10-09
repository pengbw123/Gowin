@echo off
cd /d "%~dp0"
echo Installing the UART editor and instrument analyser dependencies into this Python environment...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Installation failed. Check the Python/pip installation and network access.
    pause
    exit /b 1
)
echo Installation completed. You can now run run_harmonic_editor.bat or run_instrument_analyzer.bat.
pause
