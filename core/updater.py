"""Zelf-update: controleer, download en vervang de exe via een helper-script.

Werking:
1. version.txt op de server bevat op regel 1 het nieuwste versienummer en
   optioneel op regel 2 de SHA256-checksum van de exe.
2. Is die versie nieuwer dan de ingebouwde versie, dan wordt de nieuwe exe
   gedownload naar %TEMP% (met checksum-controle).
3. Omdat een draaiende exe zichzelf niet kan overschrijven, start
   apply_and_restart() een klein PowerShell-script dat wacht tot de app
   sluit, de exe vervangt en de app opnieuw start.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta

VERSION_URL = "https://charlesderidder.nl/toolkit/version.txt"
EXE_URL = "https://charlesderidder.nl/toolkit/CdRToolkit.exe"

_HEADERS = {"User-Agent": "CdRToolkit-Updater"}


def _haal(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _met_cb(url: str) -> str:
    """Voeg een cache-buster toe zodat proxies/CDN's geen verouderd bestand geven."""
    scheiding = "&" if "?" in url else "?"
    return f"{url}{scheiding}t={int(time.time())}"


def _parse_versie(tekst: str):
    """'1.0.3' -> (1, 0, 3); None bij ongeldige invoer."""
    try:
        return tuple(int(p) for p in tekst.strip().split("."))
    except ValueError:
        return None


def huidige_versie():
    from version import __version__
    return _parse_versie(__version__)


def is_frozen() -> bool:
    """True als we als PyInstaller-exe draaien (zelf-update werkt alleen dan)."""
    return bool(getattr(sys, "frozen", False))


def check_for_update(log):
    """
    Controleer of er een nieuwere versie is.
    Geeft (versie_tekst, sha256_of_None) terug, of None als er geen update is.
    """
    try:
        regels = _haal(_met_cb(VERSION_URL), timeout=15).decode(
            "utf-8", "replace").splitlines()
    except Exception as exc:
        log(f"Updatecontrole mislukt (geen verbinding?): {exc}", "WARNING")
        return None
    if not regels:
        return None
    nieuw = _parse_versie(regels[0])
    if nieuw is None:
        log("Updatecontrole: ongeldige versieinfo op de server.", "WARNING")
        return None
    sha = regels[1].strip().lower() if len(regels) > 1 else None
    if nieuw > huidige_versie():
        return (regels[0].strip(), sha)
    return None


def _sha256(pad: str) -> str:
    h = hashlib.sha256()
    with open(pad, "rb") as f:
        for blok in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blok)
    return h.hexdigest()


def download_update(log, verwachte_sha: str | None = None) -> str | None:
    """Download de nieuwe exe naar %TEMP%; verifieer de checksum indien opgegeven."""
    doel = os.path.join(tempfile.gettempdir(), "CdRToolkit_update.exe")
    log(f"Update downloaden van {EXE_URL} ...")
    try:
        req = urllib.request.Request(_met_cb(EXE_URL), headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp, open(doel, "wb") as f:
            totaal = int(resp.headers.get("Content-Length") or 0)
            gelezen, gemeld = 0, -1
            while True:
                blok = resp.read(256 * 1024)
                if not blok:
                    break
                f.write(blok)
                gelezen += len(blok)
                if totaal:
                    pct = gelezen * 100 // totaal
                    if pct // 10 > gemeld:  # log elke 10%
                        gemeld = pct // 10
                        log(f"Downloaden: {pct}%")
    except Exception as exc:
        log(f"Downloaden mislukt: {exc}", "ERROR")
        return None

    if verwachte_sha:
        echte = _sha256(doel)
        if echte != verwachte_sha.lower():
            log("Update verwijderd: checksum komt niet overeen!", "ERROR")
            log(f"  verwacht  : {verwachte_sha.lower()}")
            log(f"  gedownload: {echte}")
            log("Oorzaak: exe en version.txt op de server horen bij "
                "verschillende builds. Upload ze opnieuw als paar.", "WARNING")
            os.remove(doel)
            return None
        log("Checksum geverifieerd.", "SUCCESS")
    return doel


# Helper-script: wacht tot de exe vrijkomt, vervang hem en start opnieuw.
# Elke stap wordt gelogd zodat problemen te diagnosticeren zijn.
# Let op: accolades zijn verdubbeld vanwege str.format().
_UPDATER_PS1 = r"""
$ErrorActionPreference = 'Continue'
$src = '{src}'
$dst = '{dst}'
$log = '{log}'
function WL([string]$m) {{
    $t = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $log -Value "$t  $m" -Encoding UTF8
}}
WL '--- update-helper gestart ---'
WL "src: $src"
WL "dst: $dst"
Start-Sleep -Seconds 1

# Wacht tot de draaiende exe het bestand heeft vrijgegeven (max ~60 sec)
$vrij = $false
for ($i = 0; $i -lt 120; $i++) {{
    try {{
        $fs = [System.IO.File]::Open($dst, 'Open', 'ReadWrite', 'None')
        $fs.Close()
        $vrij = $true
        break
    }} catch {{ Start-Sleep -Milliseconds 500 }}
}}
WL "exe vrijgegeven voor vervanging: $vrij"

try {{
    Copy-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop
    WL 'nieuwe versie gekopieerd'
}} catch {{
    WL ("FOUT bij kopieren: " + $_.Exception.Message)
}}

if (Test-Path -LiteralPath $dst) {{
    WL ("dst aanwezig, grootte: " + (Get-Item -LiteralPath $dst).Length)
}} else {{
    WL 'dst ONTBREEKT na kopie'
}}

# Start de nieuwe versie (met één retry na 2 seconden)
try {{
    Start-Process -FilePath $dst -WorkingDirectory (Split-Path -LiteralPath $dst) -ErrorAction Stop
    WL 'nieuwe versie gestart'
}} catch {{
    WL ("FOUT bij starten: " + $_.Exception.Message)
    Start-Sleep -Seconds 2
    try {{
        Start-Process -FilePath $dst -ErrorAction Stop
        WL 'nieuwe versie gestart (2e poging)'
    }} catch {{
        WL ("FOUT bij starten (2e poging): " + $_.Exception.Message)
    }}
}}
# Ruim op: de eenmalige geplande taak en dit script mogen weg
schtasks /Delete /TN '{taak}' /F 2>$null | Out-Null
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""


def _gebruikerscontext_nodig(pad: str) -> bool:
    """
    True als de exe op een netwerkshare of in een gebruikersmap staat:
    dan moet de update-helper ONGELEVIGD draaien (een verhoogd proces heeft
    geen toegang tot netwerkshares van de gebruikerssessie).
    """
    if pad.startswith("\\\\"):
        return True
    p = pad.lower()
    beschermd = ("c:\\program files", "c:\\windows")
    return not any(p.startswith(b) for b in beschermd)


_TAAK_NAAM = "CdRToolkitUpdate"


def apply_and_restart(log, nieuw_bestand: str) -> bool:
    """Vervang de huidige exe door de download en start opnieuw.

    De helper wordt als eenmalige geplande taak gestart: die overleeft het
    sluiten van de app gegarandeerd. Bij een netwerkshare/gebruikersmap draait
    de taak ongelevigd (LIMITED) zodat de share bereikbaar blijft; bij
    beschermde mappen (Program Files e.d.) verhoogd (HIGHEST).
    """
    if not is_frozen():
        log("Zelf-update werkt alleen vanuit de exe, niet vanuit broncode.", "WARNING")
        return False

    from core.logger import data_dir
    log_map = os.path.join(data_dir(), "logs")
    os.makedirs(log_map, exist_ok=True)
    helper_log = os.path.join(log_map, "updater.log")

    script = _UPDATER_PS1.format(
        src=nieuw_bestand.replace("'", "''"),
        dst=sys.executable.replace("'", "''"),
        log=helper_log.replace("'", "''"),
        taak=_TAAK_NAAM)
    script_pad = os.path.join(tempfile.gettempdir(), "charles_onderhoud_updater.ps1")
    # utf-8-sig (met BOM) zodat PowerShell 5.1 speciale tekens in paden goed leest
    with open(script_pad, "w", encoding="utf-8-sig") as f:
        f.write(script)

    dst = sys.executable
    rl = "LIMITED" if _gebruikerscontext_nodig(dst) else "HIGHEST"
    tr = (f'powershell.exe -NoProfile -ExecutionPolicy Bypass '
          f'-WindowStyle Hidden -File "{script_pad}"')
    # /ST moet in de toekomst liggen (anders XML-fout bij /Create); /Z is niet
    # geldig samen met /IT, dus ruimt het script de taak zelf op.
    st = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    maak = subprocess.run(
        ["schtasks", "/Create", "/TN", _TAAK_NAAM, "/TR", tr,
         "/SC", "ONCE", "/ST", st, "/RL", rl, "/IT", "/F"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    gestart = False
    if maak.returncode == 0:
        run = subprocess.run(["schtasks", "/Run", "/TN", _TAAK_NAAM],
                             capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        gestart = run.returncode == 0
        if not gestart:
            log(f"Geplande taak starten mislukt: "
                f"{run.stderr or run.stdout}", "WARNING")
    else:
        log(f"Plannen van de update-helper mislukt: "
            f"{maak.stderr or maak.stdout}", "WARNING")

    if not gestart:
        # Fallback: helper als losgekoppeld proces starten; dat overleeft
        # het sluiten van de app evengoed (werkt zonder Taakplanner).
        log("Terugval: helper wordt direct als los proces gestart.", "INFO")
        try:
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", script_pad],
                creationflags=(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP |
                               subprocess.CREATE_NO_WINDOW),
                close_fds=True)
        except OSError as exc:
            log(f"Starten van de update-helper mislukt: {exc}", "ERROR")
            return False

    log("De app sluit nu; de update wordt geïnstalleerd en de app start "
        "opnieuw.", "STEP")
    log("Start de app na ~30 seconden niet vanzelf? Open dan zelf het "
        "bestand opnieuw (de update is dan al verwerkt).", "INFO")
    log(f"Helper-logboek: {helper_log}", "INFO")
    return True
