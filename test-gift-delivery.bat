@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
set PYTHONUTF8=1
".venv\Scripts\python.exe" scripts\test_gift_delivery.py %*
set "test_exit=%errorlevel%"
pause
exit /b %test_exit%
