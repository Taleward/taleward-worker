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
_V = r"(?<![A-Za-z0-9])"  # statt \b: auch nach „_“ (z. B. „X_hf_…“) greifen
_NAME = (r"[A-Za-z0-9_.\-]*?(?:token|passwor[dt]|password|passwd|passphrase|pwd|pass|secret|api[_-]?key|schl(?:ü|ue)ssel"
         r"|key|credentials?|cookie|session(?:[_-]?id)?|sid|auth)")  # endet auf ein Schlüsselwort: MISTRAL_API_KEY,
# client_secret, access_token, SMTP_PASS, session_id, private_key
_MUSTER = [
    # Private Schlüssel als PEM-Block, auch über mehrere Zeilen
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
     "[Schlüssel entfernt]"),
    # Zugangsdaten in Adressen bis zum letzten @ vor dem Host (auch ohne Punkt im Host: http://u:pw@localhost:8000)
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s?#]*@"), r"\1[Zugang entfernt]@"),
    # Cookies: die ganze Zeile nach „Cookie:“ bzw. „Set-Cookie:“, und alles nach „Authorization:“
    (re.compile(r"(?i)((?:set-)?cookie\s*:\s*)[^\n]*"), r"\1[entfernt]"),
    (re.compile(r"(?i)((?:proxy-)?authorization\s*[:=]\s*)[^\n]*"), r"\1[entfernt]"),
    # Schlüssel mit bekannter Vorsilbe (GitHub, Slack, AWS)
    (re.compile(_V + r"(?:gh[pousr]_|github_pat_|xox[abpr]-)[A-Za-z0-9_\-]{8,}"), "[Schlüssel entfernt]"),
    (re.compile(_V + r"AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"), "[Schlüssel entfernt]"),
    (re.compile(_V + r"wk\.[A-Za-z0-9_.\-]+"), "wk.[entfernt]"),
    (re.compile(_V + r"dpapi:[A-Za-z0-9+/=]+"), "dpapi:[entfernt]"),
    (re.compile(r"(?i)" + _V + r"(bearer|basic|digest|negotiate|ntlm)\s+[A-Za-z0-9_.\-~+/=]+"), r"\1 [entfernt]"),
    # „Token abc…“ ohne Kopfzeile davor – nur Werte, die nach Schlüssel aussehen (ab 8 Zeichen, mit Ziffer)
    (re.compile(r"(?i)" + _V + r"(token)\s+(?=[A-Za-z0-9_.\-~+/=]*\d)[A-Za-z0-9_.\-~+/=]{8,}"), r"\1 [entfernt]"),
    (re.compile(_V + r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]*)?"), "[JWT entfernt]"),
    (re.compile(_V + r"hf_[A-Za-z0-9]{8,}"), "hf_[entfernt]"),
    (re.compile(_V + r"(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}"), "[Schlüssel entfernt]"),
    (re.compile(_V + r"[A-Z0-9]{4}-[A-Z0-9]{4}(?![A-Za-z0-9])"), "[Code entfernt]"),
    # Name = Wert bzw. Name: Wert – Wert in "…", '…' (auch mit Leerzeichen) oder bis zum nächsten Trenner
    (re.compile(r"(?i)" + _V + r"(" + _NAME + r")(?![A-Za-z0-9])([\"']?\s*[:=]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s\"',;&\[]+)"),
     r"\1\2[entfernt]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[E-Mail entfernt]"),
    (re.compile(r"(?i)([?&#;](?:[A-Za-z0-9_\-]*?(?:token|key|code|sig|signature|secret|passwor[dt]|password|pass|auth)))"
                r"=[^&\s#]+"), r"\1=[entfernt]"),
]
# Auffangmuster: lange zufällige Zeichenketten (≥ 32 Zeichen mit Ziffern und Buchstaben) – Schlüssel ohne Vorsilbe
_ZUFALL = re.compile(r"(?<![A-Za-z0-9_\-+/=])(?=[A-Za-z0-9_\-+/=]*[0-9])(?=[A-Za-z0-9_\-+/=]*[A-Za-z])"
                     r"[A-Za-z0-9_\-+/=]{32,}(?![A-Za-z0-9_\-+/=])")


def schwaerzen(text: str) -> str:
    """Schlüssel, Passwörter, Codes, E-Mail-Adressen und den Benutzerordner unkenntlich machen."""
    for muster, ersatz in _MUSTER:
        text = muster.sub(ersatz, text)
    text = _ZUFALL.sub("[entfernt]", text)
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
        f"Betriebsart: {e.art or '–'} · Gekoppelt: {'ja' if e.gekoppelt else 'nein'}"
        f" · Server-Verbindung beiseitegelegt: {'ja' if e.verbunden_server and e.verbunden_token else 'nein'}",
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
