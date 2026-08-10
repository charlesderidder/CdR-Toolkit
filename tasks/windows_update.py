"""Windows Updates forceren via de ingebouwde Windows Update COM-API.

Gebruikt bewust de COM-API (Microsoft.Update.Session) in plaats van de
PSWindowsUpdate-module: die is niet standaard geïnstalleerd. De COM-API
werkt op elke Windows 10/11-installatie en ondersteunt zoeken, downloaden
en installeren zonder tussenkomst van de gebruiker.
"""
from core import runner

# PowerShell-script: zoek -> download -> installeer. ResultCode:
#   2 = geslaagd, 3 = geslaagd met fouten, 4 = mislukt, 5 = afgebroken
WU_SCRIPT = r"""
Write-Output "[WU] Zoeken naar beschikbare updates (kan enkele minuten duren)..."
try {
    $session  = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
} catch {
    Write-Output "[WU-FOUT] Zoeken mislukt: $($_.Exception.Message)"
    exit 1
}

Write-Output "[WU] $($result.Updates.Count) update(s) gevonden."
if ($result.Updates.Count -eq 0) { exit 0 }

$downloadColl = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $result.Updates) {
    Write-Output "[WU] Gevonden: $($u.Title)"
    try { $u.AcceptEula() } catch {}
    if (-not $u.IsDownloaded) { [void]$downloadColl.Add($u) }
}

if ($downloadColl.Count -gt 0) {
    Write-Output "[WU] Downloaden van $($downloadColl.Count) update(s)..."
    $downloader = $session.CreateUpdateDownloader()
    $downloader.Updates = $downloadColl
    $dres = $downloader.Download()
    Write-Output "[WU] Downloadresultaat: $($dres.ResultCode)"
}

$installColl = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $result.Updates) { if ($u.IsDownloaded) { [void]$installColl.Add($u) } }
if ($installColl.Count -eq 0) {
    Write-Output "[WU-FOUT] Geen updates konden gedownload worden."
    exit 2
}

Write-Output "[WU] Installeren van $($installColl.Count) update(s)..."
$installer = $session.CreateUpdateInstaller()
$installer.Updates = $installColl
$ires = $installer.Install()
Write-Output "[WU] Resultaatcode: $($ires.ResultCode)"
for ($i = 0; $i -lt $installColl.Count; $i++) {
    $r = $ires.GetUpdateResult($i)
    Write-Output ("[WU] Item: {0} -> resultaat {1}" -f $installColl.Item($i).Title, $r.ResultCode)
}
Write-Output "[WU] RebootRequired=$($ires.RebootRequired)"
if ($ires.ResultCode -le 3) { exit 0 } else { exit 3 }
"""


def run(log, auto_reboot: bool = False) -> bool:
    """Forceer detectie, download en installatie van Windows Updates."""
    log("=== Windows Update ===", "STEP")
    state = {"reboot": False}

    def on_line(line):
        log(line)
        if "RebootRequired=True" in line:
            state["reboot"] = True

    # Trigger de WU-service alvast (best effort; mislukt geruisloos)
    runner.run_quiet(["cmd", "/c", "UsoClient", "StartScan"])

    code = runner.run_powershell(WU_SCRIPT, on_line)

    if code == 0:
        log("Windows Update voltooid.", "SUCCESS")
    elif code == 2:
        log("Updates gevonden, maar downloaden is mislukt.", "ERROR")
    else:
        log(f"Windows Update eindigde met een fout (code {code}).", "ERROR")

    # Herstart-afhandeling
    if state["reboot"]:
        if auto_reboot:
            log("Herstart vereist: pc start over 2 minuten opnieuw op. "
                "Annuleren kan met 'shutdown /a'.", "WARNING")
            runner.run_quiet(["shutdown", "/r", "/t", "120",
                              "/c", "Herstart voor Windows updates"])
        else:
            log("Herstart vereist om updates af te ronden (auto-reboot staat uit).", "WARNING")
    return code == 0
