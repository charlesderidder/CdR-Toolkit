"""Scripts-bibliotheek: gedeelde scripts downloaden van charlesderidder.nl.

Server-layout:
    https://charlesderidder.nl/scripts/scripts.json  (manifest)
    https://charlesderidder.nl/scripts/<pad>         (scriptbestanden)

Manifest (scripts.json):
    {"categorieen": {
        "PowerShell": [{"naam": "info.ps1", "pad": "PowerShell/info.ps1",
                        "beschrijving": "..."}],
        "MikroTik": [...], "Proxmox": [...], "Docker": [...],
        "Batchscripts": [...]}}

Het manifest wordt gecached in Documenten\\CharlesOnderhoud\\scripts, zodat de
bibliotheek ook offline te bekijken is. Downloads komen in
Documenten\\CharlesOnderhoud\\scripts\\<categorie>\\.
"""
import json
import os
import time
import urllib.request

BASE_URL = "https://charlesderidder.nl/toolkit/scripts"
_HEADERS = {"User-Agent": "CdRToolkit-Scripts"}


def map() -> str:
    """Lokale scriptmap: Documenten\\CharlesOnderhoud\\scripts."""
    from core.logger import data_dir
    pad = os.path.join(data_dir(), "scripts")
    os.makedirs(pad, exist_ok=True)
    return pad


def _haal(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def laad_cache() -> dict:
    """Manifest uit de lokale cache (doet geen netwerkverkeer)."""
    try:
        with open(os.path.join(map(), "scripts.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"categorieen": {}}


def ververs(log) -> bool:
    """Download het manifest opnieuw en sla het op in de cache."""
    try:
        data = _haal(f"{BASE_URL}/scripts.json?t={int(time.time())}")
        m = json.loads(data.decode("utf-8", "replace"))
    except Exception as exc:
        log(f"Manifest downloaden mislukt: {exc}", "ERROR")
        return False
    with open(os.path.join(map(), "scripts.json"), "wb") as f:
        f.write(data)
    cats = m.get("categorieen", {})
    aantal = sum(len(v) for v in cats.values())
    log(f"Scripts-bibliotheek bijgewerkt: {len(cats)} categorieën, "
        f"{aantal} scripts.", "SUCCESS")
    return True


def categorieen() -> list:
    """Gesorteerde categorienamen uit de cache."""
    return sorted(laad_cache().get("categorieen", {}).keys())


def scripts(categorie: str) -> list:
    """Manifest-entries van één categorie uit de cache."""
    return laad_cache().get("categorieen", {}).get(categorie, [])


def inhoud(log, categorie: str, entry: dict):
    """Tekst van een script: lokaal uit de cache-map als gedownload, anders
    opgehaald van de server. Geeft de tekst of None bij een fout."""
    pad = entry.get("pad", "")
    if not pad:
        log("Ongeldige manifest-entry (geen pad).", "ERROR")
        return None
    lokaal = os.path.join(map(), categorie, os.path.basename(pad))
    try:
        if os.path.exists(lokaal):
            with open(lokaal, encoding="utf-8", errors="replace") as f:
                return f.read()
        return _haal(f"{BASE_URL}/{pad}", timeout=30).decode("utf-8", "replace")
    except Exception as exc:
        log(f"Script ophalen mislukt: {exc}", "ERROR")
        return None


def synchroniseer_alles(log) -> bool:
    """Ververs het manifest en download ALLE scripts (nieuw én gewijzigd)."""
    log("=== Scripts synchroniseren met server ===", "STEP")
    if not ververs(log):
        return False
    cats = categorieen()
    n, fouten = 0, 0
    for cat in cats:
        for e in scripts(cat):
            if download(log, cat, e):
                n += 1
            else:
                fouten += 1
    log(f"Synchronisatie klaar: {n} scripts in {len(cats)} categorieën"
        + (f", {fouten} mislukt." if fouten else "."),
        "SUCCESS" if not fouten else "WARNING")
    return fouten == 0


def download(log, categorie: str, entry: dict):
    """Download één script naar de lokale scriptmap. Geeft het pad of None."""
    pad = entry.get("pad", "")
    naam = entry.get("naam") or os.path.basename(pad)
    if not pad:
        log(f"Ongeldige manifest-entry voor '{naam}' (geen pad).", "ERROR")
        return None
    try:
        data = _haal(f"{BASE_URL}/{pad}", timeout=30)
    except Exception as exc:
        log(f"Downloaden van '{naam}' mislukt: {exc}", "ERROR")
        return None
    doel_map = os.path.join(map(), categorie)
    os.makedirs(doel_map, exist_ok=True)
    doel = os.path.join(doel_map, os.path.basename(pad))
    try:
        with open(doel, "wb") as f:
            f.write(data)
    except OSError as exc:
        log(f"Opslaan van '{naam}' mislukt: {exc}", "ERROR")
        return None
    log(f"Opgeslagen: {doel}", "SUCCESS")
    return doel
