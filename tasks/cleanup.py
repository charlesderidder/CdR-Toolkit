"""Opschonen: temp-mappen, Windows Update-cache en prullenbak.

Vergrendelde bestanden (in gebruik) worden geruisloos overgeslagen.
De vrijgemaakte ruimte wordt bijgehouden en gerapporteerd.
"""
import ctypes
import os
import shutil
import tempfile

from core import runner

_SYSTEMROOT = os.environ.get("SystemRoot", r"C:\Windows")

# SHEmptyRecycleBin-vlaggen: geen bevestiging, geen voortgang, geen geluid
_SHERB_FLAGS = 0x0001 | 0x0002 | 0x0004


def _map_grootte(pad: str) -> int:
    """Totale grootte van een map in bytes (fouten worden genegeerd)."""
    totaal = 0
    for root, _dirs, files in os.walk(pad, onerror=lambda e: None):
        for f in files:
            try:
                totaal += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return totaal


def _leeg_map(pad: str, log) -> int:
    """Verwijder de inhoud van een map; geef het aantal vrijgemaakte bytes."""
    vrij = 0
    try:
        entries = os.listdir(pad)
    except OSError as exc:
        log(f"Kan {pad} niet lezen: {exc}", "WARNING")
        return 0
    for naam in entries:
        volledig = os.path.join(pad, naam)
        try:
            if os.path.isfile(volledig) or os.path.islink(volledig):
                grootte = os.path.getsize(volledig)
                os.remove(volledig)
                vrij += grootte
            elif os.path.isdir(volledig):
                grootte = _map_grootte(volledig)
                shutil.rmtree(volledig, ignore_errors=False)
                vrij += grootte
        except OSError:
            pass  # bestand in gebruik of rechtenprobleem -> overslaan
    return vrij


def _formaat(aantal_bytes: int) -> str:
    mb = aantal_bytes / (1024 * 1024)
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def run(log) -> bool:
    """Voer de volledige cleanup uit en rapporteer vrijgemaakte ruimte."""
    log("=== Cleanup (opschonen) ===", "STEP")
    totaal = 0

    # 1) Temp-mappen (gebruiker + systeem)
    doelen = [
        ("Gebruikers-temp", tempfile.gettempdir()),
        ("Windows-temp", os.path.join(_SYSTEMROOT, "Temp")),
    ]
    for naam, pad in doelen:
        if os.path.isdir(pad):
            vrij = _leeg_map(pad, log)
            totaal += vrij
            log(f"{naam}: {_formaat(vrij)} vrijgemaakt.")

    # 2) Windows Update-downloadcache (service eerst stoppen)
    log("Windows Update-cache opschonen (service tijdelijk stoppen)...")
    runner.run_quiet(["net", "stop", "wuauserv"])
    cache = os.path.join(_SYSTEMROOT, "SoftwareDistribution", "Download")
    if os.path.isdir(cache):
        vrij = _leeg_map(cache, log)
        totaal += vrij
        log(f"Update-cache: {_formaat(vrij)} vrijgemaakt.")
    runner.run_quiet(["net", "start", "wuauserv"])

    # 3) Prullenbak legen via de Windows-shell (alle schijven tegelijk)
    try:
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, _SHERB_FLAGS)
        log("Prullenbak geleegd.")
    except OSError as exc:
        log(f"Prullenbak legen mislukt: {exc}", "WARNING")

    log(f"Totaal vrijgemaakt: {_formaat(totaal)}", "SUCCESS")
    return True
