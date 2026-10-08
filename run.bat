@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  python -m venv .venv || py -m venv .venv
)
if not exist .venv\Scripts\activate.bat (
  echo Python was not found. Install Python 3.12 from python.org, tick "Add to PATH", then run this again.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
pip install -q -r requirements-dev.txt
set DEMO_MODE=1
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000"
echo Dashboard running at http://127.0.0.1:5000  (press Ctrl+C to stop)
flask --app app run
