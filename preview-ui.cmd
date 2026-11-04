@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv-ui\Scripts\python.exe" goto existing_environment
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 goto python_launcher
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 goto python_command
echo Install Python 3.10 or newer, then double-click this shortcut again.
pause
exit /b 1

:existing_environment
"%~dp0.venv-ui\Scripts\python.exe" "%~dp0preview_ui.py" %*
goto finished

:python_launcher
py -3 "%~dp0preview_ui.py" %*
goto finished

:python_command
python "%~dp0preview_ui.py" %*

:finished
set "preview_exit=%errorlevel%"
if not "%preview_exit%"=="0" (
    echo.
    echo Preview could not start. Check the message above; first setup needs internet access.
    pause
)
exit /b %preview_exit%
