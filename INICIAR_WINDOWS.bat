@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (py -m venv .venv)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if "%SECRET_KEY%"=="" echo ATENCAO: Defina SECRET_KEY, ADMIN_USER e ADMIN_PASSWORD antes de disponibilizar o site.
python app.py
pause
