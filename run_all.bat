@echo off
REM Menjalankan seluruh ekosistem (6 server API + 6 dashboard). Tutup jendela / Ctrl+C untuk berhenti.
cd /d "%~dp0"
python -m pip install -r requirements.txt --quiet
if not exist "data\oceanline\fleet.json" python -m data.generate_synthetic
python run_all.py --open
pause
