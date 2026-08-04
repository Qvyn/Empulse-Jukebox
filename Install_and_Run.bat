@echo off
setlocal
title EMPULSE Jukebox Setup
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python was not found.
    echo Install Python from https://www.python.org/downloads/windows/
    echo During setup, enable "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating EMPULSE Jukebox environment...
    py -m venv .venv
    if errorlevel 1 goto :failed
)

echo Installing or updating the audio interface...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo Starting EMPULSE Jukebox...
start "" ".venv\Scripts\pythonw.exe" empulse_jukebox.py
exit /b 0

:failed
echo.
echo Setup failed. Copy the text above and send it back for troubleshooting.
pause
exit /b 1

