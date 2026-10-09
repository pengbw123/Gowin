@echo off
setlocal
cd /d "%~dp0"
:menu
cls
echo ================================================
echo Gowin digital instrument tools
echo ================================================
echo [1] FPGA harmonic editor and UART downloader
echo [2] Phase 1 - single-note spectrum analyser
echo [3] Phase 2 - C2/C3/C4/C6 timbre builder
echo [4] Install or repair Python dependencies
echo [0] Exit
echo.
set /p tool_choice=Select: 
if "%tool_choice%"=="1" call run_harmonic_editor.bat
if "%tool_choice%"=="2" call run_instrument_analyzer.bat
if "%tool_choice%"=="3" call run_timbre_builder.bat
if "%tool_choice%"=="4" call install_python_requirements.bat
if "%tool_choice%"=="0" exit /b 0
goto menu
