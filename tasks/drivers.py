"""Driver- en firmware-updates.

Strategie:
1. Detecteer OEM-update tools (Dell Command Update, HP Image Assistant, ...).
   Die leveren ook firmware/BIOS-updates en zijn daarvoor de beste bron.
2. Geen (bruikbare) OEM-tool aanwezig? Val terug op Windows Update,
   dat ook gecertificeerde driverupdates distribueert.
"""
import os

from core import runner

# Bekende OEM-tools. 'args=None' betekent: alleen detecteren, geen stille CLI.
OEM_TOOLS = [
    {
        "naam": "Dell Command Update",
        "paden": [
            r"C:\Program Files\Dell\CommandUpdate\dcu-cli.exe",
            r"C:\Program Files (x86)\Dell\CommandUpdate\dcu-cli.exe",
        ],
        "args": ["/applyUpdates", "-silent", "-reboot=disable"],
    },
    {
        "naam": "HP Image Assistant",
        "paden": [
            r"C:\Program Files\HP\HP Image Assistant\HPImageAssistant.exe",
            r"C:\Program Files (x86)\HP\HP Image Assistant\HPImageAssistant.exe",
        ],
        "args": ["/Operation:Analyze", "/Category:All", "/Selection:All",
                 "/Action:Install", "/Silent", "/ReportFolder:C:\\HPIA\\Reports"],
    },
    {
        "naam": "Lenovo System Update",
        "paden": [r"C:\Program Files (x86)\Lenovo\System Update\tvsu.exe"],
        "args": None,  # geen betrouwbare volledig stille CLI -> alleen melden
    },
]

# Windows Update COM-zoekopdracht, beperkt tot drivers
PS_DRIVER_SCRIPT = r"""
Write-Output "[DRV] Zoeken naar driver-updates via Windows Update..."
try {
    $session  = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result   = $searcher.Search("IsInstalled=0 and IsHidden=0 and Type='Driver'")
} catch {
    Write-Output "[DRV-FOUT] Zoeken mislukt: $($_.Exception.Message)"
    exit 1
}
Write-Output "[DRV] $($result.Updates.Count) driver-update(s) gevonden."
if ($result.Updates.Count -eq 0) { exit 0 }

$coll = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $result.Updates) {
    Write-Output "[DRV] Gevonden: $($u.Title)"
    try { $u.AcceptEula() } catch {}
    [void]$coll.Add($u)
}
$downloader = $session.CreateUpdateDownloader()
$downloader.Updates = $coll
Write-Output "[DRV] Downloaden..."
[void]$downloader.Download()

$installColl = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $result.Updates) { if ($u.IsDownloaded) { [void]$installColl.Add($u) } }
if ($installColl.Count -eq 0) { Write-Output "[DRV] Niets te installeren."; exit 0 }

Write-Output "[DRV] Installeren van $($installColl.Count) driver(s)..."
$installer = $session.CreateUpdateInstaller()
$installer.Updates = $installColl
$ires = $installer.Install()
Write-Output "[DRV] Resultaatcode: $($ires.ResultCode)"
Write-Output "[DRV] RebootRequired=$($ires.RebootRequired)"
if ($ires.ResultCode -le 3) { exit 0 } else { exit 3 }
"""


def _vind_oem_tool():
    """Geef het eerste gevonden OEM-tool terug als dict, anders None."""
    for tool in OEM_TOOLS:
        for pad in tool["paden"]:
            if os.path.isfile(pad):
                return {"naam": tool["naam"], "exe": pad, "args": tool["args"]}
    return None


def run(log) -> bool:
    """Voer driver/firmware-updates uit. Geeft True terug bij succes."""
    log("=== Drivers & firmware ===", "STEP")

    tool = _vind_oem_tool()
    if tool:
        if tool["args"]:
            log(f"OEM-tool gevonden: {tool['naam']}. Updates worden gestart...")
            code = runner.run_stream([tool["exe"], *tool["args"]], log)
            if code in (0, 2):  # 2 = reboot nodig bij o.a. Dell
                log(f"{tool['naam']} voltooid.", "SUCCESS")
                return True
            log(f"{tool['naam']} gaf code {code}; val terug op Windows Update.", "WARNING")
        else:
            log(f"{tool['naam']} gedetecteerd (geen stille CLI-modus). "
                "Windows Update wordt gebruikt.", "INFO")
    else:
        log("Geen OEM-update-tool gevonden. Windows Update wordt gebruikt.", "INFO")

    code = runner.run_powershell(PS_DRIVER_SCRIPT, log)
    if code == 0:
        log("Driver-updates voltooid.", "SUCCESS")
        return True
    log(f"Driver-updates mislukt (code {code}).", "ERROR")
    return False
