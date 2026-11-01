@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_vps_dashboard.ps1"
if errorlevel 1 pause
