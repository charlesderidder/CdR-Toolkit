"""Netwerk-tools (IP-tools): IP-info, diagnose, DNS opschonen en netwerk-reset.

Deze functies zijn handmatige hulpmiddelen en maken bewust GEEN deel uit van
de een-klik-onderhoudspipeline: een netwerk-reset is verstorend en doe je
alleen bij problemen.
"""
import subprocess

from core import runner

# ipconfig/netsh/ping geven uitvoer in de OEM-codepagina (NL: cp850)
_OEM = "cp850"


def _extern_ip(log) -> None:
    """Toon het externe IP-adres (best effort, slaat stilletjes over zonder internet)."""
    import urllib.request
    try:
        req = urllib.request.Request("https://api.ipify.org",
                                     headers={"User-Agent": "CharlesOnderhoud"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            log(f"Extern IP-adres: {resp.read().decode().strip()}", "SUCCESS")
    except Exception:
        log("Extern IP-adres kon niet opgehaald worden (geen internet?).", "WARNING")


def show_ip_info(log) -> bool:
    """Toon de volledige netwerkconfiguratie plus extern IP-adres."""
    log("=== IP-adres & netwerkinfo ===", "STEP")
    code = runner.run_stream(["ipconfig", "/all"], log, encoding=_OEM)
    _extern_ip(log)
    return code == 0


def flush_dns(log) -> bool:
    """Leeg de DNS-resolvercache."""
    log("=== DNS-cache opschonen ===", "STEP")
    code = runner.run_stream(["ipconfig", "/flushdns"], log, encoding=_OEM)
    if code == 0:
        log("DNS-cache opgeschoond.", "SUCCESS")
        return True
    log(f"DNS opschonen mislukt (code {code}).", "ERROR")
    return False


def _default_gateway() -> str | None:
    """Bepaal de standaardgateway via PowerShell (None als dat niet lukt)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
             "Sort-Object RouteMetric | Select-Object -First 1).NextHop"],
            capture_output=True, text=True, timeout=15,
            creationflags=runner.CREATE_NO_WINDOW)
        return out.stdout.strip() or None
    except Exception:
        return None


def network_diag(log) -> bool:
    """Diagnose: ping gateway, ping internet (8.8.8.8) en test DNS-resolutie."""
    log("=== Netwerkdiagnose ===", "STEP")
    tests = []
    gw = _default_gateway()
    if gw:
        tests.append((f"Ping standaardgateway ({gw})", ["ping", "-n", "2", gw]))
    else:
        log("Standaardgateway niet gevonden; gateway-test overgeslagen.", "WARNING")
    tests.append(("Ping internet (8.8.8.8)", ["ping", "-n", "2", "8.8.8.8"]))
    tests.append(("DNS-test (nslookup google.com)", ["nslookup", "google.com"]))

    mislukt = 0
    for naam, cmd in tests:
        log(f"--- {naam} ---")
        if runner.run_stream(cmd, log, encoding=_OEM) != 0:
            mislukt += 1
            log(f"{naam}: MISLUKT.", "WARNING")
    if mislukt == 0:
        log("Diagnose voltooid: alles in orde.", "SUCCESS")
        return True
    log(f"Diagnose voltooid: {mislukt} test(s) mislukt.", "WARNING")
    return False


def network_reset(log) -> bool:
    """Volledige netwerk-reset: DNS, IP release/renew, Winsock en TCP/IP-stack."""
    log("=== Netwerk volledig resetten ===", "STEP")
    log("Let op: de netwerkverbinding valt kort weg. Herstart de pc daarna.",
        "WARNING")
    stappen = [
        (["ipconfig", "/flushdns"], "DNS-cache legen"),
        (["ipconfig", "/release"], "IP-adres vrijgeven"),
        (["ipconfig", "/renew"], "Nieuw IP-adres aanvragen"),
        (["netsh", "winsock", "reset"], "Winsock-catalogus resetten"),
        (["netsh", "int", "ip", "reset"], "TCP/IP-stack resetten"),
    ]
    mislukt = 0
    for cmd, naam in stappen:
        log(f"--- {naam} ---")
        if runner.run_stream(cmd, log, encoding=_OEM) != 0:
            mislukt += 1
            log(f"{naam}: mislukt.", "WARNING")
    if mislukt == 0:
        log("Netwerk-reset voltooid. Herstart de pc om alles te activeren.",
            "SUCCESS")
        return True
    log(f"Netwerk-reset klaar met {mislukt} mislukte stap(pen). Herstart aanbevolen.",
        "WARNING")
    return False
