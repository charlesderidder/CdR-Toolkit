@echo off
rem Bouwt CdR-Toolkit.exe version.txt
setlocal
cd /d "%~dp0"

echo [1/1] CdR-Toolkit.exe bouwen...
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
    --name "CdR-Toolkit" main.py
pause