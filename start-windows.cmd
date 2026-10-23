@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if errorlevel 1 goto failed
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
goto install
:usepython
python -c "import sys; assert sys.version_info >= (3,11), 'Install Python 3.11+ and add it to PATH'"
if errorlevel 1 goto failed
if not exist ".venv\Scripts\python.exe" python -m venv .venv
:install
if not exist ".venv\Scripts\python.exe" goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Setup or startup failed. Check the error above and docs\AI_DEPLOY.md.
pause
exit /b 1
