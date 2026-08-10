"""Versleuteling van gevoelige waarden via Windows DPAPI (CryptProtectData).

DPAPI versleutelt per Windows-gebruiker: de blob is alleen door dezelfde
gebruiker op dezelfde pc te ontsleutelen — ideaal voor wachtwoorden in
config.json. Geen externe dependencies (ctypes op crypt32.dll).

Opgeslagen waarden krijgen het prefix 'dpapi:' + base64. Waarden zonder dat
prefix worden als onversleutelde legacy-tekst behandeld, zodat bestaande
config blijft werken.
"""
import base64
import ctypes
import ctypes.wintypes as wt

_PREFIX = "dpapi:"
_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.c_void_p)]


def _maak_blob(data: bytes):
    """DATA_BLOB + bijbehorende buffer (buffer moet in scope blijven)."""
    buf = ctypes.create_string_buffer(data, max(len(data), 1))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p)), buf


def _lees_uit(blob: _BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _kernel32.LocalFree(ctypes.c_void_p(blob.pbData))


def versleutel(tekst: str) -> str:
    """Versleutel tekst met DPAPI; geeft 'dpapi:<base64>' (of '' bij leeg)."""
    if not tekst:
        return ""
    invoer, _b1 = _maak_blob(tekst.encode("utf-8"))
    uit = _BLOB()
    if not _crypt32.CryptProtectData(
            ctypes.byref(invoer), None, None, None, None, 0, ctypes.byref(uit)):
        raise OSError("CryptProtectData is mislukt")
    return _PREFIX + base64.b64encode(_lees_uit(uit)).decode("ascii")


def ontsleutel(opgeslagen: str) -> str:
    """Ontsleutel een versleutel()-waarde; legacy-tekst gaat ongemoeid terug."""
    if not opgeslagen or not opgeslagen.startswith(_PREFIX):
        return opgeslagen or ""
    try:
        raw = base64.b64decode(opgeslagen[len(_PREFIX):])
        invoer, _b1 = _maak_blob(raw)
        uit = _BLOB()
        if not _crypt32.CryptUnprotectData(
                ctypes.byref(invoer), None, None, None, None, 0,
                ctypes.byref(uit)):
            raise OSError("CryptUnprotectData is mislukt")
        return _lees_uit(uit).decode("utf-8")
    except Exception:
        return ""  # corrupt of door andere gebruiker/pc versleuteld
