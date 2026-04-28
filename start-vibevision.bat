@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap-start.ps1" %*

if errorlevel 1 (
  echo.
  echo VibeVision startup failed. Check the messages above.
  pause
  exit /b %errorlevel%
)

echo.
echo VibeVision startup completed. You can close this window after testing.
pause
