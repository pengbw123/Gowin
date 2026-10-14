@echo off
setlocal
cd /d "%~dp0.."
python -m apps.uart_midi_player
if errorlevel 1 pause

