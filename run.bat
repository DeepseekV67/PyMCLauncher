@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0launcher.py"
) else (
  python "%~dp0launcher.py"
)
