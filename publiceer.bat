@echo off
rem Maakt dist\version.txt (versie + sha256) om samen met de exe te uploaden.
rem Gebruik: 1) verhoog versie in version.py  2) build_exe.bat  3) publiceer.bat
setlocal
cd /d "%~dp0"

if not exist "dist\CdRToolkit.exe" (
    echo dist\CdRToolkit.exe niet gevonden. Bouw eerst met build_exe.bat.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('python -c "from version import __version__; print(__version__)"') do set "VERSIE=%%v"
for /f "delims=" %%h in ('powershell -NoProfile -Command "(Get-FileHash 'dist\CdRToolkit.exe' -Algorithm SHA256).Hash.ToLower()"') do set "HASH=%%h"

(
echo %VERSIE%
echo %HASH%
) > "dist\version.txt"

echo.
echo version.txt aangemaakt voor versie %VERSIE%:
type "dist\version.txt"
echo.
echo Upload nu deze twee bestanden naar https://charlesderidder.nl/toolkit/ :
echo   dist\CdRToolkit.exe
echo   dist\version.txt
echo.
pause
