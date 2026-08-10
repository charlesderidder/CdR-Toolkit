"""Standalone Ziggo TV-viewer — draait in een EIGEN proces.

pywebview (WebView2) eist de main thread; die is in de hoofd-app van tkinter.
Daarom start de app deze module als apart proces:
    CdRToolkit.exe --ziggo-viewer
of: python main.py --ziggo-viewer

Het venster laadt ziggogo.tv, verder niets: geen knoppenbalk of overlays.
Cookies worden bewaard (storage_path), dus de Ziggo-login blijft bestaan.
Venstergrootte/-positie en de altijd-voorgrond-instelling worden bewaard in
de instellingen (config.json) en bij de volgende start weer toegepast.
"""
import sys

TITEL = "Ziggo TV — CdR Toolkit"
URL_HOME = "https://www.ziggogo.tv"


def main():
    """Entrypoint voor het viewer-proces."""
    try:
        import webview
    except ImportError:
        print("pywebview/pythonnet ontbreken: pip install pywebview pythonnet")
        sys.exit(1)

    from core import settings
    from core.logger import data_dir

    # herstel de vorige venstergrootte/-positie (default 1280x800 gecentreerd)
    geo = settings.get("ziggo_geometry", {})
    laatste = {}  # laatst bekende grootte/positie; opgeslagen bij sluiten

    window = webview.create_window(
        TITEL, URL_HOME,
        width=geo.get("w", 1280), height=geo.get("h", 800),
        x=geo.get("x"), y=geo.get("y"),
        on_top=bool(settings.get("altijd_voorgrond", True)))

    def bij_resize(w, h):
        laatste["w"], laatste["h"] = int(w), int(h)

    def bij_move(x, y):
        laatste["x"], laatste["y"] = int(x), int(y)

    def sla_geometry_op():
        if laatste:
            settings.set("ziggo_geometry", laatste)

    for event, handler in (("resized", bij_resize), ("moved", bij_move),
                           ("closed", sla_geometry_op)):
        try:
            ev = getattr(window.events, event)
            ev += handler  # EventSet ondersteunt += (geen toewijzing nodig)
        except Exception:
            pass  # oudere pywebview zonder resized/moved-events

    try:
        webview.start(private_mode=False,
                      storage_path=str(data_dir() / "webview2"))
    except TypeError:
        webview.start(private_mode=False)  # oudere pywebview zonder storage_path
