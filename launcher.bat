@echo off
REM MIMIR launcher.
REM
REM Usage:
REM   - Run this file directly (double-click) to start MIMIR.
REM   - To run MIMIR automatically at sign-in, place a shortcut to this
REM     file in your Startup folder (Win+R, type "shell:startup", Enter).
REM   - You can also pin a shortcut to this file to the taskbar/Start menu
REM     for quick manual launches.

cd /d "%~dp0"

set "PYTHONW=pythonw.exe"
if exist venv\Scripts\pythonw.exe (
    set "PYTHONW=venv\Scripts\pythonw.exe"
)

start "" /D "%~dp0" "%PYTHONW%" "%~dp0main.py"
