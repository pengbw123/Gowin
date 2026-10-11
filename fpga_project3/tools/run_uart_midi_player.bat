@echo off
setlocal
cd /d "%~dp0"
python uart_midi_player.py
if errorlevel 1 pause

