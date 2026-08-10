"""Google Nest camera's koppelen via de Smart Device Management (SDM) API.

Eenmalige voorbereiding in de Google-cloud (door de gebruiker):
1. console.cloud.google.com: project aanmaken en de
   "Smart Device Management API" activeren.
2. OAuth-toestemmingsscherm: type "Extern", eigen e-mailadres als
   testgebruiker toevoegen.
3. OAuth-client aanmaken van het type "Desktop-app" (client-ID + secret).
4. developers.nest.com/device-access: Device Access-project registreren
   (eenmalig US$5) en koppelen aan het Cloud-project -> project-ID.

Vul daarna in Instellingen de client-ID, het secret en het project-ID in en
klik op "Inloggen met Google". De refresh-token wordt DPAPI-versleuteld
bewaard; daarna verschijnen alle Nest-camera's automatisch in het dashboard.

Let op: Nest-streams lopen via Google's cloud-relay (geen lokaal RTSP zoals
Eufy) en verlopen na enkele minuten; de RtspBeheer-keepalive verlengt ze
automatisch zolang het dashboard open is.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from core import crypto, settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://smartdevicemanagement.googleapis.com/v1"
SCOPE = "https://www.googleapis.com/auth/sdm.service"
VERLENG_NA_S = 240            # streams elke 4 minuten verlengen


# ------------------------------------------------------------ configuratie
def cfg() -> dict:
    """Koppel-gegevens uit de instellingen (gevoelige velden ontsleuteld)."""
    c = settings.get("nest_cfg", {}) or {}
    return {
        "client_id": crypto.ontsleutel(c.get("client_id", "")),
        "client_secret": crypto.ontsleutel(c.get("client_secret", "")),
        "project_id": c.get("project_id", ""),
        "refresh_token": crypto.ontsleutel(c.get("refresh_token", "")),
    }


def sla_cfg(client_id: str, client_secret: str, project_id: str,
            refresh_token: str | None = None) -> None:
    """Bewaar koppel-gegevens; gevoelige velden DPAPI-versleuteld.

    refresh_token=None betekent: behoud de bestaande token.
    """
    oud = cfg()
    settings.set("nest_cfg", {
        "client_id": crypto.versleutel(client_id.strip()),
        "client_secret": crypto.versleutel(client_secret.strip()),
        "project_id": project_id.strip(),
        "refresh_token": crypto.versleutel(
            oud["refresh_token"] if refresh_token is None
            else refresh_token.strip()),
    })


def gekoppeld() -> bool:
    """True als er een refresh-token is opgeslagen."""
    return bool(cfg()["refresh_token"])


def ontkoppel() -> None:
    """Verwijder alle koppel-gegevens (tokens, client-gegevens)."""
    global _token_cache
    _token_cache = {"token": None, "exp": 0.0}
    settings.set("nest_cfg", {})


# ------------------------------------------------------------- HTTP-basis
def _post_form(url: str, velden: dict) -> dict:
    """POST x-www-form-urlencoded; geeft JSON terug of gooit RuntimeError."""
    data = urllib.parse.urlencode(velden).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read())
            oms = d.get("error_description") or d.get("error") or str(e)
        except Exception:
            oms = str(e)
        raise RuntimeError(oms) from e


_token_cache = {"token": None, "exp": 0.0}


def access_token() -> str:
    """Geldig access-token; ververst automatisch via de refresh-token."""
    global _token_cache
    if _token_cache["token"] and time.time() < _token_cache["exp"] - 60:
        return _token_cache["token"]
    c = cfg()
    if not c["refresh_token"]:
        raise RuntimeError("Google Nest is niet gekoppeld")
    d = _post_form(TOKEN_URL, {
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"],
        "grant_type": "refresh_token",
    })
    _token_cache = {"token": d["access_token"],
                    "exp": time.time() + int(d.get("expires_in", 3600))}
    return _token_cache["token"]


def _api(pad: str, methode: str = "GET", body: dict | None = None) -> dict:
    """Geautoriseerde SDM API-aanroep."""
    req = urllib.request.Request(
        f"{API}{pad}", method=methode,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {access_token()}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read())
            oms = d.get("error", {}).get("message") or str(e)
        except Exception:
            oms = str(e)
        raise RuntimeError(oms) from e


# ------------------------------------------------------------- OAuth-login
class _OAuthVanger(BaseHTTPRequestHandler):
    """Vangt de ?code= van Google's loopback-redirect op (één request)."""

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.code = q.get("code", [None])[0]
        self.server.fout = q.get("error", [None])[0]
        body = ("<html><body style='font-family:sans-serif;text-align:center;"
                "padding-top:40px'><h3>Koppeling gelukt &mdash; "
                "dit venster kan gesloten worden.</h3></body></html>"
                ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # stil houden
        pass


def login(log) -> bool:
    """Browser-login met Google (loopback-flow). Blokkerend — in thread
    draaien. Bewaart de refresh-token; geeft True als het gelukt is."""
    c = cfg()
    if not (c["client_id"] and c["client_secret"]):
        log("Vul eerst client-ID en client-secret in en klik op Opslaan.",
            "ERROR")
        return False
    if " " in c["client_id"] or " " in c["client_secret"]:
        log("Client-ID/secret mogen geen spaties bevatten (Client-ID eindigt "
            "op .apps.googleusercontent.com).", "ERROR")
        return False
    try:
        httpd = HTTPServer(("127.0.0.1", 0), _OAuthVanger)
    except OSError as exc:
        log(f"Lokale callback-server kon niet starten: {exc}", "ERROR")
        return False
    poort = httpd.server_address[1]
    httpd.code = httpd.fout = None
    httpd.timeout = 240
    params = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "redirect_uri": f"http://127.0.0.1:{poort}",
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    log("Browser geopend: log in met je Google-account en geef toestemming "
        "voor Nest (Smart Device Management)...")
    webbrowser.open(f"{AUTH_URL}?{params}")
    try:
        httpd.handle_request()            # wacht op precies één redirect
    finally:
        httpd.server_close()
    if httpd.fout or not httpd.code:
        log("Google-login afgebroken of mislukt "
            f"({httpd.fout or 'geen reactie binnen 4 minuten'}).", "WARNING")
        return False
    try:
        tokens = _post_form(TOKEN_URL, {
            "code": httpd.code,
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "redirect_uri": f"http://127.0.0.1:{poort}",
            "grant_type": "authorization_code",
        })
    except RuntimeError as exc:
        log(f"Token-uitwisseling mislukt: {exc}", "ERROR")
        return False
    refresh = tokens.get("refresh_token")
    if not refresh:
        log("Geen refresh-token ontvangen — probeer opnieuw in te loggen.",
            "ERROR")
        return False
    sla_cfg(c["client_id"], c["client_secret"], c["project_id"], refresh)
    log("Google-account gekoppeld. Klik nu op \"Camera's ophalen\".",
        "SUCCESS")
    return True


# ------------------------------------------------------------ SDM camera's
def lijst_cams() -> list:
    """Alle Nest-apparaten met een livestream-trait (camera's en deurbellen)."""
    c = cfg()
    pid = c["project_id"]
    if not pid:
        raise RuntimeError("Project-ID ontbreekt in de instellingen")
    if not re.fullmatch(r"[0-9A-Za-z-]+", pid):
        raise RuntimeError(
            f"'{pid}' is geen geldig Project-ID. Vul het Device Access "
            "project-ID in (UUID uit console.nest.google.com/device-access, "
            "bijv. 1234abcd-56ef-...), niet de projectnaam.")
    data = _api(f"/enterprises/{pid}/devices")
    uit = []
    for d in data.get("devices", []):
        traits = d.get("traits", {})
        live = traits.get("sdm.devices.traits.CameraLiveStream")
        if not live:
            continue
        naam = (traits.get("sdm.devices.traits.Info", {}).get("customName")
                or (d.get("parentRelations") or [{}])[0].get("displayName")
                or d["name"].rsplit("/", 1)[-1])
        uit.append({"naam": naam, "device_id": d["name"],
                    "protocollen": live.get("supportedProtocols", [])})
    return uit


def genereer_rtsp(device_id: str):
    """Nieuwe RTSP-stream aanmaken: (rtsp-url, extension_token)."""
    r = _api(f"/{device_id}:executeCommand", "POST", {
        "command": "sdm.devices.commands.CameraLiveStream.GenerateRtspStream",
        "params": {}})
    res = r.get("results", {})
    return res["streamUrls"]["rtspUrl"], res["streamExtensionToken"]


def verleng_rtsp(device_id: str, ext_token: str) -> str:
    """Verleng een lopende stream; geeft de nieuwe extension-token."""
    r = _api(f"/{device_id}:executeCommand", "POST", {
        "command": "sdm.devices.commands.CameraLiveStream.ExtendRtspStream",
        "params": {"streamExtensionToken": ext_token}})
    return r.get("results", {})["streamExtensionToken"]


# --------------------------------------------------------- instructies
INSTRUCTIES = """\
Nest-camera's toevoegen — stap voor stap

BELANGRIJK: camera's hoeven NIET in Google Cloud gekoppeld te worden. Ze
moeten in de Google Home app staan onder hetzelfde Google-account als
waarmee je straks inlogt — dan verschijnen ze vanzelf via de API.

Let op bij het invullen: gebruik het Device Access project-ID (een UUID
zoals 1234abcd-56ef-...), NIET de projectnaam. Een naam met een spatie
(bijv. "Charles Toolkit") geeft de foutmelding
"URL can't contain control characters".

A. Google Cloud Console (console.cloud.google.com)
1. Selecteer je project → API's en services → Bibliotheek → zoek
   "Smart Device Management API" → Inschakelen.
2. OAuth-toestemmingsscherm: type Extern, vul een naam in en voeg je
   eigen e-mailadres toe als testgebruiker.
3. Referenties → Referenties maken → OAuth-client-ID → type
   "Desktop-app" → noteer Client-ID en Client-secret.

B. Device Access Console (console.nest.google.com/device-access)
1. Registreer (eenmalig $5) en maak een project aan.
2. Koppel daarin je Cloud-project (het Cloud project-ID, bijv.
   charles-toolkit-123) én vul het OAuth-client-ID uit stap A3 in.
3. Noteer het Project-ID van dit Device Access-project — dat is de
   UUID die je in de app invult.

C. In deze app (Instellingen → Google Nest camera's)
1. Vul Client-ID, Client-secret en het Device Access project-ID (UUID)
   in → Opslaan.
2. Inloggen met Google → de browser opent; log in met het account dat
   je Nest-camera's in Google Home beheert. De waarschuwing "Google
   heeft deze app niet geverifieerd" is normaal voor test-apps: kies
   Geavanceerd → Doorgaan.
3. Camera's ophalen → alle camera's verschijnen in de lijst en in het
   dashboard.
"""


def toon_instructies(master=None):
    """Popup met stap-voor-stap uitleg over de Google Nest-koppeling."""
    import tkinter as tk
    win = tk.Toplevel(master)
    win.title("Nest-camera's toevoegen — instructies")
    win.geometry("660x600")
    win.minsize(480, 400)
    win.configure(bg="#ffffff")
    tekst = tk.Text(win, wrap="word", relief="flat", bg="#ffffff",
                    fg="#1f2937", font=("Segoe UI", 9), padx=14, pady=12,
                    cursor="arrow")
    scroll = tk.Scrollbar(win, command=tekst.yview)
    tekst.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    tekst.pack(fill="both", expand=True)
    tekst.tag_configure("titel", font=("Segoe UI", 12, "bold"),
                        spacing3=8)
    tekst.tag_configure("kop", font=("Segoe UI", 9, "bold"),
                        foreground="#4f6ef7", spacing1=10, spacing3=2)
    for i, regel in enumerate(INSTRUCTIES.splitlines()):
        if i == 0:
            tekst.insert("end", regel + "\n", "titel")
        elif regel[:3] in ("A. ", "B. ", "C. "):
            tekst.insert("end", regel + "\n", "kop")
        else:
            tekst.insert("end", regel + "\n")
    tekst.configure(state="disabled")  # alleen-lezen, wel selecteerbaar
    return win


class RtspBeheer:
    """Genereert Nest-streams en houdt ze levend (elke 4 min verlengen).

    Mislukt het verlengen, dan wordt een geheel nieuwe stream aangemaakt;
    de url_factory van de betreffende stream pakt de nieuwe URL automatisch
    bij de eerstvolgende herverbinding.
    """

    def __init__(self, log):
        self.log = log
        self._items = {}          # device_id -> {"naam", "url", "ext"}
        self._stop = threading.Event()
        self._thread = None

    def voeg_toe(self, device_id: str, naam: str) -> str | None:
        """Genereer een stream en geef de eerste URL; None bij een fout."""
        try:
            url, ext = genereer_rtsp(device_id)
        except RuntimeError as exc:
            self.log(f"Nest-stream '{naam}': {exc}", "ERROR")
            return None
        self._items[device_id] = {"naam": naam, "url": url, "ext": ext}
        return url

    def url_voor(self, device_id: str) -> str | None:
        """Huidige (mogelijk vernieuwde) stream-URL van een camera."""
        it = self._items.get(device_id)
        return it["url"] if it else None

    def start(self):
        if self._items and self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        while not self._stop.wait(VERLENG_NA_S):
            for did, it in list(self._items.items()):
                try:
                    it["ext"] = verleng_rtsp(did, it["ext"])
                except Exception as exc:
                    self.log(f"Nest-stream '{it['naam']}': verlengen mislukt "
                             f"({exc}); nieuwe stream aanmaken...", "WARNING")
                    try:
                        it["url"], it["ext"] = genereer_rtsp(did)
                    except Exception as exc2:
                        self.log(f"Nest-stream '{it['naam']}': opnieuw "
                                 f"aanmaken mislukt ({exc2})", "ERROR")

    def stop(self):
        self._stop.set()
