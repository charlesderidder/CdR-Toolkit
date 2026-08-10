@echo off
rem Bouwt CdRToolkit.exe version.txt
setlocal
cd /d "%~dp0"

echo [1/2] Afhankelijkheden controleren/installeren...
python -m pip install -r requirements.txt

echo [2/4] CdRToolkit.exe bouwen...
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
    --name "CdRToolkit" install.py
pause