"""„Passwort vergessen“ bei „Alles auf diesem PC“: Wer hier sitzt, ist der Verwalter – einen Link per E-Mail gibt es
nicht. Damit nicht jeder am entsperrten PC das Passwort neu setzen kann, bestätigt Windows Hello vorher (PIN,
Fingerabdruck oder Gesicht des Windows-Kontos). Ohne eingerichtetes Windows Hello wird nichts zurückgesetzt.

Das neue Passwort geht über die Standardeingabe an den Server (`passwort-neu`, ab Server 0.4.118), nie in die
Befehlszeile oder ins Protokoll.
"""
from __future__ import annotations

import asyncio
import logging
import sys

log = logging.getLogger("passwort")

MIN_PASSWORT = 8
MINDEST_SERVER = (0, 4, 118)


def windows_bestaetigen(text: str) -> str:
    """Windows Hello fragen. Ergebnis: "ok", "abgebrochen", "abgelehnt", "nicht_eingerichtet" oder "fehler"."""
    if sys.platform != "win32":
        return "nicht_eingerichtet"
    try:
        import winrt.windows.foundation  # noqa: F401 – Projektion für IAsyncOperation (await)
        from winrt.windows.security.credentials.ui import (
            UserConsentVerificationResult as Ergebnis,
            UserConsentVerifier,
            UserConsentVerifierAvailability as Verfuegbar,
        )
    except Exception:  # noqa: BLE001 – ohne die Windows-Bausteine gibt es keine Bestätigung
        log.exception("Windows Hello nicht ladbar")
        return "fehler"

    async def ablauf() -> str:
        verfuegbar = await UserConsentVerifier.check_availability_async()
        if verfuegbar != Verfuegbar.AVAILABLE:
            return "nicht_eingerichtet"
        r = await UserConsentVerifier.request_verification_async(text)
        if r == Ergebnis.VERIFIED:
            return "ok"
        if r == Ergebnis.CANCELED:
            return "abgebrochen"
        if r in (Ergebnis.DEVICE_NOT_PRESENT, Ergebnis.NOT_CONFIGURED_FOR_USER, Ergebnis.DISABLED_BY_POLICY):
            return "nicht_eingerichtet"
        return "abgelehnt"

    try:
        return asyncio.run(ablauf())
    except Exception:  # noqa: BLE001 – im Zweifel nichts zurücksetzen
        log.exception("Windows Hello hat nicht geantwortet")
        return "fehler"


def fassung(text: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(text or "").split(".")[:3])
    except ValueError:
        return ()


def konten(cli) -> dict:
    """Verwalter-Konten dieses PCs. `cli` ist ServerUpdate.cli (Motor mit der Umgebung des Servers)."""
    r = cli("verwalter")
    if r.returncode != 0:
        return {"ok": False, "fehler": "zu_alt" if "No such command" in (r.stderr or "") else "unbekannt"}
    namen = [z.strip() for z in r.stdout.splitlines() if z.strip()]
    return {"ok": True, "konten": namen}


def hello_text(name: str, sprache: str) -> str:
    if str(sprache or "").startswith("en"):
        return f"Taleward: set a new password for “{name}”"
    return f"Taleward: neues Passwort für „{name}“ setzen"


def neu_setzen(cli, name: str, passwort: str, sprache: str = "de", bestaetigen=windows_bestaetigen) -> dict:
    """Erst Windows Hello, dann das neue Passwort über die Standardeingabe."""
    if bestaetigen is None:
        return {"ok": False, "fehler": "unbekannt"}
    k = konten(cli)
    if not k["ok"]:
        return k
    if name not in k["konten"]:
        return {"ok": False, "fehler": "konto"}
    ergebnis = bestaetigen(hello_text(name, sprache))
    if ergebnis != "ok":
        return {"ok": False, "fehler": "hello_" + ergebnis}
    r = cli("passwort-neu", "-u", name, eingabe=passwort + "\n")
    if r.returncode == 2:
        return {"ok": False, "fehler": "password_too_short"}
    if r.returncode != 0:
        return {"ok": False, "fehler": "unbekannt"}
    return {"ok": True}
