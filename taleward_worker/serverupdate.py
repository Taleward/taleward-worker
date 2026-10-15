"""Updates für „Alles auf diesem PC“: neue Server-Fassung mit Sicherung vorher und Rückweg (Sicherheit).

Es gibt keinen anderen Server, der die Fassung vorgibt – das Programm sucht selbst die neueste freigegebene
Server-Fassung bei GitHub (`motor.neueste_fassung`, nie zurück unter die laufende). Ablauf, nur wenn gerade nichts
läuft (der eigene Worker arbeitet nicht, und der Server meldet über `GET /worker/v1/busy` weder Auftrag noch
Hochladen noch Verarbeitung):

1. Paketliste des laufenden KI-Pakets festhalten; neues KI-Paket vorbereiten – Taleward läuft dabei weiter.
2. Taleward anhalten und die Daten mit der **alten** Fassung sichern (`chronik sicherung`). Ohne Sicherung kein Tausch.
3. KI-Paket tauschen, Taleward mit der neuen Fassung starten und prüfen: antwortet, meldet die neue Fassung, läuft
   danach noch STABIL_S Sekunden ohne Absturz.
4. Klappt das nicht: KI-Paket zurück (großes Update: der behaltene alte Ordner, sonst die Paketliste), Sicherung mit
   der alten Fassung zurückspielen, alte Fassung starten. Die gescheiterte Fassung versucht das Programm nicht wieder
   von selbst – erst eine neuere oder „Jetzt aktualisieren“. Das gilt auch, wenn der Rückweg selbst scheitert.

Unmittelbar vor dem Anhalten wird die Ruhe noch einmal geprüft (das Vorbereiten kann dauern); kam Arbeit dazu,
bleibt Taleward unverändert und es geht später weiter.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

import httpx

from taleward_worker import VERSION, einzelplatz, motor, pfade, server
from taleward_worker.selbstupdate import neuer

PRUEFEN_ALLE_S = 6 * 3600
ERSTE_PRUEFUNG_NACH_S = 600     # nicht gleich beim Start – erst soll Taleward in Ruhe laufen
STABIL_S = 20
BESCHAEFTIGT_WARTEN_S = 1800    # kam beim Anhalten doch noch Arbeit dazu: in einer halben Stunde erneut
LAEUFT = ("vorbereiten", "sichern", "tauschen", "starten", "pruefen", "zurueck")


class ServerUpdate:
    def __init__(self, app):
        self.app = app                  # WorkerApp: e, server, dienst, installation
        self.angebot: str | None = None  # neuere freigegebene Fassung
        self.phase = ""                 # "" | LAEUFT … | fertig | zurueckgenommen | fehler | verschoben
        self.fehler = ""
        self.alt = ""
        self.neu = ""
        self.inst: motor.Installation | None = None
        self._naechste = time.monotonic() + ERSTE_PRUEFUNG_NACH_S
        self._wieder_ab = 0.0           # nach einem Fehler (z. B. Download) erst später wieder von selbst
        self._faden: threading.Thread | None = None
        self._sperre = threading.Lock()

    # ------------------------------------------------------------------ Abfragen
    def laeuft(self) -> bool:
        return self.phase in LAEUFT

    def installiert(self) -> str:
        inst = motor.installiert() or {}
        return str(inst.get("fassung") or self.app.e.motor_fassung or "")

    def pruefen(self) -> str | None:
        """Gibt es eine neuere freigegebene Fassung? Fehler (kein Internet) lassen das letzte Ergebnis stehen."""
        self._naechste = time.monotonic() + PRUEFEN_ALLE_S
        alt = self.installiert()
        if not alt:
            return None
        try:
            neu = motor.neueste_fassung(mindestens=alt)
        except motor.MotorFehler as e:
            self._protokoll(f"Update-Prüfung nicht möglich ({e.code}) – später noch einmal")
            return self.angebot
        self.angebot = neu if neuer(neu, alt) else None
        return self.angebot

    def beschaeftigt(self) -> str | None:
        """Grund, warum jetzt kein Update geht – oder None."""
        a = self.app
        if a.dienst.zustand.get("art") == "arbeitet":
            return "job"
        if a.installation and a.installation.phase not in ("fertig", "fehler", "abgebrochen"):
            return "installation"
        if not a.server.bereit.is_set():
            return "nicht_bereit"
        token = a.server.worker_token()
        try:
            with httpx.Client(timeout=10, headers={"User-Agent": f"TalewardWorker/{VERSION}"}) as k:
                r = k.get(f"{einzelplatz.ADRESSE}/worker/v1/busy", headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            return "nicht_bereit"
        if r.status_code == 404:
            return None  # Server vor 0.4.97 kennt die Frage nicht – dann zählt nur der eigene Worker
        if r.status_code != 200:
            return "nicht_bereit"
        try:
            d = r.json()
        except ValueError:
            return "nicht_bereit"
        return (d.get("reason") or "busy") if d.get("busy") else None

    # ------------------------------------------------------------------ Auslösen
    def wenn_faellig(self) -> None:
        """Aus der Hintergrundpflege (alle 5 Minuten): selten nachsehen, bei Ruhe von selbst aktualisieren."""
        e = self.app.e
        if not e.konto_angelegt or self.laeuft():
            return
        if time.monotonic() >= self._naechste:
            self.pruefen()
        if (self.angebot and e.auto_update and self.angebot != e.update_gescheitert
                and time.monotonic() >= self._wieder_ab and self.beschaeftigt() is None):
            self.starten(self.angebot)

    def jetzt(self) -> dict:
        """„Jetzt aktualisieren“ in der Oberfläche – auch für eine einmal gescheiterte Fassung."""
        if self.laeuft():
            return {"ok": True}
        if not self.angebot:
            self.pruefen()
        if not self.angebot:
            return {"ok": False, "grund": "aktuell"}
        grund = self.beschaeftigt()
        if grund:
            return {"ok": False, "grund": grund}
        self.starten(self.angebot)
        return {"ok": True}

    def starten(self, neu: str) -> None:
        with self._sperre:
            if self.laeuft():
                return
            self.alt, self.neu, self.fehler = self.installiert(), neu, ""
            self.phase = "vorbereiten"
            self._faden = threading.Thread(target=self._lauf, daemon=True, name="server-update")
            self._faden.start()

    # ------------------------------------------------------------------ Ablauf
    def _lauf(self) -> None:
        try:
            self._ablauf()
        finally:
            if self.phase == "fehler":
                self._wieder_ab = time.monotonic() + PRUEFEN_ALLE_S
            elif self.phase == "verschoben":
                self._wieder_ab = time.monotonic() + BESCHAEFTIGT_WARTEN_S

    def _ablauf(self) -> None:
        a = self.app
        vorher = pfade.basis() / "update" / "vorher.txt"
        sicherung: dict = {}
        angehalten = {"ja": False}
        self._protokoll(f"Update von Taleward {self.alt} auf {self.neu} beginnt")
        backend = "cpu" if a.e.geraet == "cpu" else "cuda"
        inst = motor.Installation(self.neu, testmodus=a.e.testmodus, backend=backend, alt_behalten=True)
        self.inst = inst

        def vor():  # Taleward anhalten und mit der alten Fassung sichern – ohne Sicherung kein Tausch
            grund = self.beschaeftigt()  # das Vorbereiten kann dauern – inzwischen kann Arbeit dazugekommen sein
            if grund:
                raise motor.MotorFehler("beschaeftigt", f"Taleward ist beschäftigt ({grund})")
            self.phase = "sichern"
            angehalten["ja"] = True
            a.dienst.stoppen()
            a.server.stoppen()
            sicherung["datei"] = self.sicherung_anlegen()
            self.phase = "tauschen"
        inst.vor_tausch = vor
        try:
            inst.stand_festhalten(vorher)
            inst.starten().join()
            if inst.phase != "fertig":
                return self._nicht_installiert(inst, vorher, angehalten["ja"], "datei" in sicherung)
            self.phase = "starten"
            a.server.starten()
            if self._neue_fassung_laeuft():
                inst.alt_entfernen()
                a.e.motor_fassung, a.e.update_gescheitert = self.neu, ""
                a.e.speichern()
                self.phase, self.angebot = "fertig", None
                self._protokoll(f"Taleward läuft jetzt mit Fassung {self.neu}")
                return
            self._zurueck(inst, vorher, sicherung.get("datei"))
        except Exception as e:  # noqa: BLE001 – nie mit angehaltenem Taleward stehen bleiben
            self.phase, self.fehler = "fehler", f"{type(e).__name__}: {e}"[:300]
            self._protokoll(f"Update: unerwarteter Fehler – {self.fehler}")
            if not a.server.laeuft():
                a.server.starten()

    def _nicht_installiert(self, inst: motor.Installation, vorher: Path, angehalten: bool, gesichert: bool) -> None:
        a = self.app
        self.fehler = inst.fehler or "installation"
        if not angehalten:
            # Download oder Vorbereitung gescheitert bzw. gerade beschäftigt: Taleward lief durch, nichts getauscht
            self.phase = "fehler"
            if inst.fehler == "beschaeftigt":
                self.phase, self.fehler = "verschoben", "beschaeftigt"
                self._protokoll(f"Update auf {self.neu} verschoben: {getattr(inst, 'fehlertext', '')} – später noch einmal")
            else:
                self._protokoll(f"Update auf {self.neu} vorbereitet nicht: {inst.fehler} – Taleward läuft unverändert")
            return
        if gesichert:
            # Tausch mittendrin gescheitert – der neue Code lief nie, die Daten sind unberührt
            self.phase = "zurueck"
            try:
                inst.zurueck(vorher, self.alt)
            except motor.MotorFehler as e:
                self._protokoll(f"Rückweg des KI-Pakets gescheitert: {e.code} {e.text[-300:]}")
            finally:
                self._gescheitert()
        else:
            self.phase = "fehler"
            self._protokoll("Sicherung vor dem Update gescheitert – kein Update, Taleward startet wieder")
        a.server.starten()

    def _neue_fassung_laeuft(self) -> bool:
        s = self.app.server
        if not s.warten_bis_bereit(einzelplatz.START_FRIST_S):
            self._protokoll(f"Fassung {self.neu} startet nicht")
            return False
        self.phase = "pruefen"
        p = s._prozess
        try:
            fassung = server.konfiguration(einzelplatz.ADRESSE, s.worker_token() or "").get("serverVersion")
        except server.ServerFehler as e:
            self._protokoll(f"Fassung {self.neu} antwortet nicht richtig ({e.code})")
            return False
        if fassung != self.neu:
            self._protokoll(f"Gestartet ist Fassung {fassung}, erwartet {self.neu}")
            return False
        ende = time.monotonic() + STABIL_S
        while time.monotonic() < ende:
            if s._prozess is not p or p is None or p.poll() is not None:
                self._protokoll(f"Fassung {self.neu} ist nach dem Start abgestürzt")
                return False
            time.sleep(0.5)
        return True

    def _zurueck(self, inst: motor.Installation, vorher: Path, datei: Path | None) -> None:
        a = self.app
        self.phase = "zurueck"
        self._protokoll(f"Update auf {self.neu} wird zurückgenommen – zurück auf {self.alt}")
        try:
            a.dienst.stoppen()
            a.server.stoppen()
            inst.zurueck(vorher, self.alt)
            if datei is not None:
                self.sicherung_zurueckspielen(datei)
        finally:
            self._gescheitert()  # auch wenn der Rückweg scheitert: diese Fassung nicht von selbst wieder versuchen
        a.server.starten()

    def _gescheitert(self) -> None:
        e = self.app.e
        e.update_gescheitert = self.neu
        e.speichern()
        self.phase = "zurueckgenommen"
        self._protokoll(f"Taleward läuft weiter mit Fassung {self.alt}; {self.neu} wird nicht von selbst wieder versucht")

    # ------------------------------------------------------------------ Sicherung (mit der jeweils installierten Fassung)
    def cli(self, *argumente: str, eingabe: str | None = None) -> subprocess.CompletedProcess:
        """`eingabe` (z. B. ein Passwort) geht über die Standardeingabe, nie in die Befehlszeile."""
        py = os.environ.get("TALEWARD_MOTOR") or str(pfade.motor_python())
        umgebung = self.app.server.umgebung()
        umgebung.pop("TALEWARD_STARTSCHLUESSEL_DATEI", None)
        return subprocess.run([py, "-m", "app.cli", *argumente], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=1800, env=umgebung, cwd=pfade.basis(),
                              input=eingabe if eingabe is not None else "",
                              **({"creationflags": 0x08000000} if os.name == "nt" else {}))

    def sicherung_anlegen(self) -> Path:
        r = self.cli("sicherung")
        m = re.search(r"Sicherung angelegt: (.+)$", r.stdout.strip(), re.M)
        datei = Path(m.group(1).strip()) if m else None
        if r.returncode != 0 or datei is None or not datei.is_file():
            raise motor.MotorFehler("sicherung", (r.stderr or r.stdout)[-500:])
        self._protokoll(f"Sicherung vor dem Update: {datei.name}")
        return datei

    def sicherung_zurueckspielen(self, datei: Path) -> None:
        r = self.cli("wiederherstellen", str(datei), "--ja")
        if r.returncode != 0:
            raise motor.MotorFehler("sicherung", (r.stderr or r.stdout)[-500:])
        self._protokoll(f"Daten aus der Sicherung {datei.name} zurückgespielt")

    # ------------------------------------------------------------------ Anzeige
    def _protokoll(self, text: str) -> None:
        try:
            self.app.dienst.protokoll.schreiben(text)
        except Exception:  # noqa: BLE001 – Protokoll ist Beiwerk
            pass

    def fuer_oberflaeche(self) -> dict:
        inst = self.inst
        return {"phase": self.phase, "angebot": self.angebot, "alt": self.alt, "neu": self.neu,
                "gescheitert": self.app.e.update_gescheitert or None,
                "anteil": round(inst.anteil, 3) if inst and self.phase == "vorbereiten" else None}
