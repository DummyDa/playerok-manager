@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto error
)
".venv\Scripts\python.exe" -c "import curl_cffi" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
  if errorlevel 1 goto error
)
".venv\Scripts\python.exe" server.py --open
if errorlevel 1 goto error
exit /b 0
:error
echo Failed to start Playerok Browser.
pause
exit /b 1
