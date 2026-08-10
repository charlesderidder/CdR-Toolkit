"""Remote-webserver: deze computer overnemen via de browser.

Volledig ingebouwd — geen RustDesk, RDP of andere externe software nodig.
De webserver (core/remoteserver.py) streamt het scherm als MJPEG en accepteert
muis/toetsenbord-invoer; optioneel opent UPnP (core/upnp.py) een routerpoort
voor bereik via internet.
"""
from core import crypto, settings, upnp
from core.remoteserver import POORT, RemoteServer

_server = RemoteServer()
_urls = {"lan": None, "wan": None}


def start(log, internet: bool = False) -> bool:
    """Start de webserver; toon LAN-adres (+ internet-adres bij UPnP-succes)."""
    log("=== Remote-webserver starten ===", "STEP")
    vast_pw = crypto.ontsleutel(
        settings.get("remote_wachtwoord", "") or "").strip() or None
    if vast_pw:
        log("Vast wachtwoord uit instellingen wordt gebruikt.")
    if not _server.start(log, wachtwoord=vast_pw):
        return False

    ip = upnp._lokaal_ip()
    _urls["lan"] = f"http://{ip}:{POORT}"
    log(f"LAN-adres: {_urls['lan']}")

    _urls["wan"] = None
    if internet:
        log("UPnP: proberen een routerpoort te openen...")
        if upnp.open_poort(POORT, log):
            pub = upnp.publiek_ip()
            if pub:
                _urls["wan"] = f"http://{pub}:{POORT}"
                log(f"Internet-adres: {_urls['wan']}", "SUCCESS")
            else:
                log("Extern IP kon niet worden bepaald.", "WARNING")
        else:
            log("Geen internet-toegang via UPnP; alleen LAN bereikbaar.",
                "WARNING")
    log("Sessie-wachtwoord wordt niet getoond; gebruik 'Toon' op de "
        "Remote-pagina om het zichtbaar te maken.", "SUCCESS")
    return True


def stop(log) -> bool:
    """Stop de webserver en ruim poorten/firewall-regels op."""
    log("=== Remote-webserver stoppen ===", "STEP")
    upnp.sluit_poort(POORT, log)
    ok = _server.stop(log)
    _urls["lan"] = _urls["wan"] = None
    return ok


def actief() -> bool:
    return _server.actief()


def wachtwoord() -> str:
    return _server.sessie.wachtwoord if _server.sessie else ""


def urls() -> dict:
    return dict(_urls)
