@echo off
setlocal
cd /d "%~dp0\.."
python apps\fm_editor.py
if errorlevel 1 pause

