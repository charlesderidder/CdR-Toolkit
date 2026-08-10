"""Cameradashboard: live RTSP-streams in een adaptief raster.

RTSP-URL's van camera's (bijv. Eufy) worden in config.json bewaard (sleutel
"eufy_cams": [{"naam", "url", "type", "device_id"}]). Camera's met type
"nest" komen uit de Google Nest-koppeling (tasks/nest.py): die krijgen bij
het openen van het dashboard een verse cloud-stream die automatisch wordt
verlengd. Het dashboard opent als Toplevel (1280x800, zelf te vergroten/
verkleinen): 1 camera = schermvullend, 2 = gesplitst, 3-4 = 2x2, 5-9 = 3x3
enzovoort. Dubbelklik een cel voor solo-weergave; Esc of het kruisje sluit
het dashboard. Het venster past zich nooit zelf aan — de gebruiker bepaalt
de grootte.

Elke stream draait in een eigen achtergrondthread via OpenCV (ffmpeg) met
automatische herverbinding bij uitval.
"""
import math
import threading
import tkinter as tk

from core import crypto, settings
from tasks import recorder

_FPS_MS = 66        # ~15 fps weergave
_HERPOGING_S = 3    # pauze voor herverbinding


def cams() -> list:
    """Opgeslagen camera's met ONTSLEUTELDE url's (alleen intern gebruiken)."""
    uit = []
    for c in settings.get("eufy_cams", []):
        uit.append({"naam": c.get("naam", ""),
                    "url": crypto.ontsleutel(c.get("url", "")),
                    "type": c.get("type", "rtsp"),
                    "device_id": c.get("device_id", ""),
                    "aan": c.get("aan", True)})
    return uit


def sla_cams_op(lijst: list) -> None:
    """Sla camera's op; de url wordt DPAPI-versleuteld opgeslagen."""
    uit = [{"naam": c.get("naam", ""),
            "url": crypto.versleutel(c.get("url", "")),
            "type": c.get("type", "rtsp"),
            "device_id": c.get("device_id", ""),
            "aan": bool(c.get("aan", True))} for c in lijst]
    settings.set("eufy_cams", uit)


def masker(url: str) -> str:
    """URL zonder inloggegevens, veilig om te tonen: rtsp://host/pad…"""
    rest = url.split("://", 1)[-1]
    host_pad = rest.split("@", 1)[-1]
    return f"rtsp://{host_pad}"


def weergave(c: dict) -> str:
    """Leesbare bron-omschrijving van een camera voor in de lijst."""
    if c.get("type") == "nest":
        return "Google Nest (cloud-stream)"
    return masker(c.get("url", ""))


# ------------------------------------------------------------ rasterkeuze
RASTER_SJABLONEN = [
    ("auto", "Automatisch (adaptief)"),
    ("1x1", "Sjabloon 1×1"),
    ("2x2", "Sjabloon 2×2"),
    ("3x3", "Sjabloon 3×3"),
    ("4x4", "Sjabloon 4×4"),
    ("2+1", "Sjabloon 2+1"),
    ("12+1", "Sjabloon 12+1"),
    ("5+1", "Sjabloon 5+1"),
    ("7+1", "Sjabloon 7+1"),
    ("5x5", "Sjabloon 5×5"),
    ("custom", "Aangepast raster…"),
]

_MAX_RK = 8  # max rijen/kolommen bij een aangepast raster


def raster_keuze() -> str:
    """Actieve raster-sleutel uit de instellingen."""
    return settings.get("cam_raster", "auto")


def zet_raster(keuze: str, rijen: int | None = None,
               kolommen: int | None = None) -> None:
    """Bewaar de rasterkeuze en herbouw een open dashboard meteen."""
    settings.set("cam_raster", keuze)
    if rijen is not None:
        settings.set("cam_raster_rijen", max(1, min(int(rijen), _MAX_RK)))
    if kolommen is not None:
        settings.set("cam_raster_kolommen",
                     max(1, min(int(kolommen), _MAX_RK)))
    if actief():
        _dash.bouw_grid()


def raster_cellen():
    """(rijen, kolommen, [(rij, kol, rowspan, colspan), ...]) van de actieve
    keuze, of None bij 'auto' (het dashboard kiest dan zelf een vorm)."""
    k = raster_keuze()
    if k == "auto":
        return None
    if k == "custom":
        r = max(1, min(int(settings.get("cam_raster_rijen", 2)), _MAX_RK))
        kk = max(1, min(int(settings.get("cam_raster_kolommen", 2)), _MAX_RK))
        return r, kk, [(rr, cc, 1, 1) for rr in range(r) for cc in range(kk)]
    if k in ("1x1", "2x2", "3x3", "4x4", "5x5"):
        n = int(k[0])
        return n, n, [(rr, cc, 1, 1) for rr in range(n) for cc in range(n)]
    if k == "2+1":     # 2x2 met één groot vak (1x2) links, twee klein rechts
        klein = [(0, 1), (1, 1)]
        return 2, 2, [(0, 0, 2, 1)] + [(r, c, 1, 1) for r, c in klein]
    if k == "5+1":     # 3x3 met één groot vak (2x2) linksboven
        klein = [(0, 2), (1, 2), (2, 0), (2, 1), (2, 2)]
        return 3, 3, [(0, 0, 2, 2)] + [(r, c, 1, 1) for r, c in klein]
    if k == "7+1":     # 4x4 met één groot vak (3x3) linksboven
        klein = [(0, 3), (1, 3), (2, 3), (3, 0), (3, 1), (3, 2), (3, 3)]
        return 4, 4, [(0, 0, 3, 3)] + [(r, c, 1, 1) for r, c in klein]
    if k == "12+1":    # 4x4 met één groot vak (2x2) linksboven
        klein = [(0, 2), (0, 3), (1, 2), (1, 3), (2, 0), (2, 1), (2, 2),
                 (2, 3), (3, 0), (3, 1), (3, 2), (3, 3)]
        return 4, 4, [(0, 0, 2, 2)] + [(r, c, 1, 1) for r, c in klein]
    return None


class _Stream:
    """Één RTSP-stream in een achtergrondthread, met herverbinding."""

    def __init__(self, naam, url, url_factory=None):
        self.naam, self.url = naam, url
        self.url_factory = url_factory  # callable -> verse URL (Nest)
        self.frame = None          # laatste BGR-frame (numpy)
        self.ok = False
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        try:
            import cv2
        except ImportError:
            return
        while not self._stop.is_set():
            # bij Nest kan de URL ververst zijn; haal die per (her)poging op
            doel = self.url_factory() if self.url_factory else self.url
            if not doel:
                self._stop.wait(_HERPOGING_S)
                continue
            cap = cv2.VideoCapture(doel, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self.ok = cap.isOpened()
                while not self._stop.is_set() and self.ok:
                    ok, frm = cap.read()
                    if not ok:
                        self.ok = False
                        self.frame = None
                        break
                    self.frame = frm
            finally:
                cap.release()
            if not self._stop.is_set():
                self._stop.wait(_HERPOGING_S)

    def stop(self):
        self._stop.set()


class _Dashboard(tk.Toplevel):
    """Venster met adaptief raster van live streams (grootte door gebruiker)."""

    def __init__(self, master, camlijst, on_sluiten, beheer=None, log=None):
        super().__init__(master)
        self.title("Cameradashboard — CdR Toolkit")
        self.configure(bg="#0b0e14")
        self.geometry("1280x800")
        self.minsize(480, 360)
        self.grid_propagate(False)  # venster NOOIT automatisch aanpassen
        self.attributes("-topmost",
                        bool(settings.get("altijd_voorgrond", True)))
        self._on_sluiten = on_sluiten
        self._log = log or (lambda *a, **k: None)
        self._beheer = beheer      # Nest RtspBeheer (keepalive), of None
        self._streams = []
        for c in camlijst:
            s = _Stream(c.get("naam") or c.get("url"), c.get("url", ""),
                        c.get("url_factory"))
            s.start()
            self._streams.append(s)
        self._solo = None    # index van de solo-getoonde camera, of None
        self._cells = []     # (frame, canvas, statuslabel, stream|None)
        self._fotos = []     # ImageTk-referenties (anders garbage-collect)
        self.bouw_grid()
        self.protocol("WM_DELETE_WINDOW", self.sluiten)
        self.bind("<Escape>", lambda e: self.sluiten())
        self.after(_FPS_MS, self._tick)

    @staticmethod
    def _grid_vorm(n):
        """(rijen, kolommen) voor n cellen: 1→1x1, 2→1x2, 3-4→2x2, 5-6→2x3…"""
        rijen = math.ceil(math.sqrt(n))
        return rijen, math.ceil(n / rijen)

    def bouw_grid(self):
        """Bouw het raster: vast sjabloon uit instellingen, of adaptief."""
        for cel, *_ in self._cells:
            cel.destroy()
        self._cells = []
        streams = self._streams
        lay = None
        if self._solo is not None:
            streams = [streams[self._solo]]
        else:
            lay = raster_cellen()
        if lay is None:
            rijen, kolommen = self._grid_vorm(len(streams))
            cellen = [(r, c, 1, 1) for r in range(rijen)
                      for c in range(kolommen)][:len(streams)]
        else:
            rijen, kolommen, cellen = lay
        for r in range(rijen):
            self.rowconfigure(r, weight=1)
        for c in range(kolommen):
            self.columnconfigure(c, weight=1)
        for i, (rr, cc, rs, cs) in enumerate(cellen):
            s = streams[i] if i < len(streams) else None
            cel = tk.Frame(self, bg="#0b0e14", highlightthickness=1,
                           highlightbackground="#1f2430")
            cel.grid(row=rr, column=cc, rowspan=rs, columnspan=cs,
                     sticky="nsew", padx=1, pady=1)
            # Canvas ipv Label: een canvas accepteert elke grootte en forceert
            # nooit een venster-aanpassing (een Label met image doet dat wel)
            video = tk.Canvas(cel, bg="#0b0e14", highlightthickness=0)
            video.pack(fill="both", expand=True)
            if s is not None:
                idx = self._streams.index(s)
                video.bind("<Double-Button-1>",
                           lambda e, k=idx: self._toggle_solo(k))
            balk = tk.Frame(cel, bg="#11141c")
            balk.pack(fill="x")
            status = tk.Label(balk, bg="#11141c", fg="#9ca3af",
                              font=("Segoe UI", 8), anchor="w")
            status.pack(side="left", fill="x", expand=True)
            rec_btn = None
            if s is not None:
                rec_btn = tk.Button(
                    balk, text="⏺", relief="flat", bd=0, bg="#11141c",
                    fg="#f87171", activebackground="#1f2430",
                    font=("Segoe UI", 8), padx=6, cursor="hand2",
                    command=lambda st=s: recorder.toggle(st, self._log))
                rec_btn.pack(side="right")
            self._cells.append((cel, video, status, s, rec_btn))

    def _toggle_solo(self, idx):
        self._solo = None if self._solo == idx else idx
        self.bouw_grid()

    def _tick(self):
        from PIL import Image, ImageTk
        self._fotos = []
        for _, video, status, s, rec_btn in self._cells:
            w, h = video.winfo_width(), video.winfo_height()
            video.delete("all")
            if s is None:
                status.configure(text="  (leeg vak)", fg="#4b5563")
                continue
            if s.ok and s.frame is not None and w > 60 and h > 60:
                try:
                    import cv2
                    # schaal met behoud van beeldverhouding (geen uitgerekt beeld)
                    fh, fw = s.frame.shape[:2]
                    schaal = min(w / fw, h / fh)
                    nw = max(1, int(fw * schaal))
                    nh = max(1, int(fh * schaal))
                    frm = cv2.resize(s.frame, (nw, nh),
                                     interpolation=cv2.INTER_AREA)
                    rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB)
                    foto = ImageTk.PhotoImage(Image.fromarray(rgb))
                    video.create_image(w // 2, h // 2, image=foto,
                                       anchor="center")
                    self._fotos.append(foto)
                except Exception:
                    pass
            elif not s.ok:
                video.create_text(max(w // 2, 60), max(h // 2, 40),
                                  text="Verbinden…", fill="#6b7280",
                                  font=("Segoe UI", 12))
            rec_aan = recorder.actief_voor(s)
            status.configure(
                text=f"  {s.naam}  —  " +
                ("● live" if s.ok else "● geen verbinding (herpoging)") +
                ("   ⏺ REC" if rec_aan else ""),
                fg=("#f87171" if rec_aan else
                    ("#34d399" if s.ok else "#f59e0b")))
            if rec_btn:
                rec_btn.configure(text="⏹" if rec_aan else "⏺")
        self.after(_FPS_MS, self._tick)

    def sluiten(self):
        for s in self._streams:
            s.stop()
        recorder.stop_alles(self._log)
        if self._beheer:
            self._beheer.stop()
        cb, self._on_sluiten = self._on_sluiten, None
        self.destroy()
        if cb:
            cb()


_dash = None


def actief() -> bool:
    """True als het dashboard open is."""
    try:
        return _dash is not None and _dash.winfo_exists()
    except tk.TclError:
        return False


def open_dashboard(master, camlijst, log, on_sluiten=None) -> bool:
    """Open het dashboard met de opgegeven camera's."""
    global _dash
    if actief():
        log("Het dashboard draait al.", "WARNING")
        _dash.lift()
        return True
    try:
        import cv2  # noqa: F401
    except ImportError:
        log("OpenCV ontbreekt: installeer met "
            "'python -m pip install opencv-python-headless'.", "ERROR")
        return False
    # Nest-camera's krijgen een verse cloud-stream met keepalive
    from tasks import nest
    beheer = None
    geldig = []
    for c in camlijst:
        if not c.get("aan", True):
            continue  # uitgeschakeld via Instellingen
        if c.get("type") == "nest":
            if beheer is None:
                beheer = nest.RtspBeheer(log)
            did = c.get("device_id", "")
            url = beheer.voeg_toe(did, c.get("naam", ""))
            if url:
                geldig.append({**c, "url": url,
                               "url_factory": (lambda d=did:
                                               beheer.url_voor(d))})
        elif c.get("url"):
            geldig.append(c)
    if not geldig:
        log("Geen actieve camera's — voeg er een toe of schakel er een in "
            "via Instellingen.", "WARNING")
        return False
    lay = raster_cellen()
    if lay and len(geldig) > len(lay[2]):
        log(f"Let op: het gekozen raster heeft {len(lay[2])} vakken; "
            f"{len(geldig) - len(lay[2])} camera('s) zijn niet zichtbaar.",
            "WARNING")

    def gesloten():
        global _dash
        _dash = None
        if on_sluiten:
            on_sluiten()

    _dash = _Dashboard(master, geldig, gesloten, beheer=beheer, log=log)
    if beheer:
        beheer.start()
    log(f"Dashboard gestart met {len(geldig)} camera('s). "
        "Dubbelklik een beeld voor solo-weergave; Esc = sluiten.", "SUCCESS")
    return True


def set_topmost(aan: bool, log=None) -> None:
    """Zet het open dashboard-venster (indien actief) op/van de voorgrond."""
    if actief():
        _dash.attributes("-topmost", bool(aan))
        if log:
            log(f"Dashboard altijd-op-voorgrond: "
                f"{'aan' if aan else 'uit'}.", "INFO")


def stop_dashboard(log) -> bool:
    """Sluit het dashboard als het open is."""
    if actief():
        _dash.sluiten()
        log("Dashboard gestopt.", "SUCCESS")
    else:
        log("Het dashboard draaide niet.", "WARNING")
    return True
