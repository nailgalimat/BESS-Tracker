@echo off
echo Starting BESS Tracker Sync API...
cd /d "%~dp0"
pip install -r requirements.txt -q
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
