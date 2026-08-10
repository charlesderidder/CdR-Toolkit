"""Winget-updates: alle apps stilletjes bijwerken, met retry en auto-herstel."""
from core import runner
from tasks import repair

# Exact het commando uit de specificatie
WINGET_CMD = [
    "winget", "upgrade", "--all", "--silent",
    "--accept-source-agreements", "--accept-package-agreements",
]

# Exitcodes die we als 'geslaagd' beschouwen:
#   0             = alles bijgewerkt (of niets te doen bij nieuwere winget)
#   -1978335212   = 0x8A150014: geen toepasselijke upgrades gevonden
#   -1978335189   = 0x8A15002B: update niet toepasbaar / niets te doen
OK_CODES = {0, -1978335212, -1978335189}

_SPINNER = set("-\\|/")
_BLOCK_CHARS = "█▓▒░■"


def _filter(line: str) -> bool:
    """Filter spinner- en voortgangsbalk-regels uit de winget-output."""
    s = line.strip()
    if not s:
        return False
    if all(c in _SPINNER for c in s):          # spinner: - \ | /
        return False
    if sum(s.count(c) for c in _BLOCK_CHARS) > 3:  # voortgangsbalk
        return False
    return True


def _is_unknown_option(lines) -> bool:
    """Herken of winget klaagt over een onbekende optie (NL of EN)."""
    markers = ("unrecognized", "unknown argument", "niet-herkend", "onbekende optie")
    return any(m in l for l in lines for m in markers)


def _run_once(log, args, lines_seen) -> int:
    """Voer winget één keer uit en log gefilterde, ontdubbelde regels."""
    last = {"value": None}

    def on_line(line):
        if not _filter(line) or line == last["value"]:
            return
        last["value"] = line
        lines_seen.append(line.lower())
        log(line)

    return runner.run_stream(args, on_line, cr_split=True)


def run(log, extra_args=None) -> bool:
    """Voer winget-updates uit. Geeft True terug bij succes."""
    log("=== App-updates (Winget) ===", "STEP")

    # Controleer of winget überhaupt aanwezig is
    if runner.run_quiet(["winget", "--version"]) != 0:
        log("Winget is niet gevonden op dit systeem.", "ERROR")
        log("Installeer 'App Installer' via de Microsoft Store en probeer opnieuw.", "INFO")
        return False

    args = list(WINGET_CMD) + list(extra_args or [])
    lines_seen: list = []

    # Poging 1
    code = _run_once(log, args, lines_seen)

    # Oudere winget kent mogelijk een extra optie niet -> opnieuw zonder extra's
    if code not in OK_CODES and extra_args and _is_unknown_option(lines_seen):
        log("Deze winget-versie kent een optie niet; opnieuw zonder extra opties.", "WARNING")
        lines_seen.clear()
        code = _run_once(log, WINGET_CMD, lines_seen)

    if code in OK_CODES:
        log("Winget-updates voltooid.", "SUCCESS")
        return True

    # Mislukt -> bronnen herstellen en één keer opnieuw proberen
    log(f"Winget gaf foutcode {code}. Bronnen herstellen en opnieuw proberen...", "WARNING")
    repair.repair_winget(log)
    lines_seen.clear()
    code = _run_once(log, WINGET_CMD, lines_seen)

    if code in OK_CODES:
        log("Winget-updates voltooid na herstel.", "SUCCESS")
        return True
    log(f"Winget is opnieuw mislukt (code {code}). Zie log voor details.", "ERROR")
    return False


def search(log, term: str) -> bool:
    """Zoek applicaties in winget; de resultaatlijst verschijnt in het log."""
    term = (term or "").strip()
    if not term:
        log("Geen zoekterm ingevuld.", "ERROR")
        return False
    log(f"=== Winget zoeken: '{term}' ===", "STEP")
    args = ["winget", "search", term, "--accept-source-agreements"]
    lines: list = []
    code = _run_once(log, args, lines)
    if code == 0:
        log("Zoeken voltooid. Gebruik de kolom 'Id' om te installeren.", "SUCCESS")
        return True
    log(f"Zoeken mislukt (code {code}).", "ERROR")
    return False


def install(log, package_id: str) -> bool:
    """Installeer een applicatie op exact winget-ID, volledig silent."""
    package_id = (package_id or "").strip()
    if not package_id:
        log("Geen pakket-ID ingevuld. Zoek eerst het ID via de zoekfunctie.", "ERROR")
        return False
    log(f"=== Installeren: {package_id} ===", "STEP")
    args = ["winget", "install", "--id", package_id, "-e", "--silent",
            "--accept-source-agreements", "--accept-package-agreements"]
    lines: list = []
    code = _run_once(log, args, lines)

    if code == 0 or any("successfully installed" in l for l in lines):
        log(f"'{package_id}' is geïnstalleerd.", "SUCCESS")
        return True
    if any("already installed" in l or "no applicable upgrade" in l
           for l in lines):
        log(f"'{package_id}' was al (up-to-date) geïnstalleerd.", "SUCCESS")
        return True
    log(f"Installatie van '{package_id}' mislukt (code {code}).", "ERROR")
    return False
