"""Ziggo TV: start/stop de aparte viewer (eigen proces met eigen main thread)."""
import os
import subprocess
import sys

_proc = None


def actief() -> bool:
    return _proc is not None and _proc.poll() is None


def start(log) -> bool:
    """Start het Ziggo TV-venster (ziggogo.tv in WebView2)."""
    global _proc
    log("=== Ziggo TV starten ===", "STEP")
    if actief():
        log("De Ziggo TV-viewer draait al.", "WARNING")
        return True
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--ziggo-viewer"]
    else:
        cmd = [sys.executable, os.path.abspath(sys.argv[0]), "--ziggo-viewer"]
    try:
        _proc = subprocess.Popen(cmd)
    except OSError as exc:
        log(f"Viewer starten mislukt: {exc}", "ERROR")
        return False
    log("Ziggo TV-viewer gestart in een eigen venster.", "SUCCESS")
    log("Log in met je Ziggo-account (blijft bewaard). Sluiten kan met het "
        "kruisje van het venster of via 'Stop Ziggo TV' in deze app.", "INFO")
    return True


def stop(log) -> bool:
    """Stop de viewer (proces beëindigen)."""
    global _proc
    log("=== Ziggo TV stoppen ===", "STEP")
    if actief():
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
        log("Ziggo TV-viewer gestopt.", "SUCCESS")
    else:
        log("De viewer draaide niet.", "WARNING")
    _proc = None
    return True
