"""UPnP (IGD) port-forwarding: probeer automatisch een routerpoort te openen.

Werkt alleen als de router UPnP aan heeft staan. Bij elke fout wordt netjes
False teruggegeven zodat de app gewoon LAN-only verder kan.
"""
import socket
import urllib.request
import xml.etree.ElementTree as ET

SSDP_ADDR = ("239.255.255.250", 1900)
_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
    "\r\n"
).encode()

# Onthoudt de gevonden router-service om de mapping later weer te sluiten
_staat = {"ctrl": None, "stype": None}


def _lokaal_ip() -> str:
    """Primair LAN-IP (via een dummy UDP-'verbinding')."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def publiek_ip():
    """Extern IP-adres via api.ipify.org (None bij falen)."""
    try:
        req = urllib.request.Request("https://api.ipify.org",
                                     headers={"User-Agent": "CharlesOnderhoud"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read().decode().strip()
    except Exception:
        return None


def _ontdek_igd(timeout=3):
    """Zoek een Internet Gateway Device via SSDP; geef de location-URL."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(_MSEARCH, SSDP_ADDR)
        while True:
            data, _ = s.recvfrom(2048)
            for regel in data.decode("utf-8", "replace").split("\r\n"):
                if regel.lower().startswith("location:"):
                    return regel.split(":", 1)[1].strip()
    except (socket.timeout, OSError):
        return None
    finally:
        s.close()
    return None


def _controle_url(location: str):
    """Lees het device-description XML; geef (controlURL, serviceType)."""
    with urllib.request.urlopen(location, timeout=5) as r:
        root = ET.fromstring(r.read())
    ns = {"u": "urn:schemas-upnp-org:device-1-0"}
    for service in root.iter("{urn:schemas-upnp-org:device-1-0}service"):
        stype = service.findtext("u:serviceType", "", ns)
        if "WANIPConnection" in stype or "WANPPPConnection" in stype:
            ctrl = service.findtext("u:controlURL", "", ns)
            if ctrl:
                if ctrl.startswith("http"):
                    return ctrl, stype
                base = "/".join(location.split("/")[:3])
                return base + (ctrl if ctrl.startswith("/") else "/" + ctrl), stype
    return None, None


def _soap(url: str, service_type: str, actie: str, args: dict):
    """Voer een SOAP-actie uit op de router."""
    body_args = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    body = (
        '<?xml version="1.0"?>\r\n'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{actie} xmlns:u="{service_type}">{body_args}</u:{actie}>'
        "</s:Body></s:Envelope>"
    ).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{service_type}#{actie}',
        "User-Agent": "CharlesOnderhoud",
    })
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read()


def open_poort(poort: int, log, beschrijving="CharlesOnderhoud Remote") -> bool:
    """Probeer een TCP-poortmapping op de router toe te voegen."""
    loc = _ontdek_igd()
    if not loc:
        log("UPnP: geen router (IGD) gevonden. Gebruik LAN of forward handmatig.",
            "WARNING")
        return False
    try:
        ctrl, stype = _controle_url(loc)
        if not ctrl:
            log("UPnP: geen WANIPConnection-service gevonden.", "WARNING")
            return False
        intern = _lokaal_ip()
        _soap(ctrl, stype, "AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": poort, "NewProtocol": "TCP",
            "NewInternalPort": poort, "NewInternalClient": intern,
            "NewEnabled": 1, "NewPortMappingDescription": beschrijving,
            "NewLeaseDuration": 0})
        _staat["ctrl"], _staat["stype"] = ctrl, stype
        log(f"UPnP: poort {poort} doorgestuurd naar {intern}:{poort}.", "SUCCESS")
        return True
    except Exception as exc:
        log(f"UPnP mislukt: {exc}", "WARNING")
        return False


def sluit_poort(poort: int, log) -> None:
    """Verwijder de poortmapping weer (als die eerder was aangemaakt)."""
    if not _staat["ctrl"]:
        return
    try:
        _soap(_staat["ctrl"], _staat["stype"], "DeletePortMapping", {
            "NewRemoteHost": "", "NewExternalPort": poort, "NewProtocol": "TCP"})
        log(f"UPnP: poort {poort} weer gesloten.")
    except Exception as exc:
        log(f"UPnP-poort sluiten mislukt: {exc}", "WARNING")
    _staat["ctrl"] = _staat["stype"] = None
