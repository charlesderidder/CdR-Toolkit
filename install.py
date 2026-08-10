"""Charles Computeronderhoud - hoofdvenster met zijbalk-navigatie.

Layout: links een witte zijbalk met secties (Onderhoud / Netwerk /
Configuratie), rechts het contentgebied met per pagina een actiekaart.
Taakstatussen en het logvenster zijn altijd zichtbaar onderaan.

Starten:  python main.py        (vraagt om admin-rechten via UAC)
Auto:     python main.py --auto (draait alle taken en sluit daarna)
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import calendar
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from core import crypto, settings, updater
from core.admin import is_admin, reboot_pending, relaunch_as_admin
from core.logger import AppLogger
from tasks import cleanup, drivers, eufy, eufy_web, nest, network, recorder, remote, repair, scripts_repo, windows_update, winget_task, ziggo
from version import __version__

APP_NAAM = "CdR Toolkit"

# ---------------------------------------------------------------- kleuren
BG        = "#f3f4f7"   # vensterachtergrond (lichtgrijs)
SIDEBAR   = "#ffffff"
BORDER    = "#e5e7eb"
TEKST     = "#1f2937"
MUTED     = "#6b7280"
ACCENT    = "#4f6ef7"   # primaire blauw
ACCENT_H  = "#3f5de0"   # hover
NAV_ACT   = "#e8edfc"   # actief navigatie-item
NAV_HOVER = "#f1f3f9"
KAART     = "#ffffff"

STATUS_TEKST = {"pending": "Wachten", "running": "Bezig...",
                "success": "Geslaagd", "failed": "Mislukt"}
STATUS_KLEUR = {"pending": "#9ca3af", "running": "#d97706",
                "success": "#16a34a", "failed": "#dc2626"}
LOG_KLEUR = {"INFO": "#374151", "STEP": "#2563eb", "SUCCESS": "#15803d",
             "WARNING": "#b45309", "ERROR": "#dc2626"}

# Onderhoudstaken voor het statuspaneel
TAKEN = {
    "winget":  "App-updates (Winget)",
    "windows": "Windows Update",
    "drivers": "Drivers & firmware",
    "cleanup": "Cleanup (opschonen)",
}

# Zijbalk-navigatie: (sectienaam, [(sleutel, icoon, label)])
NAVIGATIE = [
    ("Onderhoud", [
        ("alles",    "▶", "Update alles"),
        ("installer", "▼", "Apps"),
        ("windows",  "⊞", "Alleen Windows"),
        ("drivers",  "⚙", "Drivers"),
        ("cleanup",  "♻", "Cleanup"),
    ]),
    ("Netwerk", [
        ("netwerk", "◎", "Netwerk (IP-tools)"),
    ]),
    ("Remote", [
        ("remote", "⇄", "Remote desktop"),
    ]),
    ("Media", [
        ("tv", "▣", "Ziggo TV"),
        ("eufy", "◉", "Camera's"),
    ]),
    ("Scripts", [
        ("scripts", "»", "Bibliotheek"),
    ]),
    ("Office", [
        ("agenda", "◷", "Agenda"),
    ]),
]

# Pagina's: sleutel -> (titel, beschrijving, knoptekst, actie)
PAGINAS = {
    "alles": ("Update alles",
              "Voert achter elkaar uit: app-updates (Winget), Windows Update "
              "en driver/firmware-updates. Eén klik, geen bevestigingen.",
              "Start volledig onderhoud",
              lambda app: app._start(["winget", "windows", "drivers"])),
    "windows": ("Alleen Windows",
                "Forceert Windows Update: zoeken, downloaden en installeren, "
                "volledig onbeheerd via de Windows Update-API.",
                "Windows bijwerken",
                lambda app: app._start(["windows"])),
    "drivers": ("Drivers & firmware",
                "Gebruikt een OEM-tool als die aanwezig is (Dell Command "
                "Update, HP Image Assistant); anders driver-updates via "
                "Windows Update.",
                "Drivers bijwerken",
                lambda app: app._start(["drivers"])),
    "cleanup": ("Cleanup (opschonen)",
                "Leegt temp-mappen (gebruiker + systeem), de Windows "
                "Update-cache en de prullenbak. Toont de vrijgemaakte ruimte.",
                "Cleanup starten",
                lambda app: app._start(["cleanup"])),
}

# Top 25 populairste winget-apps (weergavenaam, winget-ID), alfabetisch.
# Gebaseerd op de meest gedownloade pakketten uit de winget-repository;
# pas gerust aan, maar houd de lijst alfabetisch gesorteerd.
TOP25_APPS = [
    ("7-Zip",                "7zip.7zip"),
    ("Adobe Acrobat Reader", "Adobe.Acrobat.Reader.64-bit"),
    ("AnyDesk",              "AnyDeskSoftwareGmbH.AnyDesk"),
    ("Discord",              "Discord.Discord"),
    ("Everything",           "voidtools.Everything"),
    ("GIMP",                 "GIMP.GIMP"),
    ("Git",                  "Git.Git"),
    ("Google Chrome",        "Google.Chrome"),
    ("KeePassXC",            "KeePassXCTeam.KeePassXC"),
    ("Microsoft PowerToys",  "Microsoft.PowerToys"),
    ("Microsoft Teams",      "Microsoft.Teams"),
    ("Mozilla Firefox",      "Mozilla.Firefox"),
    ("Node.js LTS",          "OpenJS.NodeJS.LTS"),
    ("Notepad++",            "Notepad++.Notepad++"),
    ("OBS Studio",           "OBSProject.OBSStudio"),
    ("Python 3.13",          "Python.Python.3.13"),
    ("qBittorrent",          "qBittorrent.qBittorrent"),
    ("Spotify",              "Spotify.Spotify"),
    ("Steam",                "Valve.Steam"),
    ("TeamViewer",           "TeamViewer.TeamViewer"),
    ("Visual Studio Code",   "Microsoft.VisualStudioCode"),
    ("VLC media player",     "VideoLAN.VLC"),
    ("Windows Terminal",     "Microsoft.WindowsTerminal"),
    ("WinRAR",               "RARLab.WinRAR"),
    ("Zoom",                 "Zoom.Zoom"),
]

CREATE_NO_WINDOW = 0x08000000

_MAAND_NL = [
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
]
_DAG_NL = ["ma", "di", "wo", "do", "vr", "za", "zo"]


def _ics_unfold(lines: list[str]) -> list[str]:
    """Maak iCalendar-regels heel: vervolgregels starten met spatie/tab."""
    uit = []
    for raw in lines:
        if (raw.startswith(" ") or raw.startswith("\t")) and uit:
            uit[-1] += raw[1:]
        else:
            uit.append(raw)
    return uit


def _ics_parse_dt(prop: str, waarde: str):
    """Parse DTSTART uit ICS; geeft een datetime terug of None."""
    prop_u = prop.upper()
    v = waarde.strip()
    try:
        if "VALUE=DATE" in prop_u or (len(v) == 8 and v.isdigit()):
            return datetime.strptime(v[:8], "%Y%m%d")
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ")
        if len(v) >= 15 and v[8] == "T":
            return datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        return None


def _rrule_parse(rrule: str) -> dict:
    """Zet RRULE-tekst om naar een eenvoudige key/value dict."""
    out = {}
    for part in (rrule or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip().upper()] = v.strip().upper()
    return out


def _ics_expand_simple(events: list[dict], vanaf: datetime,
                       tot: datetime, limiet: int) -> list[dict]:
    """Breid eenvoudige herhalingen uit (DAILY/WEEKLY) binnen een venster."""
    dagmap = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    out = []

    for ev in events:
        start = ev.get("start")
        if not isinstance(start, datetime):
            continue
        rrule = _rrule_parse(ev.get("rrule", ""))
        if not rrule:
            if vanaf <= start <= tot:
                out.append(ev)
            continue

        freq = rrule.get("FREQ", "")
        interval = max(1, int(rrule.get("INTERVAL", "1") or "1"))
        until = _ics_parse_dt("DTSTART", rrule.get("UNTIL", "")) \
            if rrule.get("UNTIL") else None

        day_codes = [d for d in rrule.get("BYDAY", "").split(",") if d]
        bydays = {dagmap[d] for d in day_codes if d in dagmap}
        if not bydays:
            bydays = {start.weekday()}

        d = max(vanaf.date(), start.date())
        eind = tot.date()
        while d <= eind:
            occ = datetime.combine(d, start.time())
            if occ < start:
                d += timedelta(days=1)
                continue
            if until and occ > until:
                break

            add = False
            if freq == "DAILY":
                delta = (d - start.date()).days
                add = (delta % interval == 0)
            elif freq == "WEEKLY":
                delta = (d - start.date()).days
                week_idx = delta // 7
                add = (week_idx % interval == 0 and d.weekday() in bydays)
            else:
                # Niet-ondersteunde frequenties: val terug op het origineel.
                if start not in [x.get("start") for x in out] and vanaf <= start <= tot:
                    out.append(ev)
                break

            if add:
                out.append({**ev, "start": occ})
                if len(out) >= limiet:
                    return sorted(out, key=lambda x: x["start"])[:limiet]
            d += timedelta(days=1)

    out.sort(key=lambda x: x["start"])
    return out[:limiet]


def _ics_events_van_url(url: str, limiet: int = 40) -> list[dict]:
    """Haal een ICS op en parse eenvoudige VEVENT-items."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "CharlesOnderhoud/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        tekst = r.read().decode("utf-8", "replace")

    regels = _ics_unfold(tekst.splitlines())
    events = []
    in_event = False
    cur = {}

    for line in regels:
        line = line.strip()
        if not line:
            continue
        if line == "BEGIN:VEVENT":
            in_event = True
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur.get("start"):
                events.append(cur)
            in_event = False
            cur = {}
            continue
        if not in_event or ":" not in line:
            continue

        prop, val = line.split(":", 1)
        p = prop.upper()
        if p.startswith("DTSTART"):
            cur["start"] = _ics_parse_dt(prop, val)
        elif p.startswith("RRULE"):
            cur["rrule"] = val.strip()
        elif p.startswith("SUMMARY"):
            cur["summary"] = val.strip() or "(zonder titel)"
        elif p.startswith("LOCATION"):
            cur["location"] = val.strip()

    nu = datetime.now()
    vanaf = nu - timedelta(days=45)
    tot = nu + timedelta(days=430)
    return _ics_expand_simple(events, vanaf, tot, limiet)


class OnderhoudApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAAM} v{__version__}")
        self.geometry("1040x740")
        self.minsize(880, 620)
        self.configure(bg=BG)

        self.auto = "--auto" in sys.argv          # automatische modus (scheduler)
        self.running = False
        self.ui_queue = queue.Queue()
        self.logger = AppLogger(self.ui_queue)
        self.var_reboot = tk.BooleanVar(value=False)
        self.var_remote_wan = tk.BooleanVar(value=False)  # UPnP-checkbox remote
        self.status_labels = {}
        self.nav_items = {}      # sleutel -> dict(frame, icon, label, actief)
        self.secties = []        # (header_label, sectie_frame, [sleutels])
        self.knoppen = []        # knoppen op de huidige pagina (voor disable)
        self.zoek_var = tk.StringVar()
        self._zoek_placeholder = True
        self._actieve_nav = None

        self._ziggo_was_actief = False  # vorige status van de TV-viewer

        self._build_ui()
        self._toon_pagina("alles")
        self.protocol("WM_DELETE_WINDOW", self._sluiten)
        self.after(100, self._poll_queue)
        self.after(300, self._check_admin)
        self.after(1000, self._check_ziggo_status)
        self.after(4000, lambda: self._update_check(auto=True))
        if self.auto:
            self.after(1000, self._auto_start)

    # --------------------------------------------------------------- layout
    def _build_ui(self):
        hoofd = tk.Frame(self, bg=BG)
        hoofd.pack(fill="both", expand=True)
        self._build_sidebar(hoofd)
        self._build_content(hoofd)

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=SIDEBAR, width=205,
                      highlightthickness=1, highlightbackground=BORDER)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Zoekveld (filtert de navigatie-items)
        zoek = tk.Entry(sb, textvariable=self.zoek_var, relief="flat",
                        bg="#f6f7f9", fg=MUTED, font=("Segoe UI", 8),
                        highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT)
        zoek.pack(fill="x", padx=10, pady=(10, 5), ipady=4)
        zoek.insert(0, "Zoeken...")
        zoek.bind("<FocusIn>", lambda e: self._zoek_focus(zoek, True))
        zoek.bind("<FocusOut>", lambda e: self._zoek_focus(zoek, False))
        self.zoek_var.trace_add("write", lambda *a: self._filter_nav())

        # Navigatiesecties
        for sectie_naam, items in NAVIGATIE:
            hdr = tk.Label(sb, text=sectie_naam.upper(), bg=SIDEBAR,
                           fg="#9aa0ac", font=("Segoe UI", 7, "bold"), anchor="w")
            hdr.pack(fill="x", padx=12, pady=(10, 3))
            frame = tk.Frame(sb, bg=SIDEBAR)
            frame.pack(fill="x", padx=6)
            sleutels = []
            for key, icoon, label in items:
                item = tk.Frame(frame, bg=SIDEBAR, cursor="hand2")
                ic = tk.Label(item, text=icoon, bg=SIDEBAR, fg=MUTED,
                              font=("Segoe UI", 9), width=2)
                ic.pack(side="left", padx=(6, 2), pady=4)
                lb = tk.Label(item, text=label, bg=SIDEBAR, fg=TEKST,
                              font=("Segoe UI", 8), anchor="w")
                lb.pack(side="left", fill="x", expand=True)
                item.pack(fill="x", pady=1)
                for w in (item, ic, lb):
                    w.bind("<Button-1>", lambda e, k=key: self._toon_pagina(k))
                    w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                    w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
                self.nav_items[key] = {"frame": item, "icon": ic,
                                       "label": lb, "actief": False}
                sleutels.append(key)
            self.secties.append((hdr, frame, sleutels))

        tk.Label(sb, text=f"{APP_NAAM}  •  v{__version__}", bg=SIDEBAR,
             fg=MUTED, font=("Segoe UI", 7)).pack(side="bottom", pady=6)

    def _build_content(self, parent):
        content = tk.Frame(parent, bg=BG)
        content.pack(side="left", fill="both", expand=True)

        # Kop: paginatitel links, badges (admin/reboot/update) rechts
        kop = tk.Frame(content, bg=BG)
        kop.pack(fill="x", padx=20, pady=(16, 8))
        self.lbl_paginatitel = tk.Label(kop, text="", bg=BG, fg="#111827",
                                        font=("Segoe UI", 15, "bold"))
        self.lbl_paginatitel.pack(side="left")
        badges = tk.Frame(kop, bg=BG)
        badges.pack(side="right")
        self.lbl_admin = tk.Label(badges, text="...", bg=BG, fg=MUTED,
                                  font=("Segoe UI", 9))
        self.lbl_admin.pack(side="right")
        # Tandwiel rechtsboven: opent de instellingen als popup
        tk.Button(badges, text="⚙", relief="flat", bd=0, bg=BG, fg=MUTED,
                  activebackground=NAV_HOVER, activeforeground=TEKST,
                  font=("Segoe UI", 13), cursor="hand2",
                  command=self._toon_instellingen_popup).pack(
                      side="right", padx=(8, 0))
        self.lbl_reboot = tk.Label(badges, text="", bg=BG, fg="#b45309",
                                   font=("Segoe UI", 9, "bold"))
        self.lbl_reboot.pack(side="right", padx=12)
        self.lbl_update = tk.Label(badges, text="", bg=BG, fg=ACCENT,
                                   font=("Segoe UI", 9, "bold"), cursor="hand2")
        self.lbl_update.pack(side="right", padx=12)
        self.lbl_update.bind("<Button-1>", lambda e: self._check_update_knop())

        # Actiekaart (wordt per pagina gevuld)
        self.kaart = tk.Frame(content, bg=KAART, highlightthickness=1,
                              highlightbackground=BORDER)
        self.kaart.pack(fill="x", padx=20, pady=(0, 10))

        # Logvenster
        log_frame = tk.Frame(content, bg=KAART, highlightthickness=1,
                             highlightbackground=BORDER)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        tk.Label(log_frame, text="LOGBOEK", bg=KAART, fg="#9aa0ac",
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(
                     fill="x", padx=14, pady=(10, 0))
        tekst_frame = tk.Frame(log_frame, bg=KAART)
        tekst_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.log_text = tk.Text(tekst_frame, bg="#fbfcfe", fg="#374151",
                                insertbackground="#374151", relief="flat",
                                font=("Consolas", 9), state="normal",
                                wrap="word", highlightthickness=1,
                                highlightbackground=BORDER)
        scroll = ttk.Scrollbar(tekst_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        for level, kleur in LOG_KLEUR.items():
            self.log_text.tag_configure(level, foreground=kleur)
        # tekst selecteerbaar + kopieerbaar, maar niet bewerkbaar
        self.log_text.bind("<Key>", self._log_toets)
        self.log_menu = tk.Menu(self, tearoff=0, bg=KAART, fg=TEKST,
                                activebackground=ACCENT,
                                activeforeground="white")
        self.log_menu.add_command(
            label="Kopiëer (Ctrl+C)",
            command=lambda: self.log_text.event_generate("<<Copy>>"))
        self.log_menu.add_command(label="Alles selecteren",
                                  command=self._log_alles_selecteren)
        self.log_menu.add_separator()
        self.log_menu.add_command(label="Logboek legen",
                                  command=self._leeg_log)
        self.log_text.bind(
            "<Button-3>", lambda e: self.log_menu.tk_popup(e.x_root, e.y_root))

    # ------------------------------------------------------ zijbalk-gedrag
    def _zoek_focus(self, entry, binnengekomen: bool):
        if binnengekomen and self._zoek_placeholder:
            entry.delete(0, "end")
            entry.config(fg=TEKST)
            self._zoek_placeholder = False
        elif not binnengekomen and not entry.get():
            entry.insert(0, "Zoeken...")
            entry.config(fg=MUTED)
            self._zoek_placeholder = True

    def _filter_nav(self):
        """Toon alleen navigatie-items die bij de zoekterm passen."""
        q = "" if self._zoek_placeholder else self.zoek_var.get().strip().lower()
        for hdr, frame, _ in self.secties:
            hdr.pack_forget()
            frame.pack_forget()
        for hdr, frame, sleutels in self.secties:
            matches = [k for k in sleutels
                       if q in self.nav_items[k]["label"].cget("text").lower()]
            if not matches:
                continue
            hdr.pack(fill="x", padx=14, pady=(14, 4))
            frame.pack(fill="x", padx=8)
            for key in sleutels:
                self.nav_items[key]["frame"].pack_forget()
            for key in matches:
                self.nav_items[key]["frame"].pack(fill="x", pady=1)

    def _nav_hover(self, key, binnen: bool):
        item = self.nav_items[key]
        if item["actief"]:
            return
        bg = NAV_HOVER if binnen else SIDEBAR
        for w in (item["frame"], item["icon"], item["label"]):
            w.config(bg=bg)

    def _style_nav(self, key, actief: bool):
        item = self.nav_items[key]
        item["actief"] = actief
        bg = NAV_ACT if actief else SIDEBAR
        for w in (item["frame"], item["icon"], item["label"]):
            w.config(bg=bg)
        item["icon"].config(fg=ACCENT if actief else MUTED)

    # --------------------------------------------------------------- pagina
    def _toon_pagina(self, key):
        if self._actieve_nav in self.nav_items:
            self._style_nav(self._actieve_nav, False)
        self._actieve_nav = key
        if key in self.nav_items:
            self._style_nav(key, True)

        for w in self.kaart.winfo_children():
            w.destroy()
        self.knoppen = []

        if key == "netwerk":
            self.lbl_paginatitel.config(text="Netwerk (IP-tools)")
            self._render_netwerk()
        elif key == "installer":
            self.lbl_paginatitel.config(text="Apps (bijwerken & installeren)")
            self._render_installer()
        elif key == "remote":
            self.lbl_paginatitel.config(text="Remote desktop")
            self._render_remote()
        elif key == "tv":
            self.lbl_paginatitel.config(text="Ziggo TV")
            self._render_tv()
        elif key == "scripts":
            self.lbl_paginatitel.config(text="Scripts-bibliotheek")
            self._render_scripts()
        elif key == "eufy":
            self.lbl_paginatitel.config(text="Camera's (Eufy & Google Nest)")
            self._render_eufy()
        elif key == "agenda":
            self.lbl_paginatitel.config(text="Office Agenda")
            self._render_agenda()
        else:
            titel, beschrijving, knoptekst, actie = PAGINAS[key]
            self.lbl_paginatitel.config(text=titel)
            tk.Label(self.kaart, text=beschrijving, bg=KAART, fg=MUTED,
                     font=("Segoe UI", 9), wraplength=620,
                     justify="left").pack(anchor="w", padx=16, pady=(14, 6))
            balk = tk.Frame(self.kaart, bg=KAART)
            balk.pack(fill="x", padx=16, pady=(0, 14))
            b = tk.Button(balk, text=knoptekst, relief="flat", bd=0,
                          bg=ACCENT, fg="white", activebackground=ACCENT_H,
                          activeforeground="white", font=("Segoe UI", 9, "bold"),
                          padx=16, pady=8, cursor="hand2",
                          command=lambda: actie(self))
            b.pack(side="left")
            self.knoppen.append(b)
        if self.running:
            self._set_knoppen("disabled")

    def _toon_instellingen_popup(self):
        """Instellingen als eigen popup-venster met duidelijke sectie-indeling."""
        if getattr(self, "_inst_win", None) and \
                self._inst_win.winfo_exists():
            self._inst_win.lift()
            return
        win = tk.Toplevel(self)
        self._inst_win = win
        win.title(f"Instellingen — {APP_NAAM}")
        win.geometry("620x760")
        win.minsize(520, 620)
        win.configure(bg=BG)
        win.transient(self)

        outer = tk.Frame(win, bg=BG)
        outer.pack(fill="both", expand=True, padx=12, pady=(10, 6))
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg=BG)
        canvas_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def _body_config(_evt=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _canvas_config(evt):
            canvas.itemconfigure(canvas_window, width=evt.width)

        body.bind("<Configure>", _body_config)
        canvas.bind("<Configure>", _canvas_config)

        def _mw(evt):
            canvas.yview_scroll(int(-evt.delta / 120), "units")

        def _bind_mw(_evt):
            canvas.bind_all("<MouseWheel>", _mw)

        def _unbind_mw(_evt):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mw)
        canvas.bind("<Leave>", _unbind_mw)

        def sectie(titel):
            sf = tk.LabelFrame(body, text=f"  {titel}  ", bg=KAART, fg=TEKST,
                               font=("Segoe UI", 8, "bold"), bd=0,
                               highlightthickness=1,
                               highlightbackground=BORDER,
                               padx=10, pady=6, labelanchor="nw")
            sf.pack(fill="x", pady=(0, 8))
            return sf

        def knoprij(ouder, items):
            rij = tk.Frame(ouder, bg=KAART)
            rij.pack(fill="x", pady=(2, 0))
            for tekst, cmd in items:
                b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                              bg="#eef1f6", fg=TEKST,
                              activebackground="#dfe5f0",
                              font=("Segoe UI", 8), padx=8, pady=3,
                              cursor="hand2", command=cmd)
                b.pack(side="left", padx=(0, 8), pady=2)
                self.knoppen.append(b)

        # ------------------------------------------------------ Algemeen
        s1 = sectie("Algemeen")
        tk.Checkbutton(s1, text="Automatisch herstarten indien nodig "
                                "(na Windows Updates)",
                       variable=self.var_reboot, bg=KAART, fg=TEKST,
                       selectcolor="#ffffff", activebackground=KAART,
                       font=("Segoe UI", 9)).pack(anchor="w")
        self.var_voorgrond = tk.BooleanVar(
            value=bool(settings.get("altijd_voorgrond", True)))
        tk.Checkbutton(s1, text="Vensters altijd op voorgrond "
                                "(Ziggo TV & Eufy-dashboard)",
                       variable=self.var_voorgrond,
                       command=self._sla_voorgrond_op,
                       bg=KAART, fg=TEKST, selectcolor="#ffffff",
                       activebackground=KAART,
                       font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        tk.Label(s1, text="Instellingen, scripts en logs staan in "
                          "Documenten\\CharlesOnderhoud. Wachtwoorden worden "
                          "versleuteld opgeslagen (Windows DPAPI).",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", pady=(4, 0))

        # ------------------------------------------------------ Office agenda
        so = sectie("Office agenda")
        tk.Label(so, text="Outlook ICS-link (delen/publiceren) voor de "
                  "agendaweergave in de app.",
             bg=KAART, fg=MUTED, font=("Segoe UI", 8),
             anchor="w", justify="left", wraplength=560).pack(fill="x")
        self.office_ics_var = tk.StringVar(value=settings.get("office_agenda_ics", ""))
        tk.Entry(so, textvariable=self.office_ics_var, relief="flat",
             bg="#f6f7f9", fg=TEKST, font=("Segoe UI", 9),
             highlightthickness=1, highlightbackground=BORDER,
             highlightcolor=ACCENT).pack(fill="x", pady=(4, 2), ipady=2)
        knoprij(so, [("Opslaan", self._office_agenda_opslaan),
                 ("Open Agenda-pagina", lambda: self._toon_pagina("agenda"))])

        # --------------------------------------- Camera's (Eufy dashboard)
        sc = sectie("Camera's (Eufy beveiligingsdashboard)")
        cam_frame = tk.Frame(sc, bg=KAART)
        cam_frame.pack(fill="x", pady=(0, 2))
        self.cam_listbox = tk.Listbox(
            cam_frame, height=4, relief="flat", bg="#f6f7f9", fg=TEKST,
            font=("Segoe UI", 9), highlightthickness=1,
            highlightbackground=BORDER, selectbackground=ACCENT,
            selectforeground="white", exportselection=False,
            activestyle="none")
        cam_scroll = ttk.Scrollbar(cam_frame, command=self.cam_listbox.yview)
        self.cam_listbox.configure(yscrollcommand=cam_scroll.set)
        cam_scroll.pack(side="right", fill="y")
        self.cam_listbox.pack(fill="x")
        tk.Label(sc, text="Inloggegevens in de URL zijn verborgen en worden "
                          "versleuteld opgeslagen.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", pady=(2, 0))
        knoprij(sc, [("Voeg toe", self._cam_toevoegen),
                     ("Bewerk", self._cam_bewerk),
                     ("Aan/Uit", self._cam_toggle),
                     ("Verwijder", self._cam_verwijder),
                     ("▲ Omhoog", lambda: self._cam_schuif(-1)),
                     ("▼ Omlaag", lambda: self._cam_schuif(1)),
                     ("Importeer meerdere", self._cam_importeer)])
        self._cam_vul_lijst()

        # --------------------------------------- Raster (dashboard)
        sra = sectie("Rasterweergave (dashboard)")
        self._raster_knoppen = {}
        rgrid = tk.Frame(sra, bg=KAART)
        rgrid.pack(fill="x")
        for i, (sleutel, label) in enumerate(eufy.RASTER_SJABLONEN):
            b = tk.Button(rgrid, text=label, relief="flat", bd=0,
                          bg="#eef1f6", fg=TEKST, activebackground="#dfe5f0",
                          font=("Segoe UI", 8), padx=10, pady=4,
                          cursor="hand2", anchor="w",
                          command=lambda k=sleutel: self._raster_kies(k))
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 8),
                   pady=2)
            rgrid.columnconfigure(i % 2, weight=1)
            self._raster_knoppen[sleutel] = b
            self.knoppen.append(b)
        rij = tk.Frame(sra, bg=KAART)
        rij.pack(fill="x", pady=(4, 0))
        tk.Label(rij, text="Aangepast:  rijen", bg=KAART, fg=TEKST,
                 font=("Segoe UI", 9)).pack(side="left")
        self.raster_r = tk.StringVar(
            value=str(settings.get("cam_raster_rijen", 2)))
        self.raster_k = tk.StringVar(
            value=str(settings.get("cam_raster_kolommen", 2)))
        tk.Entry(rij, textvariable=self.raster_r, width=4, relief="flat",
                 bg="#f6f7f9", fg=TEKST, font=("Segoe UI", 9),
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT, justify="center").pack(
                     side="left", padx=(4, 8), ipady=2)
        tk.Label(rij, text="kolommen", bg=KAART, fg=TEKST,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Entry(rij, textvariable=self.raster_k, width=4, relief="flat",
                 bg="#f6f7f9", fg=TEKST, font=("Segoe UI", 9),
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT, justify="center").pack(
                     side="left", padx=(4, 8), ipady=2)
        tk.Button(rij, text="Toepassen", relief="flat", bd=0, bg="#eef1f6",
                  fg=TEKST, activebackground="#dfe5f0", font=("Segoe UI", 8),
                  padx=10, pady=2, cursor="hand2",
                  command=lambda: self._raster_kies("custom")).pack(
                      side="left")
        tk.Label(sra, text="Rijen × kolommen (max 8×8). Bij N+1-sjablonen "
                           "krijgt de eerste camera het grote vak.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8), wraplength=560,
                 justify="left").pack(fill="x", pady=(4, 0))
        self._raster_markeer()

        # --------------------------------------------- Google Nest camera's
        sn = sectie("Google Nest camera's")
        tk.Label(sn, text="Koppel je Google-account om al je Nest-camera's "
                          "automatisch toe te voegen. Camera's hoeven NIET "
                          "in Google Cloud gekoppeld te worden — ze moeten "
                          "in de Google Home app staan onder hetzelfde "
                          "account. Eenmalig nodig: 1) Cloud-project met "
                          "'Smart Device Management API' aan, 2) OAuth-client "
                          "(Desktop-app), 3) Device Access-project (eenmalig "
                          "$5). Vul hieronder het Device Access project-ID in "
                          "(UUID), niet de projectnaam.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8), wraplength=560,
                 justify="left").pack(fill="x", pady=(0, 4))
        nestcfg = nest.cfg()
        self.nest_id_var = tk.StringVar(value=nestcfg["client_id"])
        self.nest_sec_var = tk.StringVar(value=nestcfg["client_secret"])
        self.nest_proj_var = tk.StringVar(value=nestcfg["project_id"])
        for label, var, geheim in (("Client-ID:", self.nest_id_var, False),
                                   ("Client-secret:", self.nest_sec_var, True),
                                   ("Project-ID:", self.nest_proj_var, False)):
            rij = tk.Frame(sn, bg=KAART)
            rij.pack(fill="x", pady=1)
            tk.Label(rij, text=label, bg=KAART, fg=TEKST, width=13,
                     font=("Segoe UI", 9), anchor="w").pack(side="left")
            tk.Entry(rij, textvariable=var, relief="flat", bg="#f6f7f9",
                     fg=TEKST, show="•" if geheim else "",
                     font=("Segoe UI", 9), highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=ACCENT
                     ).pack(side="left", fill="x", expand=True, ipady=2)
        self.lbl_nest_status = tk.Label(sn, text="", bg=KAART,
                                        font=("Segoe UI", 8), anchor="w")
        self.lbl_nest_status.pack(fill="x", pady=(4, 2))
        knoprij(sn, [("Opslaan", self._sla_nest_op),
                     ("Inloggen met Google", self._nest_login),
                     ("Camera's ophalen", self._nest_ophalen),
                     ("Ontkoppelen", self._nest_ontkoppel),
                     ("Handleiding", self._nest_handleiding),
                     ("ℹ Instructies",
                      lambda: nest.toon_instructies(self._inst_win))])
        self._nest_status_ververs()

        # ------------------------------------------------- Remote desktop
        sr = sectie("Remote desktop")
        rij_pw = tk.Frame(sr, bg=KAART)
        rij_pw.pack(fill="x", pady=(0, 2))
        tk.Label(rij_pw, text="Vast wachtwoord:", bg=KAART, fg=TEKST,
                 font=("Segoe UI", 9)).pack(side="left")
        self.remote_pw_var = tk.StringVar(value=crypto.ontsleutel(
            settings.get("remote_wachtwoord", "")))
        tk.Entry(rij_pw, textvariable=self.remote_pw_var, width=22,
                 relief="flat", bg="#f6f7f9", fg=TEKST, show="•",
                 font=("Segoe UI", 10), highlightthickness=1,
                 highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", padx=8, ipady=3)
        knoprij(sr, [("Wachtwoord opslaan", self._sla_remote_pw_op)])
        tk.Label(sr, text="Leeg = willekeurig wachtwoord per sessie. Alleen "
                          "gebruiken op vertrouwde LAN-netwerken.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x")

        # ------------------------------------------------ Automatisering
        s2 = sectie("Automatisering")
        tk.Label(s2, text="Draai alle onderhoudstaken automatisch via de "
                          "Windows Taakplanner.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x")
        knoprij(s2, [("Plan automatisch", self._plan_taak),
                     ("Verwijder planning", self._verwijder_planning)])

        # ------------------------------------------------------ Logboek
        s3 = sectie("Logboek")
        knoprij(s3, [("Exporteer log", self._exporteer_log),
                     ("Leeg log", self._leeg_log)])

        # ------------------------------------------------------ Herstel
        s4 = sectie("Herstel")
        knoprij(s4, [("Herstel Winget",
                      lambda: self._run_action(repair.repair_winget)),
                     ("Herstel Windows Update",
                      lambda: self._run_action(repair.repair_windows_update))])

        # --------------------------------------------------- Applicatie
        s5 = sectie("Applicatie")
        tk.Label(s5, text=f"{APP_NAAM}  •  versie {__version__}",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x")
        knoprij(s5, [("Zoek naar update", self._check_update_knop),
                     ("Download handmatig", self._download_handmatig)])

        footer = tk.Frame(win, bg=BG)
        footer.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(footer, text="Sluiten", relief="flat", bd=0,
                  bg=ACCENT, fg="white", activebackground=ACCENT_H,
                  font=("Segoe UI", 9, "bold"), padx=18, pady=6,
                  cursor="hand2", command=win.destroy).pack(
                      anchor="e", pady=(2, 0))

    # ---------------------------------------------- camerabeheer (popup)
    def _cam_vul_lijst(self):
        """Cameralijst met gemaskeerde URL's (geen inloggegevens zichtbaar)."""
        if not getattr(self, "cam_listbox", None) or \
                not self.cam_listbox.winfo_exists():
            return
        self.cam_listbox.delete(0, "end")
        for c in eufy.cams():
            uit = "" if c.get("aan", True) else "  [UIT]"
            self.cam_listbox.insert(
                "end", f"{c.get('naam', '')}{uit}  —  {eufy.weergave(c)}")
        if eufy.cams():
            self.cam_listbox.selection_set(0)

    def _cam_dialoog(self, titel, naam="", url=""):
        """Vraag naam + RTSP-URL; geeft (naam, url) of None bij annuleren."""
        dlg = tk.Toplevel(self._inst_win or self)
        dlg.title(titel)
        dlg.geometry("480x190")
        dlg.configure(bg=KAART)
        dlg.transient(self._inst_win or self)
        dlg.grab_set()
        resultaat = {}
        tk.Label(dlg, text="Naam (optioneel):", bg=KAART, fg=TEKST,
                 font=("Segoe UI", 9), anchor="w").pack(
                     fill="x", padx=14, pady=(12, 0))
        naam_var = tk.StringVar(value=naam)
        tk.Entry(dlg, textvariable=naam_var, relief="flat", bg="#f6f7f9",
                 fg=TEKST, font=("Segoe UI", 10), highlightthickness=1,
                 highlightbackground=BORDER).pack(
                     fill="x", padx=14, pady=(2, 8), ipady=3)
        tk.Label(dlg, text="RTSP-URL (bijv. rtsp://user:wachtwoord@IP:8554/live0):",
                 bg=KAART, fg=TEKST, font=("Segoe UI", 9),
                 anchor="w").pack(fill="x", padx=14)
        url_var = tk.StringVar(value=url)
        tk.Entry(dlg, textvariable=url_var, relief="flat", bg="#f6f7f9",
                 fg=TEKST, font=("Segoe UI", 10), highlightthickness=1,
                 highlightbackground=BORDER).pack(
                     fill="x", padx=14, pady=(2, 10), ipady=3)

        def ok():
            u = url_var.get().strip()
            if not u.lower().startswith("rtsp://"):
                messagebox.showerror(titel, "De URL moet met rtsp:// beginnen.",
                                     parent=dlg)
                return
            resultaat["waarde"] = (naam_var.get().strip() or u, u)
            dlg.destroy()

        rij = tk.Frame(dlg, bg=KAART)
        rij.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(rij, text="Opslaan", relief="flat", bd=0, bg=ACCENT,
                  fg="white", activebackground=ACCENT_H,
                  font=("Segoe UI", 9, "bold"), padx=14, pady=5,
                  cursor="hand2", command=ok).pack(side="left", padx=(0, 8))
        tk.Button(rij, text="Annuleren", relief="flat", bd=0, bg="#eef1f6",
                  fg=TEKST, activebackground="#dfe5f0", font=("Segoe UI", 9),
                  padx=14, pady=5, cursor="hand2",
                  command=dlg.destroy).pack(side="left")
        dlg.wait_window()
        return resultaat.get("waarde")

    def _cam_toevoegen(self):
        r = self._cam_dialoog("Camera toevoegen")
        if not r:
            return
        lijst = eufy.cams()
        lijst.append({"naam": r[0], "url": r[1]})
        eufy.sla_cams_op(lijst)
        self._cam_vul_lijst()
        self.logger.log(f"Camera toegevoegd: {r[0]}", "SUCCESS")

    def _cam_bewerk(self):
        sel = self.cam_listbox.curselection()
        if not sel:
            self.logger.log("Selecteer eerst een camera in de lijst.",
                            "WARNING")
            return
        lijst = eufy.cams()
        cam = lijst[sel[0]]
        if cam.get("type") == "nest":
            self.logger.log("Nest-camera's worden via Google beheerd; ze "
                            "kunnen hier alleen verwijderd worden.", "WARNING")
            return
        r = self._cam_dialoog("Camera bewerken",
                              cam.get("naam", ""), cam.get("url", ""))
        if not r:
            return
        lijst[sel[0]] = {"naam": r[0], "url": r[1]}
        eufy.sla_cams_op(lijst)
        self._cam_vul_lijst()
        self.cam_listbox.selection_set(sel[0])
        self.logger.log(f"Camera bijgewerkt: {r[0]}", "SUCCESS")

    def _cam_toggle(self):
        """Schakel de geselecteerde camera aan of uit (blijft bewaard)."""
        sel = self.cam_listbox.curselection()
        if not sel:
            self.logger.log("Selecteer eerst een camera in de lijst.",
                            "WARNING")
            return
        lijst = eufy.cams()
        c = lijst[sel[0]]
        c["aan"] = not c.get("aan", True)
        eufy.sla_cams_op(lijst)
        self._cam_vul_lijst()
        self.cam_listbox.selection_set(sel[0])
        self.logger.log(f"Camera '{c.get('naam')}' "
                        f"{'ingeschakeld' if c['aan'] else 'uitgeschakeld'}.",
                        "SUCCESS")

    def _cam_verwijder(self):
        sel = self.cam_listbox.curselection()
        if not sel:
            self.logger.log("Selecteer eerst een camera in de lijst.",
                            "WARNING")
            return
        lijst = eufy.cams()
        weg = lijst.pop(sel[0])
        eufy.sla_cams_op(lijst)
        self._cam_vul_lijst()
        self.logger.log(f"Camera verwijderd: {weg.get('naam')}", "SUCCESS")

    def _cam_schuif(self, richting):
        """Verplaats de geselecteerde camera omhoog (-1) of omlaag (+1)."""
        sel = self.cam_listbox.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + richting
        lijst = eufy.cams()
        if not (0 <= j < len(lijst)):
            return
        lijst[i], lijst[j] = lijst[j], lijst[i]
        eufy.sla_cams_op(lijst)
        self._cam_vul_lijst()
        self.cam_listbox.selection_set(j)

    def _cam_importeer(self):
        """Plak-dialoog: meerdere RTSP-streams tegelijk importeren."""
        dlg = tk.Toplevel(self._inst_win or self)
        dlg.title("RTSP-streams importeren")
        dlg.geometry("560x320")
        dlg.configure(bg=KAART)
        dlg.transient(self._inst_win or self)
        tk.Label(dlg, text="Plak hieronder één stream per regel. Toegestane "
                           "vormen:  'naam,rtsp://…'  of alleen  'rtsp://…'.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9),
                 justify="left").pack(anchor="w", padx=12, pady=(10, 4))
        tekst = tk.Text(dlg, height=10, font=("Consolas", 9), relief="flat",
                        bg="#f6f7f9", fg=TEKST, highlightthickness=1,
                        highlightbackground=BORDER)
        tekst.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        def doe():
            lijst = eufy.cams()
            n = 0
            for r in tekst.get("1.0", "end").splitlines():
                r = r.strip()
                if not r:
                    continue
                if r.lower().startswith("rtsp://"):
                    naam, url = "", r
                elif "," in r:
                    naam, url = (x.strip() for x in r.split(",", 1))
                else:
                    continue
                if url.lower().startswith("rtsp://"):
                    lijst.append({"naam": naam or url, "url": url})
                    n += 1
            eufy.sla_cams_op(lijst)
            dlg.destroy()
            self._cam_vul_lijst()
            self.logger.log(f"{n} stream(s) geïmporteerd.",
                            "SUCCESS" if n else "WARNING")

        tk.Button(dlg, text="Importeer", relief="flat", bd=0, bg=ACCENT,
                  fg="white", activebackground=ACCENT_H,
                  font=("Segoe UI", 9, "bold"), padx=16, pady=6,
                  cursor="hand2", command=doe).pack(pady=(0, 10))

    # ------------------------------------------------------ rasterkeuze
    def _raster_markeer(self):
        """Markeer de actieve rasterknop in de instellingen-popup."""
        actief = eufy.raster_keuze()
        for k, b in self._raster_knoppen.items():
            aan = (k == actief)
            b.config(bg=ACCENT if aan else "#eef1f6",
                     fg="white" if aan else TEKST)

    def _raster_kies(self, sleutel):
        """Kies een rastersjabloon; bij 'custom' de rijen/kolommen gebruiken."""
        if sleutel == "custom":
            try:
                r = int(self.raster_r.get())
                k = int(self.raster_k.get())
            except ValueError:
                self.logger.log("Voer hele getallen in voor rijen en "
                                "kolommen.", "WARNING")
                return
            eufy.zet_raster("custom", r, k)
            self.logger.log(f"Aangepast raster: "
                            f"{settings.get('cam_raster_rijen')}×"
                            f"{settings.get('cam_raster_kolommen')}.",
                            "SUCCESS")
        else:
            eufy.zet_raster(sleutel)
            self.logger.log(
                f"Raster: {dict(eufy.RASTER_SJABLONEN)[sleutel]}.", "SUCCESS")
        self._raster_markeer()

    def _render_remote(self):
        """Remote-pagina: ingebouwde webserver voor bediening via de browser."""
        f = self.kaart

        def sectie(tekst):
            tk.Label(f, text=tekst.upper(), bg=KAART, fg="#9aa0ac",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(
                         fill="x", padx=16, pady=(12, 2))

        def knop(rij, tekst, cmd, primair=False):
            b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                          bg=ACCENT if primair else "#eef1f6",
                          fg="white" if primair else TEKST,
                          activebackground=ACCENT_H if primair else "#dfe5f0",
                          font=("Segoe UI", 8, "bold" if primair else "normal"),
                          padx=12, pady=5, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8))
            self.knoppen.append(b)

        tk.Label(f, text="Neem deze computer over vanaf een telefoon, tablet "
                         "of andere pc via de browser. Volledig ingebouwd — "
                         "geen RustDesk, RDP of andere software nodig.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(14, 2))

        actief = remote.actief()

        sectie("Status")
        rij0 = tk.Frame(f, bg=KAART)
        rij0.pack(fill="x", padx=16, pady=(0, 2))
        tk.Label(rij0, text="● Actief" if actief else "● Gestopt",
                 bg=KAART, fg="#16a34a" if actief else MUTED,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        if actief:
            urls = remote.urls()
            info = tk.Frame(f, bg="#f6f7f9", highlightthickness=1,
                            highlightbackground=BORDER)
            info.pack(fill="x", padx=16, pady=(4, 4))

            def kopieerknop(rij, tekst):
                b = tk.Button(rij, text="Kopieer", relief="flat", bd=0,
                              bg="#e5e9f0", fg=TEKST, activebackground="#dfe5f0",
                              font=("Segoe UI", 7), padx=8, pady=2,
                              cursor="hand2",
                              command=lambda: self._kopieer(tekst))
                b.pack(side="right")
                self.knoppen.append(b)

            rij_lan = tk.Frame(info, bg="#f6f7f9")
            rij_lan.pack(fill="x", padx=10, pady=(8, 2))
            tk.Label(rij_lan, text=f"LAN-adres:  {urls.get('lan') or '-'}",
                     bg="#f6f7f9", fg=TEKST, font=("Consolas", 10),
                     anchor="w").pack(side="left")
            kopieerknop(rij_lan, urls.get("lan") or "")
            if urls.get("wan"):
                rij_wan = tk.Frame(info, bg="#f6f7f9")
                rij_wan.pack(fill="x", padx=10, pady=2)
                tk.Label(rij_wan, text=f"Internet:  {urls['wan']}",
                         bg="#f6f7f9", fg=TEKST, font=("Consolas", 10),
                         anchor="w").pack(side="left")
                kopieerknop(rij_wan, urls["wan"])
            rij_pw = tk.Frame(info, bg="#f6f7f9")
            rij_pw.pack(fill="x", padx=10, pady=(6, 8))
            # wachtwoord standaard verborgen (meekijkers op het scherm of
            # via de remote-verbinding zien het niet); 'Toon' wisselt zicht
            lbl_pw = tk.Label(rij_pw, text="Wachtwoord:  ••••-••••",
                              bg="#f6f7f9", fg="#111827",
                              font=("Consolas", 13, "bold"), anchor="w")
            lbl_pw.pack(side="left")

            def _toon_pw():
                verborgen = "•" in lbl_pw.cget("text")
                lbl_pw.config(
                    text=(f"Wachtwoord:  {remote.wachtwoord()}" if verborgen
                          else "Wachtwoord:  ••••-••••"))

            b = tk.Button(rij_pw, text="Toon", relief="flat", bd=0,
                          bg="#e5e9f0", fg=TEKST, activebackground="#dfe5f0",
                          font=("Segoe UI", 7), padx=8, pady=2,
                          cursor="hand2", command=_toon_pw)
            b.pack(side="left", padx=8)
            self.knoppen.append(b)
            kopieerknop(rij_pw, remote.wachtwoord())
            tk.Label(f, text="Open het adres in de browser van het andere "
                             "apparaat en log in met dit wachtwoord.",
                     bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                     anchor="w").pack(fill="x", padx=16)

        sectie("Bediening")
        rij1 = tk.Frame(f, bg=KAART)
        rij1.pack(fill="x", padx=16, pady=(0, 2))
        if not actief:
            knop(rij1, "Start webserver",
                 lambda: self._run_action(
                     lambda log: remote.start(
                         log, internet=self.var_remote_wan.get())),
                 primair=True)
            tk.Checkbutton(rij1, text="Ook via internet bereikbaar "
                                      "(UPnP-poort openen)",
                           variable=self.var_remote_wan, bg=KAART, fg=TEKST,
                           selectcolor="#ffffff", activebackground=KAART,
                           font=("Segoe UI", 9)).pack(side="left", padx=8)
        else:
            knop(rij1, "Stop webserver",
                 lambda: self._run_action(remote.stop))

        sectie("Let op")
        tk.Label(f, text="HTTP zonder TLS — gebruik dit op vertrouwde "
                         "netwerken. Het UAC-scherm (secure desktop) is niet "
                         "zichtbaar of bedienbaar op afstand. Een via de "
                         "webpagina aangepaste schermresolutie wordt bij het "
                         "stoppen van de webserver weer hersteld.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(0, 4))
        tk.Frame(f, bg=KAART, height=10).pack()

    def _render_tv(self):
        """Ziggo TV-pagina: start/stop de viewer (apart WebView2-venster)."""
        f = self.kaart

        def sectie(tekst):
            tk.Label(f, text=tekst.upper(), bg=KAART, fg="#9aa0ac",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(
                         fill="x", padx=16, pady=(12, 2))

        def knop(rij, tekst, cmd, primair=False):
            b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                          bg=ACCENT if primair else "#eef1f6",
                          fg="white" if primair else TEKST,
                          activebackground=ACCENT_H if primair else "#dfe5f0",
                          font=("Segoe UI", 8, "bold" if primair else "normal"),
                          padx=12, pady=5, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8))
            self.knoppen.append(b)

        tk.Label(f, text="Kijk Ziggo TV (ziggogo.tv) in een eigen venster met "
                         "WebView2. Het venster blijft op de voorgrond en "
                         "bevat verder geen knoppen of overlays.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(14, 2))

        actief = ziggo.actief()
        sectie("Status")
        rij0 = tk.Frame(f, bg=KAART)
        rij0.pack(fill="x", padx=16, pady=(0, 2))
        tk.Label(rij0, text="● Actief" if actief else "● Gestopt",
                 bg=KAART, fg="#16a34a" if actief else MUTED,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        sectie("Bediening")
        rij1 = tk.Frame(f, bg=KAART)
        rij1.pack(fill="x", padx=16, pady=(0, 2))
        if not actief:
            knop(rij1, "Start Ziggo TV",
                 lambda: self._run_action(ziggo.start), primair=True)
        else:
            knop(rij1, "Stop Ziggo TV",
                 lambda: self._run_action(ziggo.stop))

        sectie("Let op")
        tk.Label(f, text="• Log eenmalig in met je Ziggo-account; de login "
                         "blijft bewaard (Documenten\\CharlesOnderhoud\\webview2).\n"
                         "• Video loopt via DRM van de Edge WebView2-runtime; "
                         "werkt het bij jou niet, update dan de runtime.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(0, 4))
        tk.Frame(f, bg=KAART, height=10).pack()

    def _sla_voorgrond_op(self):
        """Sla de altijd-voorgrond-instelling op en pas hem direct toe."""
        aan = self.var_voorgrond.get()
        settings.set("altijd_voorgrond", aan)
        eufy.set_topmost(aan, self.logger.log)

    def _sla_remote_pw_op(self):
        """Bewaar het vaste remote-wachtwoord versleuteld in config.json."""
        pw = self.remote_pw_var.get().strip()
        settings.set("remote_wachtwoord", crypto.versleutel(pw))
        if pw:
            self.logger.log("Vast remote-wachtwoord opgeslagen "
                            "(actief vanaf de volgende serverstart).", "SUCCESS")
        else:
            self.logger.log("Vast wachtwoord gewist: elke sessie krijgt weer "
                            "een willekeurig wachtwoord.", "SUCCESS")

    # ------------------------------------------------------ Google Nest
    def _nest_status_ververs(self):
        """Werk het koppel-statuslabel in de instellingen-popup bij."""
        lbl = getattr(self, "lbl_nest_status", None)
        if lbl and lbl.winfo_exists():
            aan = nest.gekoppeld()
            lbl.config(text=("● Gekoppeld met je Google-account" if aan
                             else "● Niet gekoppeld"),
                       fg="#16a34a" if aan else MUTED)

    def _sla_nest_op(self):
        """Bewaar client-ID/secret/project-ID (versleuteld) in config.json."""
        nest.sla_cfg(self.nest_id_var.get(), self.nest_sec_var.get(),
                     self.nest_proj_var.get())
        self.logger.log("Nest koppel-gegevens opgeslagen.", "SUCCESS")

    def _nest_login(self):
        """Start de Google OAuth-flow (browser) in een achtergrondthread."""
        self._sla_nest_op()
        self._run_action(nest.login)

    def _nest_ontkoppel(self):
        nest.ontkoppel()
        self._nest_status_ververs()
        self.logger.log("Google Nest ontkoppeld; tokens verwijderd.",
                        "SUCCESS")

    def _nest_handleiding(self):
        """Open Google's Device Access-stapsgids in de browser."""
        import webbrowser
        webbrowser.open(
            "https://developers.google.com/nest/device-access/get-started")
        self.logger.log("Nest-handleiding geopend in de browser.", "INFO")

    def _nest_ophalen(self):
        """Haal alle Nest-camera's op en voeg ze aan de cameralijst toe."""
        if not nest.gekoppeld():
            self.logger.log("Nog niet gekoppeld — klik eerst op "
                            "'Inloggen met Google'.", "WARNING")
            return

        def doe(log):
            try:
                gevonden = nest.lijst_cams()
            except RuntimeError as exc:
                log(f"Nest-camera's ophalen mislukt: {exc}", "ERROR")
                return
            if not gevonden:
                log("Geen Nest-camera's gevonden in dit Google-account.",
                    "WARNING")
                return
            lijst = eufy.cams()
            bekend = {c.get("device_id") for c in lijst}
            nieuw = [c for c in gevonden if c["device_id"] not in bekend]
            for c in nieuw:
                lijst.append({"naam": c["naam"], "url": "", "type": "nest",
                              "device_id": c["device_id"]})
            if nieuw:
                eufy.sla_cams_op(lijst)
            log(f"{len(nieuw)} Nest-camera('s) toegevoegd; {len(gevonden)} "
                f"gevonden in je account.", "SUCCESS" if nieuw else "INFO")

        self._run_action(doe)

    def _kopieer(self, tekst):
        """Zet tekst op het klembord en bevestig in het logboek."""
        self.clipboard_clear()
        self.clipboard_append(tekst)
        self.logger.log(f"Gekopieerd naar het klembord: {tekst}", "INFO")

    def _render_scripts(self):
        """Scripts-bibliotheek: gedeelde scripts downloaden van de server."""
        f = self.kaart

        def sectie(tekst):
            tk.Label(f, text=tekst.upper(), bg=KAART, fg="#9aa0ac",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(
                         fill="x", padx=16, pady=(12, 2))

        def knop(rij, tekst, cmd, primair=False):
            b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                          bg=ACCENT if primair else "#eef1f6",
                          fg="white" if primair else TEKST,
                          activebackground=ACCENT_H if primair else "#dfe5f0",
                          font=("Segoe UI", 8, "bold" if primair else "normal"),
                          padx=12, pady=5, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8))
            self.knoppen.append(b)

        tk.Label(f, text="Kant-en-klare scripts van charlesderidder.nl, "
                         "verdeeld in categorieën (MikroTik, PowerShell, "
                         "Proxmox, Docker, Batchscripts). Downloads komen in "
                         "Documenten\\CharlesOnderhoud\\scripts.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(14, 2))

        sectie("Categorie")
        rij0 = tk.Frame(f, bg=KAART)
        rij0.pack(fill="x", padx=16, pady=(0, 2))
        self.scripts_cat_var = tk.StringVar()
        cats = scripts_repo.categorieen()
        self.scripts_combo = ttk.Combobox(
            rij0, textvariable=self.scripts_cat_var, state="readonly",
            width=22, values=cats)
        if cats:
            self.scripts_combo.current(0)
        self.scripts_combo.pack(side="left", padx=(0, 8))
        self.scripts_combo.bind("<<ComboboxSelected>>",
                                lambda e: self._scripts_vul_lijst())
        knop(rij0, "Vernieuwen van server",
             lambda: self._run_action(scripts_repo.ververs), primair=True)
        knop(rij0, "Open scriptmap", self._scripts_open_map)

        sectie("Scripts")
        lijst_frame = tk.Frame(f, bg=KAART)
        lijst_frame.pack(fill="x", padx=16, pady=(0, 2))
        self.scripts_listbox = tk.Listbox(
            lijst_frame, height=8, relief="flat", bg="#f6f7f9", fg=TEKST,
            font=("Segoe UI", 9), highlightthickness=1,
            highlightbackground=BORDER, selectbackground=ACCENT,
            selectforeground="white", exportselection=False,
            activestyle="none")
        lb_scroll = ttk.Scrollbar(lijst_frame,
                                  command=self.scripts_listbox.yview)
        self.scripts_listbox.configure(yscrollcommand=lb_scroll.set)
        lb_scroll.pack(side="right", fill="y")
        self.scripts_listbox.pack(fill="x")

        rij1 = tk.Frame(f, bg=KAART)
        rij1.pack(fill="x", padx=16, pady=(2, 2))
        knop(rij1, "Download geselecteerde",
             lambda: self._run_action(self._scripts_download_één), primair=True)
        knop(rij1, "Download hele categorie",
             lambda: self._run_action(self._scripts_download_alles))
        knop(rij1, "Synchroniseer alles",
             lambda: self._run_action(scripts_repo.synchroniseer_alles))
        if not cats:
            tk.Label(f, text="Nog geen bibliotheek gedownload — klik eerst op "
                             "'Vernieuwen van server'.",
                     bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                     anchor="w").pack(fill="x", padx=16, pady=(4, 0))

        sectie("Inhoud van het geselecteerde script")
        self.scripts_preview = tk.Text(
            f, height=9, relief="flat", bg="#f6f7f9", fg="#374151",
            font=("Consolas", 8), wrap="none", state="disabled",
            highlightthickness=1, highlightbackground=BORDER)
        self.scripts_preview.pack(fill="x", padx=16, pady=(0, 2))
        rij2 = tk.Frame(f, bg=KAART)
        rij2.pack(fill="x", padx=16, pady=(0, 2))
        knop(rij2, "Kopieer inhoud", self._scripts_kopieer_inhoud)
        self._scripts_preview_tekst = ""
        tk.Frame(f, bg=KAART, height=10).pack()
        self._scripts_vul_lijst()
        self.scripts_listbox.bind("<<ListboxSelect>>",
                                  lambda e: self._scripts_laadt_preview())

    def _scripts_vul_lijst(self):
        """Vul de lijst met scripts van de gekozen categorie (uit cache)."""
        self.scripts_listbox.delete(0, "end")
        cat = self.scripts_cat_var.get()
        self._scripts_entries = scripts_repo.scripts(cat) if cat else []
        for e in self._scripts_entries:
            naam = e.get("naam") or os.path.basename(e.get("pad", ""))
            beschr = (e.get("beschrijving") or "").strip()
            self.scripts_listbox.insert(
                "end", f"{naam}  —  {beschr}" if beschr else naam)
        if self._scripts_entries:
            self.scripts_listbox.selection_set(0)
            self._scripts_laadt_preview()  # preview direct meeverversen
        else:
            self._scripts_toon_preview_tekst("")

    def _scripts_download_één(self, log):
        sel = self.scripts_listbox.curselection()
        if not sel:
            log("Selecteer eerst een script in de lijst.", "WARNING")
            return
        scripts_repo.download(log, self.scripts_cat_var.get(),
                              self._scripts_entries[sel[0]])

    def _scripts_download_alles(self, log):
        cat = self.scripts_cat_var.get()
        if not self._scripts_entries:
            log("Geen scripts in deze categorie.", "WARNING")
            return
        log(f"=== Alle scripts in '{cat}' downloaden ===", "STEP")
        for e in self._scripts_entries:
            scripts_repo.download(log, cat, e)

    def _scripts_open_map(self):
        os.startfile(scripts_repo.map())

    def _scripts_laadt_preview(self):
        """Haal de inhoud van het geselecteerde script op (in een thread)."""
        sel = self.scripts_listbox.curselection()
        if not sel:
            return
        entry = self._scripts_entries[sel[0]]
        pad = entry.get("pad", "")
        self._scripts_toon_preview_tekst("Laden…")

        def worker():
            tekst = scripts_repo.inhoud(
                lambda *a: None, self.scripts_cat_var.get(), entry)
            self.ui_queue.put(("scripts_preview", pad, tekst))

        threading.Thread(target=worker, daemon=True).start()

    def _scripts_preview_resultaat(self, pad, tekst):
        """Toon de opgehaalde inhoud (alleen als de selectie niet veranderd is)."""
        sel = self.scripts_listbox.curselection()
        if not sel or self._scripts_entries[sel[0]].get("pad") != pad:
            return  # gebruiker heeft inmiddels iets anders geselecteerd
        self._scripts_toon_preview_tekst(
            tekst if tekst is not None else "(ophalen mislukt)")

    def _scripts_toon_preview_tekst(self, tekst):
        self._scripts_preview_tekst = tekst
        self.scripts_preview.configure(state="normal")
        self.scripts_preview.delete("1.0", "end")
        self.scripts_preview.insert("1.0", tekst)
        self.scripts_preview.configure(state="disabled")

    def _scripts_kopieer_inhoud(self):
        if self._scripts_preview_tekst and \
                self._scripts_preview_tekst != "Laden…":
            self._kopieer(self._scripts_preview_tekst)
        else:
            self.logger.log("Er is nog geen script-inhoud om te kopiëren.",
                            "WARNING")

    def _render_eufy(self):
        """Eufy-pagina: enkel het dashboard starten/stoppen.

        Het camerabeheer (URL's met inloggegevens) staat bewust in de
        instellingen-popup, zodat er hier geen wachtwoorden zichtbaar zijn.
        """
        f = self.kaart

        def sectie(tekst):
            tk.Label(f, text=tekst.upper(), bg=KAART, fg="#9aa0ac",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(
                         fill="x", padx=16, pady=(12, 2))

        def knop(rij, tekst, cmd, primair=False):
            b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                          bg=ACCENT if primair else "#eef1f6",
                          fg="white" if primair else TEKST,
                          activebackground=ACCENT_H if primair else "#dfe5f0",
                          font=("Segoe UI", 8, "bold" if primair else "normal"),
                          padx=12, pady=5, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8))
            self.knoppen.append(b)

        tk.Label(f, text="Live-beveiligingsdashboard voor je Eufy-camera's "
                         "via RTSP én Google Nest-camera's (cloud-stream via "
                         "de koppeling in Instellingen). De streams staan in "
                         "adaptief raster: 1 camera schermvullend, 2 "
                         "gesplitst, 3-4 in vier vlakken, enzovoort. "
                         "Dubbelklik een beeld voor solo-weergave, Esc sluit.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(14, 2))

        sectie("Camera's")
        n_cams = len(eufy.cams())
        n_aan = sum(1 for c in eufy.cams() if c.get("aan", True))
        rij0 = tk.Frame(f, bg=KAART)
        rij0.pack(fill="x", padx=16, pady=(0, 2))
        tk.Label(rij0, text=f"{n_aan} van {n_cams} camera('s) actief",
                 bg=KAART, fg=TEKST if n_cams else "#b45309",
                 font=("Segoe UI", 10, "bold")).pack(
                     side="left", padx=(0, 12))
        knop(rij0, "Camera's beheren (via Instellingen)",
             self._toon_instellingen_popup)
        tk.Label(f, text="Camera's toevoegen, bewerken, importeren en "
                         "ordenen doe je via het tandwiel (Instellingen) "
                         "rechtsboven; daar koppel je ook je Google-account "
                         "voor Nest-camera's. URL's en wachtwoorden worden "
                         "versleuteld opgeslagen en hier niet getoond.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(0, 2))

        sectie("Dashboard")
        rij2 = tk.Frame(f, bg=KAART)
        rij2.pack(fill="x", padx=16, pady=(0, 2))
        actief = eufy.actief()
        tk.Label(rij2, text="● Actief" if actief else "● Gestopt",
                 bg=KAART, fg="#16a34a" if actief else MUTED,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 12))
        if not actief:
            knop(rij2, "Start dashboard", self._eufy_start, primair=True)
        else:
            knop(rij2, "Stop dashboard", self._eufy_stop)
        knop(rij2, "Opnames bekijken", self._toon_opnames)

        sectie("Webviewer")
        rij3 = tk.Frame(f, bg=KAART)
        rij3.pack(fill="x", padx=16, pady=(0, 2))
        web_actief = eufy_web.actief()
        web_url = eufy_web.urls().get("lan")
        tk.Label(rij3, text="● Actief" if web_actief else "● Gestopt",
                 bg=KAART, fg="#16a34a" if web_actief else MUTED,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 12))
        if not web_actief:
            knop(rij3, "Start webviewer",
                 lambda: self._run_action(lambda log: eufy_web.start(log)),
                 primair=True)
        else:
            knop(rij3, "Stop webviewer",
                 lambda: self._run_action(lambda log: eufy_web.stop(log)))
            if web_url:
                import webbrowser
                knop(rij3, "Open in browser",
                     lambda: webbrowser.open(web_url))
                knop(rij3, "Kopieer URL",
                     lambda: self._kopieer(web_url))
                knop(rij3, "Kopieer voor Hub",
                     lambda: self._kopieer(web_url))
        if web_url:
            lbl_link = tk.Label(f, text=web_url,
                                bg=KAART, fg=ACCENT,
                                font=("Consolas", 10), anchor="w",
                                cursor="hand2")
            lbl_link.pack(fill="x", padx=16, pady=(0, 2))
            lbl_link.bind("<Button-1>",
                          lambda e, url=web_url: __import__("webbrowser").open(url))
            tk.Label(f, text="Kopieer deze link naar je Google Nest Hub of een mobiel apparaat om de webviewer direct te openen.",
                     bg=KAART, fg=MUTED, font=("Segoe UI", 8), wraplength=620,
                     justify="left").pack(fill="x", padx=16, pady=(0, 2))
        tk.Frame(f, bg=KAART, height=10).pack()

    def _render_agenda(self):
        """Office Agenda: Outlook-achtige maandweergave op basis van ICS."""
        f = self.kaart

        def knop(rij, tekst, cmd, primair=False):
            b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                          bg=ACCENT if primair else "#eef1f6",
                          fg="white" if primair else TEKST,
                          activebackground=ACCENT_H if primair else "#dfe5f0",
                          font=("Segoe UI", 8, "bold" if primair else "normal"),
                          padx=12, pady=5, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8))
            self.knoppen.append(b)

        tk.Label(f, text="Agenda op basis van de Outlook ICS-link uit Instellingen.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9),
                 justify="left").pack(anchor="w", padx=16, pady=(14, 6))

        toolbar = tk.Frame(f, bg=KAART)
        toolbar.pack(fill="x", padx=16, pady=(0, 6))
        b_vandaag = tk.Button(toolbar, text="Vandaag", relief="flat", bd=0,
                              bg="#eef1f6", fg=TEKST,
                              activebackground="#dfe5f0",
                              font=("Segoe UI", 8), padx=10, pady=4,
                              cursor="hand2", command=self._agenda_naar_vandaag)
        b_vandaag.pack(side="left", padx=(0, 6))
        self.knoppen.append(b_vandaag)

        b_prev = tk.Button(toolbar, text="‹", relief="flat", bd=0,
                           bg="#eef1f6", fg=TEKST,
                           activebackground="#dfe5f0",
                           font=("Segoe UI", 10, "bold"), padx=9, pady=2,
                           cursor="hand2",
                           command=lambda: self._agenda_maand_verschuif(-1))
        b_prev.pack(side="left", padx=(0, 3))
        self.knoppen.append(b_prev)

        b_next = tk.Button(toolbar, text="›", relief="flat", bd=0,
                           bg="#eef1f6", fg=TEKST,
                           activebackground="#dfe5f0",
                           font=("Segoe UI", 10, "bold"), padx=9, pady=2,
                           cursor="hand2",
                           command=lambda: self._agenda_maand_verschuif(1))
        b_next.pack(side="left", padx=(0, 10))
        self.knoppen.append(b_next)

        self.agenda_maand_lbl = tk.Label(toolbar, text="", bg=KAART, fg=TEKST,
                                         font=("Segoe UI", 16, "bold"))
        self.agenda_maand_lbl.pack(side="left")

        knop(toolbar, "Verversen", self._agenda_ververs, primair=True)
        knop(toolbar, "Instellingen", self._toon_instellingen_popup)

        self.agenda_status = tk.Label(f, text="", bg=KAART, fg=MUTED,
                                      font=("Segoe UI", 8), anchor="w")
        self.agenda_status.pack(fill="x", padx=16, pady=(0, 4))

        self.agenda_kalender = tk.Frame(f, bg="#f6f7f9", highlightthickness=1,
                                        highlightbackground=BORDER)
        self.agenda_kalender.pack(fill="both", expand=True,
                                  padx=16, pady=(0, 10))

        nu = date.today()
        self._agenda_items = []
        self._agenda_dagmap = {}
        self._agenda_view_jaar = nu.year
        self._agenda_view_maand = nu.month
        self._agenda_render_kalender()
        self.agenda_status.config(text="Klik op Verversen om agenda op te halen.")

    def _agenda_ververs(self):
        """Haal afspraken op uit de opgegeven ICS-link."""
        url = settings.get("office_agenda_ics", "").strip()
        if not url:
            self.logger.log("Vul eerst een Outlook ICS-link in via Instellingen.",
                            "WARNING")
            if hasattr(self, "agenda_status") and self.agenda_status.winfo_exists():
                self.agenda_status.config(
                    text="Geen ICS-link ingesteld (Instellingen > Office agenda).")
            return
        if not (url.lower().startswith("http://") or
                url.lower().startswith("https://")):
            self.logger.log("ICS-link moet met http:// of https:// beginnen.",
                            "WARNING")
            return
        if hasattr(self, "agenda_status") and self.agenda_status.winfo_exists():
            self.agenda_status.config(text="Agenda laden…")

        def worker():
            try:
                events = _ics_events_van_url(url)
                fout = None
            except urllib.error.URLError as exc:
                events, fout = [], f"Netwerkfout: {exc.reason}"
            except Exception as exc:
                events, fout = [], str(exc)
            self.after(0, lambda: self._agenda_resultaat(events, fout))

        threading.Thread(target=worker, daemon=True).start()

    def _agenda_resultaat(self, events: list, fout: str | None):
        """Werk de agendaweergave bij met opgehaalde afspraken."""
        if not hasattr(self, "agenda_kalender") or not self.agenda_kalender.winfo_exists():
            return
        self._agenda_items = events
        self._agenda_dagmap = {}
        if fout:
            if hasattr(self, "agenda_status") and self.agenda_status.winfo_exists():
                self.agenda_status.config(text=f"Fout bij ophalen: {fout}")
            self.logger.log(f"Agenda ophalen mislukt: {fout}", "ERROR")
            self._agenda_render_kalender()
            return
        if not events:
            if hasattr(self, "agenda_status") and self.agenda_status.winfo_exists():
                self.agenda_status.config(text="Geen komende afspraken gevonden.")
            self.logger.log("Agenda geladen: geen komende afspraken.", "INFO")
            self._agenda_render_kalender()
            return
        for it in events:
            st = it.get("start")
            if isinstance(st, datetime):
                self._agenda_dagmap.setdefault(st.date(), []).append(it)

        self._agenda_render_kalender()
        if hasattr(self, "agenda_status") and self.agenda_status.winfo_exists():
            self.agenda_status.config(text=f"{len(events)} afspraak/afspraken geladen.")
        self.logger.log(f"Agenda geladen: {len(events)} afspraak/afspraken.",
                        "SUCCESS")

    def _agenda_maand_verschuif(self, delta: int):
        """Ga naar vorige/volgende maand in de compacte kalender."""
        maand = self._agenda_view_maand + delta
        jaar = self._agenda_view_jaar
        if maand < 1:
            maand = 12
            jaar -= 1
        elif maand > 12:
            maand = 1
            jaar += 1
        self._agenda_view_maand = maand
        self._agenda_view_jaar = jaar
        self._agenda_render_kalender()

    def _agenda_naar_vandaag(self):
        """Spring terug naar de huidige maand."""
        nu = date.today()
        self._agenda_view_jaar = nu.year
        self._agenda_view_maand = nu.month
        self._agenda_render_kalender()

    def _agenda_render_kalender(self):
        """Teken Outlook-achtige maandweergave met dagvakken en afspraken."""
        if not hasattr(self, "agenda_kalender") or not self.agenda_kalender.winfo_exists():
            return
        for w in self.agenda_kalender.winfo_children():
            w.destroy()

        y, m = self._agenda_view_jaar, self._agenda_view_maand
        self.agenda_maand_lbl.config(text=f"{_MAAND_NL[m - 1]} {y}")

        koppen = _DAG_NL
        for c, naam in enumerate(koppen):
            tk.Label(self.agenda_kalender, text=naam, bg="#ffffff", fg=TEKST,
                     font=("Segoe UI", 12, "bold" if c == 6 else "normal"),
                     anchor="w", padx=8, pady=6).grid(
                         row=0, column=c, sticky="nsew", padx=0, pady=0)
            self.agenda_kalender.columnconfigure(c, weight=1)

        weken = calendar.Calendar(firstweekday=0).monthdatescalendar(y, m)
        vandaag = date.today()
        for r, week in enumerate(weken, start=1):
            self.agenda_kalender.rowconfigure(r, weight=1, minsize=96)
            for c, d in enumerate(week):
                aantal = len(self._agenda_dagmap.get(d, []))
                in_maand = (d.month == m)
                cel_bg = "#ffffff" if in_maand else "#fafafa"
                cel = tk.Frame(self.agenda_kalender, bg=cel_bg,
                               highlightthickness=1,
                               highlightbackground="#d6d6d6")
                cel.grid(row=r, column=c, sticky="nsew", padx=0, pady=0)
                cel.rowconfigure(1, weight=1)
                cel.columnconfigure(0, weight=1)

                dag_tekst = str(d.day)
                if d.day == 1 and not in_maand:
                    dag_tekst = f"1 {_MAAND_NL[d.month - 1][:3]}"
                dag_fg = TEKST if in_maand else "#9ca3af"
                if d == vandaag:
                    dag_fg = "#1d4ed8"

                tk.Label(cel, text=dag_tekst, bg=cel_bg, fg=dag_fg,
                         font=("Segoe UI", 12,
                               "bold" if (d == vandaag or d.day == 1) else "normal"),
                         anchor="nw", padx=6, pady=4).grid(
                             row=0, column=0, sticky="nw")

                body = tk.Frame(cel, bg=cel_bg)
                body.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
                events = sorted(self._agenda_dagmap.get(d, []),
                                key=lambda ev: ev.get("start") or datetime.min)
                for ev in events[:3]:
                    st = ev.get("start")
                    sam = (ev.get("summary") or "(zonder titel)").strip()
                    if st:
                        txt = f"{sam} {st.strftime('%H:%M')}"
                    else:
                        txt = sam
                    tk.Label(body, text=txt, bg="#d7ebfb", fg="#0f172a",
                             font=("Segoe UI", 8), anchor="w",
                             justify="left", padx=3, pady=1).pack(
                                 fill="x", pady=(0, 2))
                if aantal > 3:
                    tk.Label(body, text=f"+{aantal - 3} meer",
                             bg=cel_bg, fg="#1d4ed8", font=("Segoe UI", 8),
                             anchor="w").pack(fill="x")

    def _office_agenda_opslaan(self):
        """Sla de Office ICS-link op vanuit Instellingen."""
        url = self.office_ics_var.get().strip() if hasattr(self, "office_ics_var") else ""
        if url and not (url.lower().startswith("http://") or
                        url.lower().startswith("https://")):
            self.logger.log("ICS-link moet met http:// of https:// beginnen.",
                            "WARNING")
            return
        settings.set("office_agenda_ics", url)
        self.logger.log("Office agenda-link opgeslagen.", "SUCCESS")
        if self._actieve_nav == "agenda":
            self._toon_pagina("agenda")

    def _eufy_start(self):
        eufy.open_dashboard(self, eufy.cams(), self.logger.log,
                            on_sluiten=self._eufy_dash_gesloten)

    def _eufy_stop(self):
        eufy.stop_dashboard(self.logger.log)

    def _toon_opnames(self):
        """Open het opnames-venster (afspelen, kopiëren, verwijderen)."""
        recorder.toon_opnames(self, self.logger.log)

    def _eufy_dash_gesloten(self):
        if self._actieve_nav == "eufy":
            self._toon_pagina("eufy")

    def _render_netwerk(self):
        """Netwerkpagina: alle IP-tools samengevoegd op één tabblad."""
        f = self.kaart

        def sectie(tekst):
            tk.Label(f, text=tekst.upper(), bg=KAART, fg="#9aa0ac",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(
                         fill="x", padx=16, pady=(12, 2))

        def knop(rij, tekst, cmd, primair=False):
            b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                          bg=ACCENT if primair else "#eef1f6",
                          fg="white" if primair else TEKST,
                          activebackground=ACCENT_H if primair else "#dfe5f0",
                          font=("Segoe UI", 8, "bold" if primair else "normal"),
                          padx=12, pady=5, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8))
            self.knoppen.append(b)

        tk.Label(f, text="Handmatige netwerkhulpmiddelen. Deze tools draaien "
                         "nooit automatisch mee met het onderhoud.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(14, 2))

        sectie("Informatie")
        rij1 = tk.Frame(f, bg=KAART)
        rij1.pack(fill="x", padx=16, pady=(0, 2))
        knop(rij1, "Toon netwerkinfo (ipconfig /all + extern IP)",
             lambda: self._run_action(network.show_ip_info), primair=True)

        sectie("Opschonen & diagnose")
        rij2 = tk.Frame(f, bg=KAART)
        rij2.pack(fill="x", padx=16, pady=(0, 2))
        knop(rij2, "DNS-cache opschonen",
             lambda: self._run_action(network.flush_dns))
        knop(rij2, "Netwerkdiagnose (ping + DNS-test)",
             lambda: self._run_action(network.network_diag))

        sectie("Herstel (verstorend)")
        tk.Label(f, text="Netwerk reset: DNS legen, IP release/renew, "
                         "Winsock- en TCP/IP-reset. De verbinding valt kort "
                         "weg; herstart de pc daarna.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9), wraplength=620,
                 justify="left").pack(anchor="w", padx=16)
        rij3 = tk.Frame(f, bg=KAART)
        rij3.pack(fill="x", padx=16, pady=(4, 2))
        knop(rij3, "Netwerk resetten",
             lambda: self._run_action(network.network_reset))
        tk.Frame(f, bg=KAART, height=10).pack()

    def _render_installer(self):
        """Apps: bijwerken én installeren via winget."""
        f = self.kaart

        def sectie(tekst):
            tk.Label(f, text=tekst.upper(), bg=KAART, fg="#9aa0ac",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(
                         fill="x", padx=16, pady=(12, 2))

        def knop(rij, tekst, cmd, primair=False):
            b = tk.Button(rij, text=tekst, relief="flat", bd=0,
                          bg=ACCENT if primair else "#eef1f6",
                          fg="white" if primair else TEKST,
                          activebackground=ACCENT_H if primair else "#dfe5f0",
                          font=("Segoe UI", 8, "bold" if primair else "normal"),
                          padx=12, pady=5, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8), pady=2)
            self.knoppen.append(b)

        def invoerveld(rij, var, width=28):
            tk.Entry(rij, textvariable=var, width=width, relief="flat",
                     bg="#f6f7f9", fg=TEKST, font=("Segoe UI", 10),
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=ACCENT).pack(side="left", padx=(0, 8),
                                                 ipady=3)

        tk.Label(f, text="Werk apps bij en installeer ze volledig onbeheerd "
                         "via winget. Resultaten en voortgang verschijnen in "
                         "het logboek.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 9), wraplength=620,
                 justify="left").pack(anchor="w", padx=16, pady=(14, 2))

        sectie("Bijwerken")
        rij0 = tk.Frame(f, bg=KAART)
        rij0.pack(fill="x", padx=16, pady=(0, 2))
        knop(rij0, "Alle apps bijwerken (winget upgrade --all)",
             lambda: self._start(["winget"]), primair=True)

        sectie("Top 25 populairste winget-apps")
        lijst_frame = tk.Frame(f, bg=KAART)
        lijst_frame.pack(fill="x", padx=16, pady=(0, 2))
        self.top25_listbox = tk.Listbox(
            lijst_frame, height=7, relief="flat", bg="#f6f7f9", fg=TEKST,
            font=("Segoe UI", 9), highlightthickness=1,
            highlightbackground=BORDER, selectbackground=ACCENT,
            selectforeground="white", exportselection=False,
            activestyle="none")
        lb_scroll = ttk.Scrollbar(lijst_frame,
                                  command=self.top25_listbox.yview)
        self.top25_listbox.configure(yscrollcommand=lb_scroll.set)
        lb_scroll.pack(side="right", fill="y")
        self.top25_listbox.pack(fill="x")
        for naam, _ in TOP25_APPS:
            self.top25_listbox.insert("end", naam)
        self.top25_listbox.selection_set(0)
        self.top25_listbox.bind("<<ListboxSelect>>",
                                lambda e: self._top25_keuze())
        rij1 = tk.Frame(f, bg=KAART)
        rij1.pack(fill="x", padx=16, pady=(2, 2))
        knop(rij1, "Installeer geselecteerde app",
             lambda: self._run_action(
                 lambda log: winget_task.install(log, self._top25_id())),
             primair=True)

        sectie("Zoeken in winget")
        rij2 = tk.Frame(f, bg=KAART)
        rij2.pack(fill="x", padx=16, pady=(0, 2))
        self.zoek_app_var = tk.StringVar()
        invoerveld(rij2, self.zoek_app_var)
        knop(rij2, "Zoek",
             lambda: self._run_action(
                 lambda log: winget_task.search(log, self.zoek_app_var.get())))

        sectie("Installeren op exact ID")
        rij3 = tk.Frame(f, bg=KAART)
        rij3.pack(fill="x", padx=16, pady=(0, 2))
        self.install_id_var = tk.StringVar()
        invoerveld(rij3, self.install_id_var)
        knop(rij3, "Installeer",
             lambda: self._run_action(
                 lambda log: winget_task.install(log,
                                                 self.install_id_var.get())),
             primair=True)
        tk.Label(f, text="Tip: zoek eerst op naam; kopieer daarna de waarde uit "
                         "de kolom 'Id' naar het installatieveld.",
                 bg=KAART, fg=MUTED, font=("Segoe UI", 8),
                 justify="left").pack(anchor="w", padx=16, pady=(4, 0))
        tk.Frame(f, bg=KAART, height=10).pack()

    def _top25_id(self) -> str:
        """winget-ID van de geselecteerde top-25-app (anders de eerste)."""
        sel = self.top25_listbox.curselection()
        return TOP25_APPS[sel[0] if sel else 0][1]

    def _top25_keuze(self):
        """Zet het ID van de gekozen top-25-app klaar in het ID-veld."""
        self.install_id_var.set(self._top25_id())

    # ------------------------------------------------------------- helpers
    def _append_log(self, level, stamp, text):
        self.log_text.insert("end", f"[{stamp}] {text}\n", level)
        regels = int(self.log_text.index("end-1c").split(".")[0])
        if regels > 5000:
            self.log_text.delete("1.0", "1001.0")
        self.log_text.see("end")

    def _log_toets(self, e):
        # Ctrl-combinaties (Ctrl+C) doorlaten; typen in het logboek blokkeren
        if not (e.state & 0x4):
            return "break"

    def _log_alles_selecteren(self):
        self.log_text.tag_add("sel", "1.0", "end-1c")

    def _set_status(self, key, state):
        lbl = self.status_labels.get(key)
        if lbl:
            lbl.config(text=STATUS_TEKST[state], fg=STATUS_KLEUR[state])

    def _set_knoppen(self, staat):
        for b in self.knoppen:
            try:
                b.config(state=staat)
            except tk.TclError:
                pass  # knop bestaat niet meer (pagina gewisseld)

    def _poll_queue(self):
        """Verwerk berichten uit worker-threads (draait op de UI-thread)."""
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                if msg[0] == "log":
                    _, level, stamp, text = msg
                    self._append_log(level, stamp, text)
                elif msg[0] == "status":
                    _, key, state = msg
                    self._set_status(key, state)
                elif msg[0] == "reboot":
                    self.lbl_reboot.config(text="⚠ Herstart aanbevolen")
                elif msg[0] == "update_result":
                    _, auto, res = msg
                    self._update_resultaat(auto, res)
                elif msg[0] == "update_downloaded":
                    _, pad, versie = msg
                    self._update_gedownload(pad, versie)
                elif msg[0] == "scripts_preview":
                    _, pad, tekst = msg
                    self._scripts_preview_resultaat(pad, tekst)
                elif msg[0] == "done":
                    self._klaar()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------ acties
    def _start(self, takenlijst):
        """Start een reeks onderhoudstaken in een aparte thread."""
        if self.running:
            return
        self.running = True
        self._set_knoppen("disabled")
        for key in takenlijst:
            self._set_status(key, "pending")
        threading.Thread(target=self._pipeline, args=(takenlijst,),
                         daemon=True).start()

    def _run_action(self, func):
        """Start een losse actie (netwerk-tool, herstel) in een aparte thread."""
        if self.running:
            return
        self.running = True
        self._set_knoppen("disabled")

        def worker():
            try:
                func(self.logger.log)
            except Exception as exc:
                self.logger.log(f"Onverwachte fout: {exc}", "ERROR")
            self.ui_queue.put(("done",))

        threading.Thread(target=worker, daemon=True).start()

    def _pipeline(self, takenlijst):
        """Voer de taken sequentieel uit (worker-thread)."""
        uitvoerders = {
            "winget":  lambda: winget_task.run(self.logger.log),
            "windows": lambda: windows_update.run(self.logger.log,
                                                  auto_reboot=self.var_reboot.get()),
            "drivers": lambda: drivers.run(self.logger.log),
            "cleanup": lambda: cleanup.run(self.logger.log),
        }
        for key in takenlijst:
            self.ui_queue.put(("status", key, "running"))
            try:
                ok = uitvoerders[key]()
            except Exception as exc:
                self.logger.log(f"Onverwachte fout in taak '{key}': {exc}", "ERROR")
                ok = False
            self.ui_queue.put(("status", key, "success" if ok else "failed"))

        self.logger.log("=== Alle taken afgerond ===", "STEP")
        if reboot_pending():
            self.logger.log("Let op: een herstart is nodig om updates af te ronden.",
                            "WARNING")
            self.ui_queue.put(("reboot",))
        self.ui_queue.put(("done",))

    def _klaar(self):
        self.running = False
        self._set_knoppen("normal")
        # draaiende instellingen-popup meeverversen (Nest-status, cameralijst)
        if getattr(self, "_inst_win", None) and self._inst_win.winfo_exists():
            self._nest_status_ververs()
            self._cam_vul_lijst()
        if self._actieve_nav == "remote":
            # volledig opnieuw renderen (schone kaart): status klopt weer
            self._toon_pagina("remote")
        elif self._actieve_nav == "tv":
            self._toon_pagina("tv")
        elif self._actieve_nav == "scripts":
            # na 'Vernieuwen van server': combobox en lijst opnieuw vullen
            self._toon_pagina("scripts")
        if self._actieve_nav == "eufy":
            self._toon_pagina("eufy")
        if self.auto:
            self.logger.log("Automatische modus: venster sluit over 15 seconden.",
                            "INFO")
            self.after(15000, self.destroy)

    def _auto_start(self):
        self.logger.log("Automatische modus: alle onderhoudstaken worden gestart.",
                        "STEP")
        self._start(["winget", "windows", "drivers", "cleanup"])

    def _check_ziggo_status(self):
        """Poll of de Ziggo-viewer nog draait; ververs de TV-pagina bij wijziging.

        Zo verdwijnt '● Actief' automatisch uit de bediening als de gebruiker
        het viewer-venster zelf sluit (via ✕ of het kruisje van Windows).
        """
        actief = ziggo.actief()
        if actief != self._ziggo_was_actief:
            if self._ziggo_was_actief and not actief:
                self.logger.log("Ziggo TV-viewer is afgesloten.", "INFO")
            self._ziggo_was_actief = actief
            if self._actieve_nav == "tv" and not self.running:
                self._toon_pagina("tv")
        self.after(1000, self._check_ziggo_status)

    def _check_admin(self):
        if is_admin():
            self.lbl_admin.config(text="✔ Admin-rechten", fg="#16a34a")
            # Geen herstart-badge bij het opstarten: die is alleen relevant
            # ná het draaien van taken (zie _pipeline).
            return
        self.lbl_admin.config(text="✖ Geen admin-rechten", fg="#dc2626")
        if messagebox.askyesno(APP_NAAM,
                               "Deze app heeft administrator-rechten nodig.\n\n"
                               "Nu opnieuw starten als administrator?"):
            if relaunch_as_admin():
                self.destroy()
                return
        self.logger.log("Zonder admin-rechten zullen veel taken mislukken.", "WARNING")

    # ------------------------------------------------------ instellingen
    def _exporteer_log(self):
        pad = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("Logbestanden", "*.log"), ("Alle bestanden", "*.*")],
            initialfile=os.path.basename(self.logger.log_path))
        if pad and self.logger.export(pad):
            messagebox.showinfo(APP_NAAM, f"Log geëxporteerd naar:\n{pad}")

    def _leeg_log(self):
        self.log_text.delete("1.0", "end")

    def _schtasks(self, args):
        return subprocess.run(["schtasks"] + args, capture_output=True,
                              text=True, creationflags=CREATE_NO_WINDOW)

    def _plan_taak(self):
        dagelijks = messagebox.askyesno(
            APP_NAAM, "Dagelijks automatisch draaien om 09:00?\n\n"
                      "Kies 'Nee' voor wekelijks (maandag 09:00).")
        if getattr(sys, "frozen", False):
            doel = f'"{sys.executable}" --auto'
        else:
            doel = f'"{sys.executable}" "{os.path.abspath(__file__)}" --auto'
        args = ["/Create", "/TN", APP_NAAM, "/TR", doel, "/ST", "09:00",
                "/RL", "HIGHEST", "/F"]
        args += ["/SC", "DAILY"] if dagelijks else ["/SC", "WEEKLY", "/D", "MON"]
        res = self._schtasks(args)
        if res.returncode == 0:
            messagebox.showinfo(APP_NAAM, "Geplande taak aangemaakt.\n"
                                "Aanpassen kan via 'Taakbeheer' (taskschd.msc).")
        else:
            messagebox.showerror(APP_NAAM, f"Plannen mislukt:\n{res.stderr or res.stdout}")

    def _verwijder_planning(self):
        res = self._schtasks(["/Delete", "/TN", APP_NAAM, "/F"])
        if res.returncode == 0:
            messagebox.showinfo(APP_NAAM, "Geplande taak verwijderd.")
        else:
            messagebox.showerror(APP_NAAM, "Verwijderen mislukt "
                                           "(bestaat de taak wel?).")

    # --------------------------------------------------------- zelf-update
    def _check_update_knop(self):
        self._update_check(auto=False)

    def _update_check(self, auto: bool):
        """Controleer in een thread of er een nieuwere versie op de server staat."""
        if self.running:
            return
        self.running = True
        self._set_knoppen("disabled")

        def worker():
            try:
                res = updater.check_for_update(self.logger.log)
            except Exception as exc:
                self.logger.log(f"Updatecontrole fout: {exc}", "ERROR")
                res = None
            self.ui_queue.put(("update_result", auto, res))

        threading.Thread(target=worker, daemon=True).start()

    def _update_resultaat(self, auto: bool, res):
        """Verwerk het resultaat van de updatecontrole (UI-thread)."""
        self.running = False
        self._set_knoppen("normal")
        if res is None:
            if not auto:  # bij stille controle niets melden als alles actueel is
                self.logger.log(f"Je hebt de nieuwste versie (v{__version__}).",
                                "SUCCESS")
            return
        nieuwe_versie, sha = res
        self.logger.log(f"Nieuwe versie beschikbaar: v{nieuwe_versie} "
                        f"(huidig: v{__version__}).", "WARNING")
        self.lbl_update.config(text=f"⬆ v{nieuwe_versie} beschikbaar")
        if auto:
            return  # alleen melden, niet storen met popups
        if messagebox.askyesno(APP_NAAM,
                               f"Versie {nieuwe_versie} is beschikbaar.\n\n"
                               "Nu downloaden en installeren?"):
            self._update_download(nieuwe_versie, sha)

    def _update_download(self, versie: str, sha):
        if self.running:
            return
        self.running = True
        self._set_knoppen("disabled")

        def worker():
            pad = updater.download_update(self.logger.log, verwachte_sha=sha)
            self.ui_queue.put(("update_downloaded", pad, versie))

        threading.Thread(target=worker, daemon=True).start()

    def _download_handmatig(self):
        """Fallback: open de exe-download in de browser voor handmatige update."""
        import webbrowser
        webbrowser.open(updater.EXE_URL)
        self.logger.log("Browser geopend op de download-URL.", "INFO")
        messagebox.showinfo(
            APP_NAAM,
            "De download van CharlesOnderhoud.exe start in je browser.\n\n"
            "Sluit daarna deze app en vervang de oude exe door het "
            "gedownloade bestand. Start de app daarna opnieuw.")

    def _update_gedownload(self, pad, versie: str):
        self.running = False
        self._set_knoppen("normal")
        if not pad:
            # Automatisch downloaden mislukt: bied handmatige route aan
            if messagebox.askyesno(
                    APP_NAAM,
                    "Automatisch downloaden/installeren is mislukt.\n\n"
                    "Handmatig downloaden via de browser?"):
                self._download_handmatig()
            return
        self.logger.log(f"Update v{versie} gedownload.", "SUCCESS")
        if messagebox.askyesno(APP_NAAM,
                               "De update is gedownload.\n\nDe app sluit nu; de "
                               "update wordt geïnstalleerd en de app start "
                               "opnieuw. Start hij na ~30 seconden niet "
                               "vanzelf? Open dan zelf het bestand opnieuw."
                               "\n\nDoorgaan?"):
            if updater.apply_and_restart(self.logger.log, pad):
                self.destroy()

    def _sluiten(self):
        if self.running:
            messagebox.showinfo(APP_NAAM, "Er lopen nog taken. "
                                          "Wacht tot deze klaar zijn.")
            return
        self.destroy()


if __name__ == "__main__":
    if "--ziggo-viewer" in sys.argv:
        # Apart viewer-proces: pywebview vereist de main thread van dit proces
        from tasks import ziggo_viewer
        ziggo_viewer.main()
    else:
        OnderhoudApp().mainloop()
