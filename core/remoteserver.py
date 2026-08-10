"""Ingebouwde remote-webserver: scherm bekijken én bedienen via de browser.

Volledig zelfvoorzienend — geen RustDesk, RDP of andere software nodig:
- Schermcapture via Pillow ImageGrab (JPEG-stream, ~30 fps)
- Webserver via http.server (MJPEG + input-endpoints, login met wachtwoord,
  echte schermresolutie instelbaar (RDP-stijl) en uitlogknop in de werkbalk)
- Invoerinjectie (muis/toetsenbord) via ctypes: SetCursorPos/mouse_event/keybd_event
- Firewall-regel wordt bij start/stop automatisch gezet/verwijderd

Enige externe afhankelijkheid: Pillow (pip install Pillow).
"""
import ctypes
import io
import os
import shutil
import sys
from ctypes import wintypes
import json
import secrets
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

POORT = 8765
FPS = 30
MAX_BREEDTE = 1920          # frames worden tot deze breedte verkleind
JPEG_KWALITEIT = 55
# bij start wordt de host-resolutie hierop gezet (dichtstbijzijnde stand als
# deze niet bestaat); bij stop wordt de oorspronkelijke resolutie hersteld
STANDAARD_RESOLUTIE = (1920, 1080)

_user32 = ctypes.windll.user32


# ------------------------------------------------------- monitor-detectie
class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
    ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)


def enum_monitors():
    """Beeldschermen van links naar rechts: rect, afmetingen, primair-vlag."""
    gevonden = []

    def _cb(hmon, _hdc, _rect, _lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            gevonden.append({
                "rect": (r.left, r.top, r.right, r.bottom),
                "x": r.left, "y": r.top,
                "w": r.right - r.left, "h": r.bottom - r.top,
                "primair": bool(info.dwFlags & 1),  # MONITORINFOF_PRIMARY
                "naam": info.szDevice,
            })
        return True

    _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    gevonden.sort(key=lambda m: (m["x"], m["y"]))
    for i, m in enumerate(gevonden):
        m["i"] = i
    return gevonden


# ------------------------------------------------------- resolutie-beheer
class _DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("dmOrientation", ctypes.c_short),
                ("dmPaperSize", ctypes.c_short),
                ("dmPaperLength", ctypes.c_short),
                ("dmPaperWidth", ctypes.c_short),
                ("dmScale", ctypes.c_short),
                ("dmCopies", ctypes.c_short),
                ("dmDefaultSource", ctypes.c_short),
                ("dmPrintQuality", ctypes.c_short),
                ("dmColor", ctypes.c_short),
                ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short),
                ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD),
                ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD),
                ("dmPanningHeight", wintypes.DWORD)]


_ENUM_HUIDIG = 0xFFFFFFFF   # ENUM_CURRENT_SETTINGS
_DM_PELS = 0x00180000       # DM_PELSWIDTH | DM_PELSHEIGHT


def _stand(device=None, nummer=_ENUM_HUIDIG):
    """Eén DEVMODE ophalen; None zodra de moduslijst uitgeput is."""
    dm = _DEVMODEW()
    dm.dmSize = ctypes.sizeof(_DEVMODEW)
    if _user32.EnumDisplaySettingsW(device, nummer, ctypes.byref(dm)):
        return dm
    return None


def lijst_resoluties(device=None):
    """Ondersteunde (breedte, hoogte)-standen van een beeldscherm (aflopend)."""
    gevonden, i = set(), 0
    while True:
        dm = _stand(device, i)
        if dm is None:
            break
        if dm.dmBitsPerPel == 32 and dm.dmPelsWidth >= 800:
            gevonden.add((dm.dmPelsWidth, dm.dmPelsHeight))
        i += 1
    return sorted(gevonden, key=lambda m: m[0] * m[1], reverse=True)


def _beste_modus(modes, w, h):
    """Stand uit modes die het dichtst bij (w, h) ligt: eerst dezelfde
    beeldverhouding (kruisproduct ~ 0), dan het kleinste maatverschil."""
    if not modes:
        return None
    return min(modes, key=lambda m: (abs(m[0] * h - m[1] * w),
                                     abs(m[0] - w) + abs(m[1] - h)))


def _zet_resolutie(device, w, h) -> bool:
    """Pas de echte resolutie van één beeldscherm aan (tijdelijk; niets in
    het register). True bij DISP_CHANGE_SUCCESSFUL."""
    dm = _stand(device)
    if dm is None:
        return False
    dm.dmPelsWidth = w
    dm.dmPelsHeight = h
    dm.dmFields = _DM_PELS
    return _user32.ChangeDisplaySettingsExW(
        device, ctypes.byref(dm), None, 0, None) == 0


# ------------------------------------------------------------- schermbeeld
class Scherm:
    """Capture-thread: houdt het nieuwste JPEG-frame + actieve weergave bij."""

    def __init__(self):
        self.cond = threading.Condition()
        self.frame = b""
        self.seq = 0
        self.stop = threading.Event()
        # actieve weergave voor input-mapping:
        # ([(start_x, seg_b, seg_h, abs_x, abs_y), ...], totale_b, totale_h)
        self.view = ([(0, 1, 1, 0, 0)], 1, 1)
        # None = alle schermen naast elkaar; anders lijst indices (max 5)
        self.monitor = None
        # oorsprong van het virtuele scherm (negatief bij monitoren links)
        self.origin_x = _user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        self.origin_y = _user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        # instelbare stream-breedte (bandbreedte-limiet, 0 = onverkleind)
        self.max_breedte = MAX_BREEDTE
        # oorspronkelijke resoluties per beeldscherm (herstel bij stop)
        self.orig_res = {}

    def _actief_scherm(self):
        """Monitor-dict van het eerste getoonde beeldscherm (voor resolutie)."""
        mons = enum_monitors()
        if not mons:
            return None
        for i in (self.monitor or []):
            if 0 <= i < len(mons):
                return mons[i]
        return mons[next((x["i"] for x in mons if x["primair"]), 0)]

    def huidige_resolutie(self):
        """(breedte, hoogte, apparaatnaam) van het getoonde beeldscherm."""
        mon = self._actief_scherm()
        return ((mon["w"], mon["h"], mon["naam"]) if mon else (0, 0, None))

    def zet_resolutie(self, w: int, h: int) -> bool:
        """Verander de echte resolutie van het getoonde beeldscherm."""
        mon = self._actief_scherm()
        if not mon:
            return False
        if (w, h) == (mon["w"], mon["h"]):
            return True
        self.orig_res.setdefault(mon["naam"], (mon["w"], mon["h"]))
        return _zet_resolutie(mon["naam"], w, h)

    def herstel_resoluties(self, log):
        """Zet gewijzigde beeldschermen terug op hun eigen stand."""
        for dev, (w, h) in list(self.orig_res.items()):
            if not _zet_resolutie(dev, w, h):
                log(f"Resolutie van {dev} kon niet worden hersteld.",
                    "WARNING")
        self.orig_res.clear()

    def capture_loop(self, log):
        from PIL import Image, ImageGrab
        log(f"Schermcapture gestart ({FPS} fps, stream max "
            f"{MAX_BREEDTE}px breed per scherm).")
        while not self.stop.is_set():
            try:
                mons = enum_monitors()
                sel = self.monitor  # None = alle; lijst indices = selectie
                gekozen = ([mons[i] for i in sel if 0 <= i < len(mons)]
                           if sel else [])
                if sel and not gekozen:
                    sel = None  # selectie bestaat niet meer -> alles
                if gekozen and len(gekozen) == 1:
                    # één gekozen beeldscherm
                    r = gekozen[0]["rect"]
                    img = ImageGrab.grab(bbox=r, all_screens=True)
                    self.view = ([(0, img.size[0], img.size[1], r[0], r[1])],
                                 img.size[0], img.size[1])
                elif gekozen:
                    # meerdere gekozen schermen (max 5) naast elkaar
                    delen = [(m, ImageGrab.grab(bbox=m["rect"],
                                                all_screens=True))
                             for m in gekozen]
                    tot_w = sum(im.size[0] for _, im in delen)
                    max_h = max(im.size[1] for _, im in delen)
                    img = Image.new("RGB", (tot_w, max_h))
                    segs, x = [], 0
                    for m, im in delen:
                        img.paste(im, (x, 0))
                        segs.append((x, im.size[0], im.size[1],
                                     m["x"], m["y"]))
                        x += im.size[0]
                    self.view = (segs, tot_w, max_h)
                else:
                    # alle schermen naast elkaar
                    img = ImageGrab.grab(all_screens=True)
                    self.view = ([(0, img.size[0], img.size[1],
                                   self.origin_x, self.origin_y)],
                                 img.size[0], img.size[1])
                # bandbreedte-limiet schaalt mee met het aantal schermen
                mb = self.max_breedte * min(len(self.view[0]), 3)
                if mb and img.size[0] > mb:
                    nh = int(img.size[1] * mb / img.size[0])
                    img = img.resize((mb, nh))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=JPEG_KWALITEIT)
                with self.cond:
                    self.frame = buf.getvalue()
                    self.seq += 1
                    self.cond.notify_all()
            except Exception as exc:
                log(f"Capture-fout: {exc}", "WARNING")
                time.sleep(1)
            time.sleep(1 / FPS)


# ----------------------------------------------------------- invoerinjectie
_SPECIAL_KEYS = {
    "Enter": 0x0D, "Backspace": 0x08, "Tab": 0x09, "Escape": 0x1B,
    "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21,
    "PageDown": 0x22, "ArrowLeft": 0x25, "ArrowUp": 0x26,
    "ArrowRight": 0x27, "ArrowDown": 0x28, "ShiftLeft": 0xA0,
    "ShiftRight": 0xA1, "ControlLeft": 0xA2, "ControlRight": 0xA3,
    "AltLeft": 0xA4, "AltRight": 0xA5, "MetaLeft": 0x5B, "MetaRight": 0x5C,
    "CapsLock": 0x14, "Space": 0x20,
}
for _i in range(1, 13):
    _SPECIAL_KEYS[f"F{_i}"] = 0x6F + _i

# knop -> (down-vlag, up-vlag) voor mouse_event: links, middel, rechts
_MOUSE_FLAGS = {0: (0x0002, 0x0004), 1: (0x0020, 0x0040), 2: (0x0008, 0x0010)}


def _toets(code: str, key: str, down: bool):
    """Vertaal een browser-toets (code/key) naar een Windows-toetsaanslag."""
    up = 0 if down else 2  # KEYEVENTF_KEYUP = 2
    if code in _SPECIAL_KEYS:
        _user32.keybd_event(_SPECIAL_KEYS[code], 0, up, 0)
    elif key and len(key) == 1:
        r = _user32.VkKeyScanW(ord(key))
        if r == -1:
            return
        vk = r & 0xFF
        shift = bool((r >> 8) & 1)
        if shift:
            _user32.keybd_event(0xA0, 0, 0 if down else 2, 0)
        _user32.keybd_event(vk, 0, up, 0)
        if shift and not down:
            pass  # shift komt bij de volgende 'down' vanzelf weer omhoog


def _drives() -> list:
    """Beschikbare stationsletters (C:\\, D:\\, ...)."""
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]


def _fs_items(pad: str) -> list:
    """Mapinhoud: mappen eerst, dan bestanden (alfabetisch)."""
    items = []
    with os.scandir(pad) as it:
        for e in it:
            try:
                st = e.stat()
                is_dir = e.is_dir()
                items.append({
                    "naam": e.name,
                    "dir": is_dir,
                    "grootte": 0 if is_dir else st.st_size,
                    "gewijzigd": time.strftime("%d-%m-%Y %H:%M",
                                               time.localtime(st.st_mtime)),
                })
            except OSError:
                pass
    items.sort(key=lambda x: (not x["dir"], x["naam"].lower()))
    return items


def verwerk_input(data: dict, scherm: Scherm):
    """Verwerk één input-event uit de browser."""
    t = data.get("t")
    if t in ("move", "down", "up"):
        segs, tot_w, tot_h = scherm.view
        vx = min(max(float(data.get("x", 0)), 0.0), 1.0) * tot_w
        vy = min(max(float(data.get("y", 0)), 0.0), 1.0) * tot_h
        # segmenten liggen horizontaal naast elkaar (top-aligned)
        sx, sw, sh, ax, ay = segs[-1]
        for seg in segs:
            if vx < seg[0] + seg[1]:
                sx, sw, sh, ax, ay = seg
                break
        x = ax + int(min(max(vx - sx, 0.0), sw - 1.0))
        y = ay + int(min(max(vy, 0.0), sh - 1.0))
        _user32.SetCursorPos(x, y)
        if t in ("down", "up"):
            b = int(data.get("b", 0))
            fl = _MOUSE_FLAGS.get(b, _MOUSE_FLAGS[0])
            _user32.mouse_event(fl[0] if t == "down" else fl[1], 0, 0, 0, 0)
    elif t == "wheel":
        _user32.mouse_event(0x0800, 0, 0, int(data.get("d", 0)), 0)  # WHEEL
    elif t == "key":
        _toets(data.get("code", ""), data.get("key", ""), bool(data.get("down")))


# --------------------------------------------------------------- webpagina
_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>CdR Toolkit - Remote</title>
<style>
:root{--bg:#0f1115;--balk:#181c24;--lijn:#262c38;--tekst:#e5e9f0;--accent:#4f6ef7;--muted:#8b93a5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tekst);height:100vh;
font-family:'Segoe UI',system-ui,sans-serif;overflow:hidden;
display:flex;flex-direction:column}
#balk{display:flex;align-items:center;gap:6px;padding:8px 12px;
background:var(--balk);border-bottom:1px solid var(--lijn);flex-wrap:wrap}
#balk .titel{font-weight:600;font-size:14px;margin-right:4px}
#balk .dot{color:#22c55e;font-size:12px;margin-right:8px}
#balk button{background:#232936;color:var(--tekst);border:1px solid var(--lijn);
border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}
#balk button.on{background:var(--accent);border-color:var(--accent);color:#fff}
#balk button:active{filter:brightness(1.25)}
#balk .spacer{flex:1}
#balk select{background:#232936;color:var(--tekst);border:1px solid var(--lijn);
border-radius:6px;padding:6px 8px;font-size:13px;cursor:pointer}
#stage{flex:1;display:flex;align-items:center;justify-content:center;
background:#000;position:relative;overflow:hidden}
#stage img{width:100%;height:100%;object-fit:contain;touch-action:none;
user-select:none;-webkit-user-select:none;-webkit-user-drag:none;
cursor:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'%3E%3Cpath d='M4 2 L4 18.5 L8.6 14 L11.2 20.5 L13.6 19.5 L11 13 L17.5 12.5 Z' fill='%230b3d91' stroke='%23ffffff' stroke-width='1.3'/%3E%3C/svg%3E") 4 2, default}
#kbWrap{display:none;position:absolute;bottom:12px;left:50%;
transform:translateX(-50%);background:var(--balk);border:1px solid var(--lijn);
border-radius:8px;padding:8px;z-index:10}
#kbWrap input{font-size:16px;padding:8px;width:240px;background:var(--bg);
color:var(--tekst);border:1px solid var(--lijn);border-radius:6px}
#files{flex:1;display:none;gap:8px;padding:8px;overflow:hidden}
.kolom{flex:1;display:flex;flex-direction:column;background:var(--balk);
border:1px solid var(--lijn);border-radius:8px;min-width:0}
.kop{padding:8px 10px;font-weight:600;font-size:13px;
border-bottom:1px solid var(--lijn)}
.crumb{padding:6px 10px;font-size:11px;color:var(--muted);
border-bottom:1px solid var(--lijn);word-break:break-all}
.lijst{flex:1;overflow-y:auto;font-size:13px}
.rij{display:flex;gap:6px;padding:4px 10px;cursor:pointer;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}
.rij:hover{background:#232936}
.rij.sel{background:var(--accent);color:#fff}
.rij .grootte{margin-left:auto;color:var(--muted);font-size:11px}
.acties{display:flex;gap:6px;padding:8px;border-top:1px solid var(--lijn);
flex-wrap:wrap}
.acties button{background:#232936;color:var(--tekst);border:1px solid var(--lijn);
border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer}
.hint{padding:6px 10px;font-size:11px;color:var(--muted)}
</style></head><body>
<div id=balk>
 <span class=titel>CdR Toolkit</span><span class=dot>●</span>
 <button id=tabBeeld class=on>🖥 Beeld</button>
 <button id=tabBest>📁 Bestanden</button>
 <span style="font-size:12px;color:var(--muted)">Schermen:</span>
 <span id=mons></span>
 <label for=selRes style="font-size:12px;color:var(--muted)">Resolutie</label>
 <select id=selRes title="Echte schermresolutie van deze pc (RDP-stijl)"></select>
 <span class=spacer></span>
 <button id=btnKb>⌨ Toetsenbord</button>
 <button id=btnFs>⛶ Volledig scherm</button>
 <button id=btnRe>↻ Opnieuw</button>
 <button id=btnOut>🚪 Uitloggen</button>
</div>
<div id=stage>
 <img id=s src="/stream">
 <div id=kbWrap><input id=kbIn autocomplete=off autocapitalize=off
  spellcheck=false placeholder="Typ hier..."></div>
</div>
<div id=files>
 <div class=kolom>
  <div class=kop>Extern (deze pc)</div>
  <div class=crumb id=crumbR>...</div>
  <div class=lijst id=listR></div>
  <div class=acties>
   <button id=btnDl>⬇ Download</button>
   <button id=btnMk>+ Map</button>
   <button id=btnRn>Naam wijzigen</button>
   <button id=btnDel>Verwijderen</button>
  </div>
 </div>
 <div class=kolom>
  <div class=kop>Lokaal (jouw apparaat)</div>
  <div class=hint>Kies bestanden om te uploaden naar de externe map.
   Downloads gaan naar je eigen downloadmap.</div>
  <div class=acties><input type=file id=filePick multiple
   style="font-size:12px;color:var(--tekst)"></div>
  <div class=lijst id=listL></div>
  <div class=acties><button id=btnUl>⬆ Upload naar extern</button></div>
 </div>
</div>
<script>
var img=document.getElementById('s');
function pos(e){var r=img.getBoundingClientRect();
 var iw=img.naturalWidth,ih=img.naturalHeight;
 if(!iw||!ih)return{x:0,y:0};
 // object-fit:contain -> reken de zwarte balken rond het beeld mee
 var s=Math.min(r.width/iw,r.height/ih),dw=iw*s,dh=ih*s,
  ox=r.left+(r.width-dw)/2,oy=r.top+(r.height-dh)/2;
 return{x:Math.min(1,Math.max(0,(e.clientX-ox)/dw)),
        y:Math.min(1,Math.max(0,(e.clientY-oy)/dh))}}
function stuur(d){fetch('/input',{method:'POST',
 headers:{'Content-Type':'application/json'},
 body:JSON.stringify(d)}).catch(function(){})}
function klik(p,b){stuur({t:'move',x:p.x,y:p.y});
 stuur({t:'down',x:p.x,y:p.y,b:b});
 setTimeout(function(){stuur({t:'up',x:p.x,y:p.y,b:b})},40)}
function herverbind(){img.src='/stream?t='+Date.now()}
document.getElementById('btnRe').onclick=herverbind;
// ---- resolutie (echt, zoals RDP) + uitloggen ----
var selRes=document.getElementById('selRes');
function laadRes(){fetch('/config').then(function(r){return r.json()})
 .then(function(c){selRes.innerHTML='';
 (c.resoluties||[]).forEach(function(m){var o=document.createElement('option');
  o.value=m.w+'x'+m.h;o.textContent=m.w+' × '+m.h;
  if(c.res&&m.w===c.res.w&&m.h===c.res.h)o.selected=true;
  selRes.appendChild(o)})})}
function zetRes(w,h){return fetch('/resolutie',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({w:w,h:h})})
 .then(function(r){return r.json()}).then(function(d){
  if(d.fout)alert(d.fout);herverbind();laadRes()})}
selRes.onchange=function(){var p=selRes.value.split('x');
 zetRes(parseInt(p[0],10),parseInt(p[1],10))};
document.getElementById('btnOut').onclick=function(){location='/logout'};
document.getElementById('btnFs').onclick=function(){
 if(document.fullscreenElement){document.exitFullscreen();return}
 // host-resolutie gelijk aan dit scherm -> echt schermvullend zonder balken
 zetRes(screen.width,screen.height).catch(function(){}).then(function(){
  document.documentElement.requestFullscreen()})};
laadRes();
var kbWrap=document.getElementById('kbWrap'),kbIn=document.getElementById('kbIn'),kbAan=false;
document.getElementById('btnKb').onclick=function(){kbAan=!kbAan;
 kbWrap.style.display=kbAan?'block':'none';if(kbAan)kbIn.focus()};
var lastKb='';
kbIn.addEventListener('input',function(){var v=kbIn.value,i;
 if(v.length>lastKb.length)for(i=lastKb.length;i<v.length;i++){
  stuur({t:'key',code:'',key:v[i],down:true});
  stuur({t:'key',code:'',key:v[i],down:false})}
 lastKb=v});
kbIn.addEventListener('keydown',function(e){e.stopPropagation();
 if(e.key==='Enter'){e.preventDefault();
  stuur({t:'key',code:'Enter',key:'Enter',down:true});
  stuur({t:'key',code:'Enter',key:'Enter',down:false});kbIn.value='';lastKb=''}
 else if(e.key==='Backspace'){e.preventDefault();
  stuur({t:'key',code:'Backspace',key:'Backspace',down:true});
  stuur({t:'key',code:'Backspace',key:'Backspace',down:false})}});
// ---- schermkeuze (meerdere tegelijk mogelijk, max 5) ----
var mb=document.getElementById('mons');
function mkKnop(txt,act,fn){var b=document.createElement('button');
 b.textContent=txt;if(act)b.className='on';b.onclick=fn;return b}
function laadMons(){fetch('/monitors').then(function(r){return r.json()})
 .then(function(d){mb.innerHTML='';
 var act=d.active;
 mb.appendChild(mkKnop('Alle schermen',act===null||act===undefined,
  function(){kiesMon(null)}));
 (d.monitors||[]).forEach(function(m){
  var aan=act&&act.indexOf(m.i)>=0;
  mb.appendChild(mkKnop('Scherm '+(m.i+1)+(m.primair?' *':'')+
   ' ('+m.w+' × '+m.h+')',aan,function(){toggleMon(m.i)}))})})}
function kiesMon(i){fetch('/monitor',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({m:i})})
 .then(function(){herverbind();laadMons();laadRes()})}
function toggleMon(i){fetch('/monitor',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({toggle:i})})
 .then(function(r){return r.json().then(function(d){
  if(!r.ok&&d.fout)alert(d.fout);
  herverbind();laadMons();laadRes()})})}
laadMons();
// ---- muis (desktop) ----
var lastMove=0;
img.addEventListener('mousemove',function(e){var n=Date.now();
 if(n-lastMove<40)return;lastMove=n;var p=pos(e);
 stuur({t:'move',x:p.x,y:p.y})});
img.addEventListener('mousedown',function(e){e.preventDefault();var p=pos(e);
 stuur({t:'down',x:p.x,y:p.y,b:e.button})});
img.addEventListener('mouseup',function(e){var p=pos(e);
 stuur({t:'up',x:p.x,y:p.y,b:e.button})});
img.addEventListener('contextmenu',function(e){e.preventDefault()});
img.addEventListener('wheel',function(e){e.preventDefault();
 stuur({t:'wheel',d:e.deltaY<0?120:-120})},{passive:false});
img.addEventListener('dragstart',function(e){e.preventDefault()});
// ---- aanraking (tablet/telefoon) ----
var T={start:null,moved:false,timer:0,twee:false,t0:0,acc:0};
img.addEventListener('touchstart',function(e){e.preventDefault();
 if(e.touches.length===2){T.twee=true;clearTimeout(T.timer);T.acc=0;
  T.c={x:(e.touches[0].clientX+e.touches[1].clientX)/2,
       y:(e.touches[0].clientY+e.touches[1].clientY)/2};T.moved=false;return}
 T.twee=false;T.start=pos(e.touches[0]);T.moved=false;T.t0=Date.now();
 T.timer=setTimeout(function(){if(!T.moved){klik(T.start,2);T.moved=true}},550);
},{passive:false});
img.addEventListener('touchmove',function(e){e.preventDefault();
 if(T.twee&&e.touches.length===2){
  var c={x:(e.touches[0].clientX+e.touches[1].clientX)/2,
         y:(e.touches[0].clientY+e.touches[1].clientY)/2};
  var dy=c.y-T.c.y;T.c=c;T.acc+=dy;T.moved=true;
  if(Math.abs(T.acc)>50){stuur({t:'wheel',d:T.acc<0?120:-120});T.acc=0}
  return}
 if(!T.start)return;var p=pos(e.touches[0]);
 if(!T.moved&&Math.hypot(p.x-T.start.x,p.y-T.start.y)>0.008){
  T.moved=true;clearTimeout(T.timer)}
 if(T.moved)stuur({t:'move',x:p.x,y:p.y});
},{passive:false});
img.addEventListener('touchend',function(e){e.preventDefault();
 clearTimeout(T.timer);
 if(T.twee){if(!T.moved)klik(pos(e.changedTouches[0]),2);
  T.twee=false;return}
 if(!T.moved&&Date.now()-T.t0<400){var p=pos(e.changedTouches[0]);klik(p,0)}
},{passive:false});
// ---- toetsenbord (desktop) ----
var spec={Enter:1,Backspace:1,Tab:1,Escape:1,Delete:1,Home:1,End:1,PageUp:1,
 PageDown:1,ArrowLeft:1,ArrowUp:1,ArrowRight:1,ArrowDown:1};
document.addEventListener('keydown',function(e){
 if(e.target===kbIn)return;
 if(spec[e.code]||e.key.length===1){e.preventDefault();
 stuur({t:'key',code:e.code,key:e.key,down:true})}});
document.addEventListener('keyup',function(e){
 if(e.target===kbIn)return;
 if(spec[e.code]||e.key.length===1){e.preventDefault();
 stuur({t:'key',code:e.code,key:e.key,down:false})}});
// ---- tabbladen ----
var stage=document.getElementById('stage'),filesEl=document.getElementById('files');
function toonTab(best){
 stage.style.display=best?'none':'flex';
 filesEl.style.display=best?'flex':'none';
 document.getElementById('tabBeeld').className=best?'':'on';
 document.getElementById('tabBest').className=best?'on':'';
 if(best&&hostPad===null)fsGa('')}
document.getElementById('tabBeeld').onclick=function(){toonTab(false)};
document.getElementById('tabBest').onclick=function(){toonTab(true)};
// ---- bestandsbeheer (extern = host) ----
var hostPad=null,hostSel=null,localFiles=[];
function esc(s){var d=document.createElement('div');d.textContent=s;
 return d.innerHTML}
function fmtG(n){if(n<1024)return n+' B';if(n<1048576)return(n/1024).toFixed(1)+' KB';
 if(n<1073741824)return(n/1048576).toFixed(1)+' MB';
 return(n/1073741824).toFixed(2)+' GB'}
function joinPad(a,b){return a?a.replace(/\\\\+$/,'')+'\\\\'+b:b}
function rij(label,it,onclick){var d=document.createElement('div');
 d.className='rij';
 d.innerHTML='<span>'+esc(label)+'</span>'+
  (it&&!it.dir?'<span class=grootte>'+fmtG(it.grootte)+'</span>':'');
 d.onclick=onclick;return d}
function fsGa(pad){fetch('/fs/list?pad='+encodeURIComponent(pad))
 .then(function(r){return r.json()}).then(function(d){
 if(d.fout){alert(d.fout);return}
 hostPad=d.pad;hostSel=null;
 document.getElementById('crumbR').textContent=d.pad||'Deze computer';
 var el=document.getElementById('listR');el.innerHTML='';
 if(d.pad){var up=document.createElement('div');up.className='rij';
  up.textContent='[..]';up.onclick=function(){fsGa(d.parent||'')};
  el.appendChild(up)}
 d.items.forEach(function(it){
  var r=rij((it.dir?'📁 ':'📄 ')+it.naam,it,function(){
   if(it.dir){fsGa(joinPad(hostPad,it.naam))}
   else{var was=r.className==='rij sel';
    document.querySelectorAll('#listR .rij').forEach(function(x){
     x.className='rij'});
    r.className=was?'rij':'rij sel';
    hostSel=was?null:joinPad(hostPad,it.naam)}});
  el.appendChild(r)})})}
function fsActie(d,ververs){fetch('/fs/actie',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})
 .then(function(r){return r.json()}).then(function(x){
  if(x.fout)alert(x.fout);if(ververs!==false)fsGa(hostPad)})}
document.getElementById('btnDl').onclick=function(){
 if(!hostSel){alert('Selecteer eerst een bestand in het externe paneel.');return}
 var a=document.createElement('a');
 a.href='/fs/download?pad='+encodeURIComponent(hostSel);
 a.download=hostSel.split('\\\\').pop();document.body.appendChild(a);a.click();
 a.remove()};
document.getElementById('btnMk').onclick=function(){
 var n=prompt('Naam van de nieuwe map:');if(n)
 fsActie({actie:'mkdir',pad:hostPad,naam:n})};
document.getElementById('btnRn').onclick=function(){
 if(!hostSel){alert('Selecteer eerst een bestand.');return}
 var n=prompt('Nieuwe naam:',hostSel.split('\\\\').pop());if(n)
 fsActie({actie:'rename',pad:hostSel,naam:n})};
document.getElementById('btnDel').onclick=function(){
 if(!hostSel){alert('Selecteer eerst een bestand of map.');return}
 if(confirm('Definitief verwijderen:\\n'+hostSel))
 fsActie({actie:'delete',pad:hostSel})};
// ---- lokaal paneel (upload/download) ----
document.getElementById('filePick').onchange=function(e){
 localFiles=Array.from(e.target.files);
 var el=document.getElementById('listL');el.innerHTML='';
 localFiles.forEach(function(f){
  el.appendChild(rij('📄 '+f.name,{grootte:f.size},function(){}))})};
document.getElementById('btnUl').onclick=function(){
 if(!localFiles.length){alert('Kies eerst bestanden.');return}
 if(!hostPad){alert('Navigeer eerst naar een externe map.');return}
 var klaar=0;
 localFiles.forEach(function(f){
  fetch('/fs/upload?pad='+encodeURIComponent(hostPad)+'&naam='+
   encodeURIComponent(f.name),{method:'POST',body:f})
  .then(function(r){return r.json()}).then(function(x){
   if(x.fout)alert('Upload mislukt voor '+f.name+': '+x.fout);
   if(++klaar===localFiles.length)fsGa(hostPad)})})};
</script></body></html>"""

_LOGIN = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>CdR Toolkit - Inloggen</title>
<style>body{background:#111;color:#ddd;font-family:sans-serif;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}
form{background:#1e1e1e;padding:24px;border-radius:8px;text-align:center}
input,button{padding:8px;font-size:16px;margin:4px}</style></head><body>
<form method=post action="/login"><h3>CdR Toolkit</h3>
<input type=password name=pw placeholder="Wachtwoord" autofocus>
<button>Inloggen</button>{fout}</form></body></html>"""


# ---------------------------------------------------------------- handler
class _Handler(BaseHTTPRequestHandler):
    server_version = "CdRToolkit/remote"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # stil houden; app-log volstaat
        pass

    def _auth(self) -> bool:
        c = cookies.SimpleCookie(self.headers.get("Cookie"))
        m = c.get("token")
        return bool(m) and m.value == self.server.sessie.token

    def _stuur_html(self, html: str, status=200):
        b = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _stuur_json(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _logout(self):
        """Maak het sessietoken ongeldig en verwijder de login-cookie."""
        self.server.sessie.token = secrets.token_hex(8)
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie",
                         "token=; Max-Age=0; HttpOnly; SameSite=Strict")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --------------------------------------------------- bestandstransfer
    def _query(self) -> dict:
        return parse_qs(urlparse(self.path).query)

    def _fs_list(self):
        pad = self._query().get("pad", [""])[0]
        try:
            if not pad:
                items = [{"naam": d, "dir": True, "grootte": 0,
                          "gewijzigd": ""} for d in _drives()]
                self._stuur_json({"pad": "", "parent": None, "items": items})
                return
            parent = os.path.dirname(pad.rstrip("\\/")) or ""
            if parent == pad.rstrip("\\/"):
                parent = ""
            self._stuur_json({"pad": pad, "parent": parent,
                              "items": _fs_items(pad)})
        except OSError as exc:
            self._stuur_json({"fout": str(exc)}, status=400)

    def _fs_download(self):
        pad = self._query().get("pad", [""])[0]
        try:
            grootte = os.path.getsize(pad)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(grootte))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{os.path.basename(pad)}"')
            self.end_headers()
            with open(pad, "rb") as fh:
                while True:
                    blok = fh.read(1024 * 1024)
                    if not blok:
                        break
                    self.wfile.write(blok)
        except OSError:
            self.send_error(404)

    def _fs_upload(self):
        q = self._query()
        doelmap = q.get("pad", [""])[0]
        naam = os.path.basename(q.get("naam", ["upload.bin"])[0])
        rest = int(self.headers.get("Content-Length", 0))
        try:
            os.makedirs(doelmap, exist_ok=True)
            with open(os.path.join(doelmap, naam), "wb") as fh:
                while rest > 0:
                    blok = self.rfile.read(min(1024 * 1024, rest))
                    if not blok:
                        break
                    fh.write(blok)
                    rest -= len(blok)
            self._stuur_json({"ok": True, "naam": naam})
        except OSError as exc:
            self._stuur_json({"fout": str(exc)}, status=400)

    def _fs_actie(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
            actie, pad = d.get("actie"), d.get("pad", "")
            naam = d.get("naam", "")
            doel = d.get("doel", "")
            if actie == "mkdir":
                os.makedirs(os.path.join(pad, naam or "Nieuwe map"),
                            exist_ok=True)
            elif actie == "delete":
                if os.path.isdir(pad):
                    shutil.rmtree(pad)
                else:
                    os.remove(pad)
            elif actie == "rename":
                os.rename(pad, os.path.join(os.path.dirname(pad), naam))
            elif actie == "copy":
                if os.path.isdir(pad):
                    shutil.copytree(pad, os.path.join(
                        doel, os.path.basename(pad)), dirs_exist_ok=True)
                else:
                    shutil.copy2(pad, doel)
            elif actie == "move":
                shutil.move(pad, doel)
            else:
                raise ValueError(f"onbekende actie: {actie}")
            self._stuur_json({"ok": True})
        except (OSError, ValueError, shutil.Error) as exc:
            self._stuur_json({"fout": str(exc)}, status=400)

    # ------------------------------------------------------------- GET
    def do_GET(self):
        pad = self.path.split("?", 1)[0]  # query-string negeren bij routering
        if pad == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if pad == "/logout":
            self._logout()
            return
        if not self._auth():
            self._stuur_html(_LOGIN.replace("{fout}", ""))
            return
        if pad in ("/", "/index.html"):
            self._stuur_html(_PAGE)
        elif pad == "/stream":
            self._mjpeg()
        elif pad == "/monitors":
            self._stuur_json({
                "monitors": [{"i": m["i"], "w": m["w"], "h": m["h"],
                              "primair": m["primair"]}
                             for m in enum_monitors()],
                "active": self.server.sessie.scherm.monitor,
            })
        elif pad == "/config":
            # huidige én ondersteunde resoluties van het getoonde beeldscherm
            w, h, dev = self.server.sessie.scherm.huidige_resolutie()
            self._stuur_json({
                "res": {"w": w, "h": h},
                "resoluties": [{"w": a, "h": b}
                               for a, b in lijst_resoluties(dev)],
            })
        elif pad == "/fs/list":
            self._fs_list()
        elif pad == "/fs/download":
            self._fs_download()
        else:
            self.send_error(404)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        pad = self.path.split("?", 1)[0]
        if pad == "/login":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode("utf-8", "replace")
            velden = dict(p.split("=", 1) for p in body.split("&") if "=" in p)
            if unquote_plus(velden.get("pw", "")) == self.server.sessie.wachtwoord:
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie",
                                 f"token={self.server.sessie.token}; "
                                 "HttpOnly; SameSite=Strict")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                fout = "<p style='color:#f66'>Onjuist wachtwoord</p>"
                self._stuur_html(_LOGIN.replace("{fout}", fout), status=401)
            return
        if pad == "/input" and self._auth():
            n = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
                verwerk_input(data, self.server.sessie.scherm)
            except Exception:
                pass
            self.send_response(204)
            self.end_headers()
            return
        if pad == "/fs/upload" and self._auth():
            self._fs_upload()
            return
        if pad == "/fs/actie" and self._auth():
            self._fs_actie()
            return
        if pad == "/resolutie" and self._auth():
            # echte resolutie van het getoonde beeldscherm (RDP-stijl)
            n = int(self.headers.get("Content-Length", 0))
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
                w, h = int(d.get("w")), int(d.get("h"))
                if not (200 <= w <= 16000 and 200 <= h <= 16000):
                    raise ValueError
            except (ValueError, TypeError):
                self._stuur_json({"fout": "Ongeldige resolutie."}, status=400)
                return
            scherm = self.server.sessie.scherm
            _w, _h, dev = scherm.huidige_resolutie()
            modus = _beste_modus(lijst_resoluties(dev), w, h)
            if modus and scherm.zet_resolutie(*modus):
                self._stuur_json({"ok": True, "w": modus[0], "h": modus[1]})
            else:
                self._stuur_json(
                    {"fout": "Deze resolutie kon niet worden ingesteld."},
                    status=400)
            return
        if pad == "/monitor" and self._auth():
            # schermkeuze: m=null -> alle; m=[i,..] -> selectie (max 5);
            # toggle=i -> scherm aan/uit in de selectie
            n = int(self.headers.get("Content-Length", 0))
            scherm = self.server.sessie.scherm
            n_mon = len(enum_monitors())
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                data = {}
            if "toggle" in data:
                try:
                    i = int(data["toggle"])
                except (ValueError, TypeError):
                    i = -1
                cur = list(scherm.monitor or [])
                if i in cur:
                    cur.remove(i)
                elif 0 <= i < n_mon:
                    if len(cur) >= 5:
                        self._stuur_json(
                            {"fout": "Maximaal 5 schermen tegelijk."},
                            status=400)
                        return
                    cur.append(i)
                    cur.sort()
                scherm.monitor = cur or None
            else:
                m = data.get("m")
                if m is None:
                    scherm.monitor = None
                elif isinstance(m, list):
                    sel = sorted({int(v) for v in m
                                  if isinstance(v, (int, float))
                                  and 0 <= int(v) < n_mon})
                    scherm.monitor = sel[:5] or None
                else:
                    try:
                        idx = int(m)
                        if 0 <= idx < n_mon:
                            scherm.monitor = [idx]
                    except (ValueError, TypeError):
                        pass
            self._stuur_json({"ok": True, "active": scherm.monitor})
            return
        self.send_error(404)

    # ------------------------------------------------------------ MJPEG
    def _mjpeg(self):
        scherm = self.server.sessie.scherm
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        laatste = 0
        try:
            while not scherm.stop.is_set():
                with scherm.cond:
                    if scherm.seq == laatste:
                        scherm.cond.wait(timeout=2)
                    if scherm.seq == laatste:
                        continue
                    laatste = scherm.seq
                    data = scherm.frame
                kop = (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: %d\r\n\r\n" % len(data))
                self.wfile.write(kop + data + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # viewer heeft de verbinding gesloten


# --------------------------------------------------------------- beheerder
class _StilleServer(ThreadingHTTPServer):
    """ThreadingHTTPServer die verbroken clientverbindingen geruisloos negeert."""

    def handle_error(self, request, client_address):
        pass


class RemoteSessie:
    def __init__(self, poort: int, vast_wachtwoord: str | None = None):
        self.poort = poort
        # vast wachtwoord uit instellingen, anders willekeurig (RustDesk-stijl)
        self.wachtwoord = (vast_wachtwoord or
                           f"{secrets.randbelow(9000) + 1000}-"
                           f"{secrets.randbelow(9000) + 1000}")
        self.token = secrets.token_hex(8)
        self.scherm = Scherm()
        self.httpd = None


class RemoteServer:
    """Beheert de levenscyclus van de webserver (singleton)."""

    def __init__(self):
        self.sessie: RemoteSessie | None = None

    def start(self, log, poort: int = POORT,
              wachtwoord: str | None = None) -> bool:
        if self.sessie:
            log("Remote-webserver draait al.", "WARNING")
            return True
        try:
            from PIL import Image, ImageGrab  # noqa: F401 - dep-check
        except Exception as exc:
            log(f"Pillow ontbreekt of kan niet laden: {exc}", "ERROR")
            log("Installeer/upgrade Pillow in de Python-omgeving van deze app:",
                "ERROR")
            log(f'"{sys.executable}" -m pip install --upgrade Pillow',
                "ERROR")
            return False
        sessie = RemoteSessie(poort, vast_wachtwoord=wachtwoord)
        try:
            httpd = _StilleServer(("0.0.0.0", poort), _Handler)
        except OSError as exc:
            log(f"Poort {poort} is niet beschikbaar: {exc}", "ERROR")
            return False
        httpd.daemon_threads = True
        httpd.sessie = sessie
        sessie.httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        threading.Thread(target=sessie.scherm.capture_loop, args=(log,),
                         daemon=True).start()
        self.sessie = sessie
        # standaard in 1920x1080 starten (RDP-stijl); bij stop hersteld
        w, h, dev = sessie.scherm.huidige_resolutie()
        if (w, h) != STANDAARD_RESOLUTIE:
            modus = _beste_modus(lijst_resoluties(dev), *STANDAARD_RESOLUTIE)
            if modus and modus != (w, h):
                if sessie.scherm.zet_resolutie(*modus):
                    log(f"Schermresolutie tijdelijk op {modus[0]}x{modus[1]} "
                        "gezet (bij stoppen weer hersteld).")
                else:
                    log("Schermresolutie kon niet worden aangepast.",
                        "WARNING")
        self._firewall(log, poort, toevoegen=True)
        log(f"Remote-webserver actief op poort {poort}.", "SUCCESS")
        return True

    def stop(self, log) -> bool:
        if not self.sessie:
            log("Remote-webserver draait niet.", "WARNING")
            return False
        s = self.sessie
        s.scherm.stop.set()
        with s.scherm.cond:
            s.scherm.cond.notify_all()
        s.httpd.shutdown()
        s.httpd.server_close()
        s.scherm.herstel_resoluties(log)
        self._firewall(log, s.poort, toevoegen=False)
        self.sessie = None
        log("Remote-webserver gestopt.", "SUCCESS")
        return True

    def actief(self) -> bool:
        return self.sessie is not None

    @staticmethod
    def _firewall(log, poort: int, toevoegen: bool):
        """Zet of verwijder een inkomende firewall-regel (we zijn verhoogd)."""
        from core import runner
        if toevoegen:
            code = runner.run_quiet(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 "name=CdR Toolkit Remote", "dir=in", "action=allow",
                 "protocol=TCP", f"localport={poort}"])
            if code == 0:
                log(f"Firewall-regel voor poort {poort} toegevoegd.")
            else:
                log("Firewall-regel toevoegen mislukt; controleer handmatig.",
                    "WARNING")
        else:
            # verwijder de regel van zowel de huidige als de oude app-naam
            for naam in ("CdR Toolkit Remote", "CharlesOnderhoud Remote"):
                runner.run_quiet(
                    ["netsh", "advfirewall", "firewall", "delete", "rule",
                     f"name={naam}"])
            log("Firewall-regel verwijderd.")
