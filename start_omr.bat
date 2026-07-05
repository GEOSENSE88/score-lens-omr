@echo off
rem == Sannam OMR one-click launcher ==============================
rem Double-click: starts the server (minimized) if not running,
rem then opens the browser. Closing the "OMR server" window stops it.

set "OMR_DIR=%~dp0"
cd /d "%OMR_DIR%"

rem If the server is already up, just open the browser
curl -s -o nul --max-time 2 http://127.0.0.1:5050/ && goto :open

echo Starting OMR scoring server...
set PYTHONIOENCODING=utf-8
start "OMR server (close to stop)" /min cmd /c "python web_app.py"

rem Wait for the server (up to ~15s)
set /a tries=0
:wait
set /a tries+=1
curl -s -o nul --max-time 1 http://127.0.0.1:5050/ && goto :open
if %tries% geq 30 (
    echo Failed to start. Check python and requirements.
    pause
    exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto :wait

:open
start "" http://127.0.0.1:5050/
exit /b 0
