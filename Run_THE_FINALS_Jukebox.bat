@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo First run detected. Starting setup...
    call Install_and_Run_THE_FINALS.bat
    exit /b %errorlevel%
)
start "" ".venv\Scripts\pythonw.exe" finals_jukebox.py
