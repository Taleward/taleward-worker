"""Sicherung mit Passwort und Hilfe-Datei – für „Alles auf diesem PC“.

Sicherung: Der Server legt sie an und verschlüsselt sie mit dem Passwort (`chronik sicherung-export`, ab Server
0.4.100); das Passwort geht nur über die Standardeingabe, nie in die Befehlszeile oder ins Protokoll. Zurückspielen
geht nur bei angehaltenem Taleward: anhalten, `chronik sicherung-import` (der jetzige Stand wird vorher gesichert),
wieder starten.

Hilfe-Datei: Fassungen, System, Einstellungen und die letzten Zeilen der Protokolle – ohne Schlüssel, Passwörter,
E-Mail-Adressen und den Windows-Benutzernamen. Die Oberfläche zeigt sie vor dem Speichern genau so, wie sie
gespeichert wird.
"""
from __future__ import annotations

import os
import platform
import re
import threading
import time
from pathlib import Path

from taleward_worker import VERSION, motor, pfade

MIN_PASSWORT = 8
ENDUNG = ".taleward"


class Sicherung:
    """Läuft in einem eigenen Faden; die Oberfläche fragt `stand()` ab."""

    def __init__(self, app):
        self.app = app
        self.phase = ""     # "" | speichern | zurueckspielen | fertig | fehler
        self.art = ""       # speichern | zurueckspielen
        self.fehler = ""    # passwort | format | beschaeftigt | unbekannt
        self.datei = ""     # nur der Dateiname, zum Anzeigen
        self.gewaehlt: Path | None = None
        self._sperre = threading.Lock()

    def laeuft(self) -> bool:
        return self.phase in ("speichern", "zurueckspielen")

    def stand(self) -> dict:
        return {"phase": self.phase, "art": self.art, "fehler": self.fehler, "datei": self.datei,
                "gewaehlt": self.gewaehlt.name if self.gewaehlt else ""}

    def _hindernis(self) -> str | None:
        a = self.app
        if self.laeuft() or a.ep_update.laeuft():
            return "beschaeftigt"
        if a.installation and a.installation.phase not in ("fertig", "fehler", "abgebrochen"):
            return "beschaeftigt"
        return None

    # ------------------------------------------------------------------ Speichern
    def speichern(self, ziel: Path, passwort: str) -> dict:
        with self._sperre:
            grund = self._hindernis()
            if grund:
                return {"ok": False, "fehler": grund}
            if not self.app.server.bereit.is_set():
                return {"ok": False, "fehler": "startet_noch"}
            self.phase, self.art, self.fehler, self.datei = "speichern", "speichern", "", ziel.name
        threading.Thread(target=self._speichern, args=(ziel, passwort), daemon=True, name="sicherung").start()
        return {"ok": True}

    def _speichern(self, ziel: Path, passwort: str) -> None:
        try:
            r = self.app.ep_update.cli("sicherung-export", str(ziel), eingabe=passwort + "\n")
            m = re.search(r"Sicherung gespeichert: (.+)$", r.stdout.strip(), re.M)
            if r.returncode != 0 or not m:
                self._protokoll("Sicherung nicht gespeichert: " + (r.stderr or r.stdout).strip()[-300:])
                self.phase, self.fehler = "fehler", "unbekannt"
                return
            self.datei = Path(m.group(1).strip()).name
            self._protokoll(f"Sicherung gespeichert: {self.datei}")
            self.phase = "fertig"
        except Exception as e:  # noqa: BLE001 – die Oberfläche zeigt den Fehler
            self._protokoll(f"Sicherung nicht gespeichert: {type(e).__name__}")
            self.phase, self.fehler = "fehler", "unbekannt"

    # ------------------------------------------------------------------ Zurückspielen
    def zurueckspielen(self, passwort: str) -> dict:
        with self._sperre:
            grund = self._hindernis()
            if grund:
                return {"ok": False, "fehler": grund}
            if not self.gewaehlt or not self.gewaehlt.is_file():
                return {"ok": False, "fehler": "keine_datei"}
            if self.app.dienst.zustand.get("art") == "arbeitet":
                return {"ok": False, "fehler": "beschaeftigt"}
            datei = self.gewaehlt
            self.phase, self.art, self.fehler, self.datei = "zurueckspielen", "zurueckspielen", "", datei.name
        threading.Thread(target=self._zurueckspielen, args=(datei, passwort), daemon=True,
                         name="zurueckspielen").start()
        return {"ok": True}

    def _zurueckspielen(self, datei: Path, passwort: str) -> None:
        a = self.app
        try:
            a.dienst.stoppen()
            a.server.stoppen()
            r = a.ep_update.cli("sicherung-import", str(datei), "--ja", eingabe=passwort + "\n")
            if r.returncode == 2:
                self.phase, self.fehler = "fehler", "passwort"
            elif r.returncode != 0:
                self._protokoll("Sicherung nicht zurückgespielt: " + (r.stderr or r.stdout).strip()[-300:])
                self.phase, self.fehler = "fehler", "format"
            else:
                self._protokoll(f"Sicherung zurückgespielt: {datei.name}")
                if not a.e.konto_angelegt:  # neuer PC: das Konto kommt aus der Sicherung
                    a.e.konto_angelegt, a.e.anmeldename = True, ""
                    a.e.speichern()
                self.gewaehlt = None
                self.phase = "fertig"
        except Exception as e:  # noqa: BLE001
            self._protokoll(f"Sicherung nicht zurückgespielt: {type(e).__name__}")
            self.phase, self.fehler = "fehler", "unbekannt"
        finally:
            a.taleward_starten()

    def _protokoll(self, text: str) -> None:
        try:
            self.app.dienst.protokoll.schreiben(text)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------- Hilfe-Datei
_MUSTER = [
    (re.compile(r"\bwk\.[A-Za-z0-9_.\-]+"), "wk.[entfernt]"),
    (re.compile(r"\bdpapi:[A-Za-z0-9+/=]+"), "dpapi:[entfernt]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_.\-~+/=]+"), "Bearer [entfernt]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{8,}"), "hf_[entfernt]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}"), "[Schlüssel entfernt]"),
    (re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b"), "[Code entfernt]"),
    (re.compile(r"(?i)\b(token|passwor[dt]|password|secret|api[_-]?key|schl(?:ü|ue)ssel|key)(\"?\s*[:=]\s*\"?)"
                r"[^\s\"',;&\[]+"), r"\1\2[entfernt]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[E-Mail entfernt]"),
    (re.compile(r"([?&](?:token|key|code|sig|signature|access_token)=)[^&\s]+", re.I), r"\1[entfernt]"),
]


def schwaerzen(text: str) -> str:
    """Schlüssel, Passwörter, Codes, E-Mail-Adressen und den Benutzerordner unkenntlich machen."""
    for muster, ersatz in _MUSTER:
        text = muster.sub(ersatz, text)
    heim = str(Path.home())
    if len(heim) > 3:
        text = text.replace(heim, "%USERPROFILE%").replace(heim.replace("\\", "/"), "%USERPROFILE%")
    try:
        nutzer = os.getlogin()
    except OSError:
        nutzer = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(nutzer) >= 3:
        text = re.sub(re.escape(nutzer), "[Benutzer]", text, flags=re.I)
    return text


def _letzte_zeilen(datei: Path, anzahl: int) -> list[str]:
    try:
        with datei.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            return f.read().decode("utf-8", errors="replace").splitlines()[-anzahl:]
    except OSError:
        return []


def hilfe_text(app) -> str:
    e = app.e
    inst = motor.installiert() or {}
    hw = app.hardware or {}
    karte = hw.get("grafikkarte") or {}
    zeilen = [
        "Taleward – Hilfe-Datei",
        f"Erstellt: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "== Fassungen",
        f"Programm: {VERSION}",
        f"KI-Paket: {inst.get('fassung') or '–'} ({'Testmodus' if inst.get('testmodus') else inst.get('backend') or '–'})",
        f"Server: {app.server_version or '–'}",
        f"System: {platform.system()} {platform.release()} ({platform.version()}), {platform.machine()}",
        f"Grafikkarte: {karte.get('name') or '–'} ({karte.get('vram_mb') or '–'} MB, Treiber {karte.get('treiber') or '–'})",
        f"Arbeitsspeicher: {hw.get('ramGb') or '–'} GB · frei auf dem Datenträger: {hw.get('platzGb') or '–'} GB",
        "",
        "== Einstellungen",
        f"Betriebsart: {e.art or '–'} · Gekoppelt: {'ja' if e.gekoppelt else 'nein'}",
        f"Testmodus: {e.testmodus} · Gerät: {e.geraet} · Modell: {e.modell} · Grafikspeicher-Grenze: {e.vram_grenze_mb}",
        f"Recaps auf diesem PC: {e.recaps_lokal} · Pausiert: {e.pausiert} · Automatische Updates: {e.auto_update}",
        f"Zustand des Workers: {app.dienst.zustand.get('art')} {app.dienst.zustand.get('code') or ''}".rstrip(),
    ]
    if e.einzelplatz:
        z = app.server.zustand
        zeilen.append(f"Taleward auf diesem PC: {z.get('art')} {z.get('code') or ''}".rstrip())
        zeilen.append(f"Update: {app.ep_update.phase or '–'} {app.ep_update.fehler or ''}".rstrip())
        if app.netz and app.netz.get("noetig"):
            zeilen.append(f"Netz: Freigabe {'ja' if app.netz.get('regel') else 'nein'}, "
                          f"Netzprofil {', '.join(app.netz.get('profile') or []) or '–'}")
    if app.installation and app.installation.phase != "fertig":
        zeilen += ["", "== Installation", *app.installation.zeilen[-120:]]
    zeilen += ["", "== Protokoll des Workers", *_letzte_zeilen(pfade.protokolle() / "worker.log", 300)]
    if e.einzelplatz:
        zeilen += ["", "== Protokoll von Taleward", *_letzte_zeilen(pfade.protokolle() / "server.log", 300)]
    return schwaerzen("\n".join(zeilen)) + "\n"
