"""Geheimnisse unter Windows mit dem Windows-Datenschutz (DPAPI, Benutzerbereich) ablegen.

Betrifft den Schlüssel, mit dem dieser Worker beim Server angemeldet ist (in den Einstellungen), und den Schlüssel
des Workers von „Alles auf diesem PC“ (schreibt der Server ab 0.4.100 geschützt, wenn das Programm es ihm sagt).
Lesen kann beides nur derselbe Windows-Nutzer auf demselben PC. Text-Form: `dpapi:<Base64>`; Klartext aus älteren
Fassungen wird weiter gelesen und beim nächsten Speichern geschützt. Außerhalb von Windows ändert sich nichts.
"""
from __future__ import annotations

import base64
import logging
import sys

log = logging.getLogger("taleward_worker")
PRAEFIX = "dpapi:"
_ENTROPIE = b"Taleward"


def verfuegbar() -> bool:
    return sys.platform == "win32"


def schuetzen(daten: bytes) -> bytes:
    return _aufruf(daten, schuetzen=True)


def oeffnen(daten: bytes) -> bytes:
    return _aufruf(daten, schuetzen=False)


def _aufruf(daten: bytes, schuetzen: bool) -> bytes:  # pragma: no cover – nur unter Windows
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    puffer = []  # bis zum Ende des Aufrufs festhalten, sonst zeigte der Blob ins Leere

    def blob(b: bytes) -> Blob:
        puffer.append(ctypes.create_string_buffer(b, len(b)))
        return Blob(len(b), ctypes.cast(puffer[-1], ctypes.POINTER(ctypes.c_char)))

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    ein, entropie, aus = blob(daten), blob(_ENTROPIE), Blob()
    funktion = crypt32.CryptProtectData if schuetzen else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN – nie eine Rückfrage anzeigen
    if not funktion(ctypes.byref(ein), None, ctypes.byref(entropie), None, None, 0x1, ctypes.byref(aus)):
        raise OSError(ctypes.GetLastError() or "DPAPI")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(aus.pbData, aus.cbData)
    finally:
        kernel32.LocalFree(aus.pbData)


def text_schuetzen(wert: str) -> str:
    if not verfuegbar() or not wert or wert.startswith(PRAEFIX):
        return wert
    try:
        return PRAEFIX + base64.b64encode(schuetzen(wert.encode("utf-8"))).decode("ascii")
    except (OSError, AttributeError, ValueError):
        # Lieber weiter wie bisher (nur für den Nutzer lesbar) als gar nicht speichern können
        log.warning("Windows-Datenschutz nicht verfügbar – Wert ungeschützt abgelegt")
        return wert


def text_oeffnen(wert: str) -> str:
    """Klartext unverändert; geschützt, aber hier nicht zu öffnen (anderer PC oder Nutzer) → leer."""
    if not wert or not wert.startswith(PRAEFIX):
        return wert
    if not verfuegbar():
        return ""
    try:
        return oeffnen(base64.b64decode(wert[len(PRAEFIX):])).decode("utf-8")
    except (OSError, ValueError):
        log.warning("Ein geschützter Schlüssel lässt sich auf diesem PC nicht öffnen")
        return ""
