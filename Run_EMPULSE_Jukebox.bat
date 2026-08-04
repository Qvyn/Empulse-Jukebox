@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    call Install_and_Run.bat
    exit /b %errorlevel%
)
start "" ".venv\Scripts\pythonw.exe" empulse_jukebox.py

