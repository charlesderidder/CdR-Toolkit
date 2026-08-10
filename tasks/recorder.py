"""Opnames van camerabeelden: opnemen naar mp4 en opnames beheren/afspelen.

Opnames komen in Documenten\\CharlesOnderhoud\\opnames als
<cameranaam>_YYYYMMDD_HHMMSS.mp4 (codec mp4v, ~10 fps). Opnemen gebeurt
vanuit het dashboard met de ⏺-knop per camera; beheer (afspelen in de app,
openen in een speler, kopiëren/downloaden, verwijderen) via het
opnames-venster (toon_opnames).

Let op: opnames die al op het SD-kaartje van een Eufy-camera staan zijn via
geen open interface bereikbaar (alleen via de officiële Eufy-app). Deze
module maakt eigen opnames van de live streams.
"""
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from core.logger import data_dir

_OPNAME_FPS = 10


def opname_map() -> str:
    """Map met opnames: Documenten\\CharlesOnderhoud\\opnames (aangemaakt)."""
    pad = os.path.join(data_dir(), "opnames")
    os.makedirs(pad, exist_ok=True)
    return pad


def _veilige_naam(naam: str) -> str:
    """Maak een cameranaam veilig als bestandsnaam."""
    return re.sub(r'[<>:"/\\|?*]', "_", naam).strip() or "camera"


class Recorder:
    """Schrijft de frames van één stream weg naar een mp4-bestand."""

    def __init__(self, stream, pad, log):
        self.stream, self.pad, self.log = stream, pad, log
        self._stop = threading.Event()
        self._thread = None
        self._writer = None
        self.actief = False

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        import cv2
        self.actief = True
        laatste = None
        try:
            while not self._stop.is_set():
                frm = self.stream.frame
                if frm is None or frm is laatste:
                    time.sleep(1 / (_OPNAME_FPS * 2))
                    continue
                if self._writer is None:
                    h, w = frm.shape[:2]
                    self._writer = cv2.VideoWriter(
                        self.pad, cv2.VideoWriter_fourcc(*"mp4v"),
                        _OPNAME_FPS, (w, h))
                    if not self._writer.isOpened():
                        self.log(f"Opname kan niet worden weggeschreven "
                                 f"naar {self.pad}", "ERROR")
                        break
                self._writer.write(frm)
                laatste = frm
                time.sleep(1 / (_OPNAME_FPS * 2))
        finally:
            if self._writer is not None:
                self._writer.release()
            self.actief = False

    def stop(self):
        self._stop.set()


# actieve opnames: id(stream) -> Recorder
_recorders = {}


def toggle(stream, log) -> bool:
    """Start of stop de opname van een stream; geeft de nieuwe staat (aan?)."""
    key = id(stream)
    rec = _recorders.get(key)
    if rec and rec.actief:
        rec.stop()
        if rec._thread:
            # wacht tot de writer gereleased is (mp4 is dan echt afgesloten)
            rec._thread.join(timeout=3)
        _recorders.pop(key, None)
        log(f"Opname gestopt: {os.path.basename(rec.pad)}", "SUCCESS")
        return False
    naam = _veilige_naam(stream.naam)
    pad = os.path.join(opname_map(),
                       f"{naam}_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
    rec = Recorder(stream, pad, log)
    _recorders[key] = rec
    rec.start()
    log(f"Opname gestart: {os.path.basename(pad)}", "SUCCESS")
    return True


def actief_voor(stream) -> bool:
    """True als er van deze stream een opname loopt."""
    rec = _recorders.get(id(stream))
    return bool(rec and rec.actief)


def stop_alles(log=None):
    """Stop alle lopende opnames (bijv. bij het sluiten van het dashboard)."""
    for _key, rec in list(_recorders.items()):
        rec.stop()
    for _key, rec in list(_recorders.items()):
        if rec._thread:
            rec._thread.join(timeout=3)  # mp4 netjes afsluiten
        if log:
            log(f"Opname gestopt: {os.path.basename(rec.pad)}", "INFO")
    _recorders.clear()


def lijst() -> list:
    """Opnames in de opname-map, nieuwste eerst."""
    uit = []
    for f in os.listdir(opname_map()):
        if not f.lower().endswith((".mp4", ".avi", ".mkv")):
            continue
        vol = os.path.join(opname_map(), f)
        try:
            st = os.stat(vol)
        except OSError:
            continue
        uit.append({"naam": f, "pad": vol, "grootte": st.st_size,
                    "gewijzigd": st.st_mtime})
    uit.sort(key=lambda x: x["gewijzigd"], reverse=True)
    return uit


# ------------------------------------------------------------- afspelen
class _Speler(tk.Toplevel):
    """Afspeelvenster voor één opname (spatie = pauze, Esc = sluiten)."""

    def __init__(self, master, pad):
        super().__init__(master)
        self.title(f"Opname — {os.path.basename(pad)}")
        self.configure(bg="#0b0e14")
        self.geometry("960x580")
        import cv2
        self._cap = cv2.VideoCapture(pad)
        if not self._cap.isOpened():
            tk.Label(self, text="Kan deze opname niet openen.", bg="#0b0e14",
                     fg="#f87171", font=("Segoe UI", 12)).pack(expand=True)
            self._cap = None
            return
        fps = self._cap.get(cv2.CAP_PROP_FPS) or _OPNAME_FPS
        self._vertraging = max(15, int(1000 / min(fps, 30)))
        self._pauze = False
        self._foto = None
        self.video = tk.Canvas(self, bg="#0b0e14", highlightthickness=0)
        self.video.pack(fill="both", expand=True)
        self._status = tk.Label(self, bg="#11141c", fg="#9ca3af",
                                font=("Segoe UI", 8), anchor="w")
        self._status.pack(fill="x")
        self._status.configure(text=f"  {os.path.basename(pad)}  —  "
                                    "spatie = pauze, Esc = sluiten")
        self.protocol("WM_DELETE_WINDOW", self.sluiten)
        self.bind("<Escape>", lambda e: self.sluiten())
        self.bind("<space>", lambda e: self._pauzeren())
        self._tick()

    def _pauzeren(self):
        self._pauze = not self._pauze

    def _tick(self):
        if not self._pauze:
            ok, frm = self._cap.read()
            if not ok:
                self._status.configure(text="  Einde van de opname  —  "
                                            "Esc = sluiten")
                return
            import cv2
            from PIL import Image, ImageTk
            w, h = self.video.winfo_width(), self.video.winfo_height()
            if w > 60 and h > 60:
                fh, fw = frm.shape[:2]
                schaal = min(w / fw, h / fh)
                frm = cv2.resize(frm, (max(1, int(fw * schaal)),
                                       max(1, int(fh * schaal))),
                                 interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB)
                self._foto = ImageTk.PhotoImage(Image.fromarray(rgb))
                self.video.create_image(w // 2, h // 2, image=self._foto)
        self.after(self._vertraging, self._tick)

    def sluiten(self):
        if self._cap is not None:
            self._cap.release()
        self.destroy()


# -------------------------------------------------------- opnames-venster
def toon_opnames(master, log):
    """Venster met alle opnames: afspelen, openen, kopiëren, verwijderen."""
    win = tk.Toplevel(master)
    win.title("Opnames — CdR Toolkit")
    win.geometry("680x500")
    win.minsize(520, 380)
    win.configure(bg="#ffffff")

    tk.Label(win, text="Opnames van je camera's (mp4). Afspelen kan direct "
                       "in de app; 'Kopieer naar…' downloadt een opname "
                       "naar een eigen map.",
             bg="#ffffff", fg="#6b7280", font=("Segoe UI", 9),
             anchor="w", justify="left").pack(fill="x", padx=12, pady=(10, 4))

    lijst_frame = tk.Frame(win, bg="#ffffff")
    lijst_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))
    lb = tk.Listbox(lijst_frame, relief="flat", bg="#f6f7f9", fg="#1f2937",
                    font=("Consolas", 9), highlightthickness=1,
                    highlightbackground="#e5e7eb", selectbackground="#4f6ef7",
                    selectforeground="white", exportselection=False,
                    activestyle="none")
    scroll = ttk.Scrollbar(lijst_frame, command=lb.yview)
    lb.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    lb.pack(fill="both", expand=True)

    items = []

    def ververs():
        nonlocal items
        items = lijst()
        lb.delete(0, "end")
        for it in items:
            tijd = time.strftime("%d-%m %H:%M",
                                 time.localtime(it["gewijzigd"]))
            mb = it["grootte"] / 1048576
            lb.insert("end", f"{it['naam']:<46} {tijd}  {mb:>7.1f} MB")
        if items:
            lb.selection_set(0)
        else:
            lb.insert("end", "(nog geen opnames — gebruik ⏺ in het "
                             "dashboard om op te nemen)")

    def keuze():
        sel = lb.curselection()
        return items[sel[0]] if sel and sel[0] < len(items) else None

    def act_afspelen():
        it = keuze()
        if it:
            _Speler(win, it["pad"])

    def act_open():
        it = keuze()
        if it:
            os.startfile(it["pad"])

    def act_verkenner():
        it = keuze()
        if it:
            subprocess.run(["explorer", "/select,", it["pad"]])

    def act_kopieer():
        it = keuze()
        if not it:
            return
        doel = filedialog.askdirectory(parent=win,
                                       title="Kies een doelmap")
        if not doel:
            return
        try:
            shutil.copy2(it["pad"], os.path.join(doel, it["naam"]))
            log(f"Opname gekopieerd naar {doel}: {it['naam']}", "SUCCESS")
        except OSError as exc:
            log(f"Kopiëren mislukt: {exc}", "ERROR")

    def act_verwijder():
        it = keuze()
        if not it:
            return
        try:
            os.remove(it["pad"])
            log(f"Opname verwijderd: {it['naam']}", "INFO")
        except OSError as exc:
            log(f"Verwijderen mislukt: {exc}", "ERROR")
        ververs()

    knoppen = tk.Frame(win, bg="#ffffff")
    knoppen.pack(fill="x", padx=12, pady=(0, 6))
    for tekst, cmd in (("▶ Afspelen", act_afspelen),
                       ("Open in speler", act_open),
                       ("Toon in verkenner", act_verkenner),
                       ("Kopieer naar…", act_kopieer),
                       ("Verwijderen", act_verwijder),
                       ("Vernieuwen", ververs),
                       ("Open opnamemap",
                        lambda: os.startfile(opname_map()))):
        tk.Button(knoppen, text=tekst, relief="flat", bd=0, bg="#eef1f6",
                  fg="#1f2937", activebackground="#dfe5f0",
                  font=("Segoe UI", 8), padx=10, pady=4,
                  cursor="hand2", command=cmd).pack(side="left",
                                                    padx=(0, 6), pady=2)

    tk.Label(win, text="Let op: beelden die al op het SD-kaartje van een "
                       "Eufy-camera staan, zijn alleen via de officiële "
                       "Eufy-app te bekijken (geen open interface).",
             bg="#ffffff", fg="#9ca3af", font=("Segoe UI", 8),
             anchor="w", justify="left", wraplength=640).pack(
                 fill="x", padx=12, pady=(0, 10))

    ververs()
    return win
