"""Automatische herstelacties voor Winget en Windows Update."""
from core import runner

# Reset van de Windows Update-componenten: services stoppen,
# cachemappen hernoemen (Windows maakt ze zelf opnieuw aan), services starten.
WU_RESET_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Write-Output "[Herstel] Windows Update-services stoppen..."
Stop-Service -Name wuauserv, cryptSvc, bits, msiserver -Force
Start-Sleep -Seconds 2
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$sd = Join-Path $env:SystemRoot 'SoftwareDistribution'
$cr = Join-Path $env:SystemRoot 'System32\catroot2'
if (Test-Path $sd) {
    Rename-Item $sd "SoftwareDistribution.bak.$stamp"
    Write-Output "[Herstel] SoftwareDistribution-cache gereset."
}
if (Test-Path $cr) {
    Rename-Item $cr "catroot2.bak.$stamp"
    Write-Output "[Herstel] catroot2 gereset."
}
Write-Output "[Herstel] Services opnieuw starten..."
Start-Service -Name bits, cryptSvc, wuauserv
Write-Output "[Herstel] Windows Update-herstel voltooid."
"""


def repair_winget(log) -> bool:
    """Herstel de winget-bronnen (source reset + update)."""
    log("--- Winget-herstel gestart ---", "STEP")
    c1 = runner.run_stream(["winget", "source", "reset", "--force"], log, cr_split=True)
    c2 = runner.run_stream(["winget", "source", "update"], log, cr_split=True)
    ok = (c1 == 0 and c2 == 0)
    if ok:
        log("Winget-herstel voltooid.", "SUCCESS")
    else:
        log(f"Winget-herstel mogelijk onvolledig (codes {c1}/{c2}).", "WARNING")
    return ok


def repair_windows_update(log) -> bool:
    """Reset Windows Update: services, SoftwareDistribution en catroot2."""
    log("--- Windows Update-herstel gestart ---", "STEP")
    code = runner.run_powershell(WU_RESET_SCRIPT, log)
    if code == 0:
        log("Windows Update-herstel voltooid.", "SUCCESS")
        return True
    log(f"Windows Update-herstel mislukt (code {code}).", "ERROR")
    return False
