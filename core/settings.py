"""Kleine persistente instellingen (config.json in de datamap)."""
import json
import os
import shutil

from core.logger import app_dir, data_dir

_PAD = os.path.join(data_dir(), "config.json")
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        # eenmalige migratie: oude config naast de exe meenemen naar de datamap
        oud = os.path.join(app_dir(), "config.json")
        if not os.path.exists(_PAD) and os.path.exists(oud):
            try:
                shutil.copyfile(oud, _PAD)
            except OSError:
                pass
        try:
            with open(_PAD, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def get(sleutel: str, standaard=None):
    """Lees een instelling; geeft standaard terug als die niet bestaat."""
    return _load().get(sleutel, standaard)


def set(sleutel: str, waarde) -> None:
    """Sla een instelling op (meteen weggeschreven naar config.json)."""
    data = _load()
    data[sleutel] = waarde
    with open(_PAD, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
