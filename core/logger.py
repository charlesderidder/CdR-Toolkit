"""Logging naar bestand én naar de UI (thread-safe via een queue)."""
import logging
import os
import queue
import shutil
import sys
import winreg
from datetime import datetime


def app_dir() -> str:
    """Map waarin de app draait (werkt ook als PyInstaller .exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _documenten() -> str:
    """Echte Documenten-map van de gebruiker (ook als die is verplaatst)."""
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Explorer\User Shell Folders") as k:
            pad, _ = winreg.QueryValueEx(k, "Personal")
            return os.path.expandvars(pad)
    except OSError:
        return os.path.join(os.path.expanduser("~"), "Documents")


def data_dir() -> str:
    """Centrale datamap: Documenten\\CharlesOnderhoud (wordt aangemaakt)."""
    pad = os.path.join(_documenten(), "CharlesOnderhoud")
    os.makedirs(pad, exist_ok=True)
    return pad


class AppLogger:
    """Schrijft elke regel naar een logbestand en stuurt hem naar de UI."""

    def __init__(self, ui_queue: queue.Queue):
        self.ui_queue = ui_queue
        log_dir = os.path.join(data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"onderhoud_{stamp}.log")

        self._logger = logging.getLogger("onderhoud")
        self._logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
        self._logger.addHandler(handler)

    def log(self, message: str, level: str = "INFO"):
        """Log een bericht. level: INFO, STEP, SUCCESS, WARNING of ERROR."""
        msg = str(message).rstrip()
        if not msg:
            return
        level = level.upper()
        # 'STEP' en 'SUCCESS' zijn geen standaardniveaus -> INFO in het bestand
        file_level = level if level in ("WARNING", "ERROR", "DEBUG") else "INFO"
        self._logger.log(getattr(logging, file_level), f"[{level}] {msg}")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.ui_queue.put(("log", level, stamp, msg))

    # Handige snelkoppelingen
    def info(self, m):    self.log(m, "INFO")
    def step(self, m):    self.log(m, "STEP")
    def success(self, m): self.log(m, "SUCCESS")
    def warning(self, m): self.log(m, "WARNING")
    def error(self, m):   self.log(m, "ERROR")

    def export(self, dest_path: str) -> bool:
        """Kopieer het huidige logbestand naar een gekozen locatie."""
        try:
            for h in self._logger.handlers:
                h.flush()
            shutil.copyfile(self.log_path, dest_path)
            return True
        except OSError:
            return False
