@echo off
cd /d "%~dp0"
python scripts\setup.py
if errorlevel 1 pause
