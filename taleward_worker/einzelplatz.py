"""„Alles auf diesem PC“: Das Programm startet den Taleward-Server aus dem KI-Paket und überwacht ihn.

- Gestartet wird `python -m app.cli einzelplatz` (Betriebsart Einzelplatz des Servers, ab Server 0.4.96): im Heimnetz
  erreichbar, Verwaltung und Worker-Anschluss nur auf diesem PC.
- Das erste Konto legt das Programm über einen einmaligen Startschlüssel an (Datei, nur für den Windows-Nutzer;
  der Server liest und löscht sie beim Start). Nie in der Befehlszeile, nie im Protokoll.
- Den Schlüssel für den Worker schreibt der Server bei jedem Start in eine Datei; das Programm koppelt seinen Worker
  damit – ohne Kopplungscode.
- Beenden: Stoppdatei (der Server fährt geordnet herunter), notfalls hart.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
from collections import deque

import httpx

from taleward_worker import VERSION, geschuetzt, motor, pfade, prozesse

MINDEST_SERVER = "0.4.104"  # ab dieser Server-Fassung: Datenbank-Migrationen im KI-Paket, Schlüssel geschützt
PORT = 8000
ADRESSE = f"http://127.0.0.1:{PORT}"
APP_ADRESSE = f"http://localhost:{PORT}/app/"
WARTEZEITEN = [3, 10, 30, 120]
START_FRIST_S = 180          # so lange darf der erste Start dauern (Datenbank einrichten)
APP_FRIST_S = 900            # so lange darf das erste Laden der App dauern, bevor die Oberfläche einen Hinweis zeigt
STOPP_FRIST_S = 20
MIN_PASSWORT = 8


class ServerDienst:
    """Startet den Server, startet ihn nach einem Absturz neu und meldet seinen Zustand."""

    def __init__(self, befehl: list[str] | None = None):
        self._befehl = befehl  # für Tests
        self._prozess: subprocess.Popen | None = None
        self._sperre = threading.RLock()
        self._soll_laufen = False
        self._versuche = 0
        self._neustart_um: float | None = None
        self._schluessel: str | None = None
        self._mit_schluessel = False
        self.bei_bereit = None  # Aufruf nach jedem erfolgreichen Start (Worker mit frischem Schlüssel koppeln)
        self.zustand: dict = {"art": "gestoppt"}
        self.zeilen: deque[str] = deque(maxlen=400)
        self.bereit = threading.Event()
        self.app_bereit = threading.Event()  # die Web-App liegt vor (lädt der Server beim ersten Start von GitHub)
        self.app_fehlt = False
        self._waechter: threading.Thread | None = None

    # ------------------------------------------------------------------ Steuerung
    def starten(self, startschluessel: bool = False) -> None:
        """`startschluessel`: noch kein Konto – dem Server einen frischen einmaligen Schlüssel mitgeben."""
        with self._sperre:
            self._soll_laufen = True
            self._versuche = 0
            self._neustart_um = None
            self._mit_schluessel = startschluessel
            if self._waechter is None:
                self._waechter = threading.Thread(target=self._wachen, daemon=True, name="server-waechter")
                self._waechter.start()
            if not self.laeuft():
                self._prozess_starten()

    def stoppen(self, warten: float = STOPP_FRIST_S) -> None:
        with self._sperre:
            self._soll_laufen = False
            self._neustart_um = None
            p = self._prozess
        self.bereit.clear()
        self.app_bereit.clear()
        if p and p.poll() is None:
            try:
                pfade.server_stopp().write_text("stopp", encoding="utf-8")
            except OSError:
                pass
            try:
                p.wait(warten)
            except subprocess.TimeoutExpired:
                pass
            prozesse.beenden(p, warten=5, sanft=True)
        pfade.server_stopp().unlink(missing_ok=True)
        self._setzen("gestoppt")

    def laeuft(self) -> bool:
        return self._prozess is not None and self._prozess.poll() is None

    def warten_bis_bereit(self, frist: float = START_FRIST_S) -> bool:
        return self.bereit.wait(frist)

    # ------------------------------------------------------------------ Prozess
    def befehl(self) -> list[str]:
        if self._befehl:
            return list(self._befehl)
        py = os.environ.get("TALEWARD_MOTOR") or str(pfade.motor_python())
        return [py, "-m", "app.cli", "einzelplatz", "--port", str(PORT)]

    def umgebung(self) -> dict:
        env = motor.saubere_umgebung()
        env.update({
            "TALEWARD_EINZELPLATZ": "1", "EINZELPLATZ_PORT": str(PORT),
            "DATA_DIR": str(pfade.einzelplatz_daten()),
            "EINZELPLATZ_WORKER_DATEI": str(pfade.worker_schluessel()),
            "EINZELPLATZ_STOPP_DATEI": str(pfade.server_stopp()),
            "EINZELPLATZ_WORKER_GESCHUETZT": "true",  # Schlüssel mit dem Windows-Datenschutz (Server ab 0.4.100)
            "LOCAL_WORKER_AUTOSTART": "false",
            "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
            "PATH": str(pfade.werkzeuge()) + os.pathsep + env.get("PATH", ""),  # ffmpeg für die Längenmessung
        })
        # Nichts von außen hereinlassen, was die Betriebsart umstellt
        for name in ("DATABASE_URL", "PUBLIC_URL", "FORWARDED_ALLOW_IPS", "CORS_ORIGINS", "TALEWARD_STARTSCHLUESSEL",
                     "TALEWARD_STARTSCHLUESSEL_DATEI", "VERWALTUNG_NUR_AUS", "EINRICHTUNG_IM_HEIMNETZ"):
            env.pop(name, None)
        if self._schluessel:
            env["TALEWARD_STARTSCHLUESSEL_DATEI"] = str(pfade.startschluessel())
        return env

    def _schluessel_vorbereiten(self) -> None:
        pfade.startschluessel().unlink(missing_ok=True)
        self._schluessel = None
        if not self._mit_schluessel:
            return
        self._schluessel = secrets.token_urlsafe(32)
        datei = pfade.startschluessel()
        fd = os.open(datei, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # %LOCALAPPDATA% gehört nur dem Nutzer
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self._schluessel)

    def _prozess_starten(self) -> None:
        if not self._befehl and not os.environ.get("TALEWARD_MOTOR") and not pfade.motor_python().exists():
            self._setzen("fehler", code="motor_fehlt")
            self._soll_laufen = False
            return
        self.bereit.clear()
        pfade.server_stopp().unlink(missing_ok=True)
        pfade.worker_schluessel().unlink(missing_ok=True)  # der Server schreibt einen frischen
        self._schluessel_vorbereiten()
        self._setzen("startet")
        self._schreiben("Taleward startet …")
        try:
            self._prozess = subprocess.Popen(self.befehl(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                             bufsize=1, env=self.umgebung(), cwd=pfade.basis(),
                                             **prozesse.start_argumente())
        except OSError as e:
            self._setzen("fehler", code="start_fehlgeschlagen", text=str(e))
            self._soll_laufen = False
            return
        prozesse.zuordnen(self._prozess)
        p = self._prozess
        threading.Thread(target=self._lesen, args=(p,), daemon=True, name="server-ausgabe").start()
        threading.Thread(target=self._bereit_pruefen, args=(p,), daemon=True, name="server-bereit").start()

    def _bereit_pruefen(self, p: subprocess.Popen) -> None:
        ende = time.monotonic() + START_FRIST_S
        with httpx.Client(timeout=3, headers={"User-Agent": f"TalewardWorker/{VERSION}"}) as k:
            while time.monotonic() < ende and p.poll() is None:
                try:
                    if k.get(f"{ADRESSE}/api/v1/health").status_code == 200 and pfade.worker_schluessel().exists():
                        self._versuche = 0
                        self._setzen("laeuft")
                        self.bereit.set()
                        self._schreiben("Taleward läuft")
                        if callable(self.bei_bereit):
                            threading.Thread(target=self.bei_bereit, daemon=True, name="worker-koppeln").start()
                        threading.Thread(target=self._app_pruefen, args=(p,), daemon=True, name="app-da").start()
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        if p.poll() is None and p is self._prozess:
            self._setzen("fehler", code="startet_nicht")

    def _app_pruefen(self, p: subprocess.Popen) -> None:
        """Liegt die Web-App vor? Beim ersten Start holt der Server sie erst – bis dahin bleibt „Taleward öffnen“ aus.
        Nach APP_FRIST_S ein Hinweis (Internet prüfen), weiter nachsehen, nur seltener."""
        beginn = time.monotonic()
        self.app_fehlt = False
        with httpx.Client(timeout=5, headers={"User-Agent": f"TalewardWorker/{VERSION}"}) as k:
            while p.poll() is None and p is self._prozess:
                try:
                    if k.get(f"{ADRESSE}/app/").status_code == 200:
                        self.app_fehlt = False
                        self.app_bereit.set()
                        return
                except httpx.HTTPError:
                    pass
                lange = time.monotonic() - beginn > APP_FRIST_S
                if lange and not self.app_fehlt:
                    self.app_fehlt = True
                    self._schreiben("Die App liegt noch nicht vor – Internetverbindung prüfen")
                time.sleep(30 if lange else 2)

    def _lesen(self, p: subprocess.Popen) -> None:
        belegt = False
        for zeile in p.stdout:
            zeile = zeile.rstrip()
            if not zeile:
                continue
            self._schreiben(zeile)
            if "10048" in zeile or "address already in use" in zeile.lower():
                belegt = True
        code = p.wait()
        self.bereit.clear()
        self.app_bereit.clear()
        if self._schluessel:
            pfade.startschluessel().unlink(missing_ok=True)
        with self._sperre:
            if p is not self._prozess:
                return
            if not self._soll_laufen:
                self._setzen("gestoppt")
            elif belegt:
                self._setzen("fehler", code="anschluss_belegt")
                self._soll_laufen = False
            else:
                warte = WARTEZEITEN[min(self._versuche, len(WARTEZEITEN) - 1)]
                self._versuche += 1
                self._schreiben(f"Taleward unerwartet beendet (Code {code}) – Neustart in {warte} s")
                self._setzen("abgestuerzt", code=str(code), neuerVersuch=time.time() + warte)
                self._neustart_um = time.monotonic() + warte

    def _wachen(self) -> None:
        while True:
            time.sleep(1)
            try:
                with self._sperre:
                    if self._soll_laufen and self._neustart_um and time.monotonic() >= self._neustart_um:
                        self._neustart_um = None
                        self._prozess_starten()
            except Exception as e:  # noqa: BLE001 – der Wächter darf nie sterben
                self._schreiben(f"Fehler im Wächter: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ Konto und Worker
    def worker_token(self) -> str | None:
        try:
            wert = geschuetzt.text_oeffnen(pfade.worker_schluessel().read_text(encoding="utf-8").strip())
        except OSError:
            return None
        return wert if wert.startswith("wk.") else None

    def konto_anlegen(self, name: str, passwort: str, betriebsart: str | None = None) -> dict:
        """Erstes Konto über den Startschlüssel. Antwort {ok, anmeldename} bzw. {ok: False, fehler}."""
        if not self._schluessel:
            return {"ok": False, "fehler": "kein_schluessel"}
        body = {"schluessel": self._schluessel, "name": name, "passwort": passwort}
        if betriebsart:
            body["betriebsart"] = betriebsart
        try:
            with httpx.Client(timeout=30, headers={"User-Agent": f"TalewardWorker/{VERSION}"}) as k:
                r = k.post(f"{ADRESSE}/verwaltung/einzelplatz/konto", json=body)
        except httpx.HTTPError:
            return {"ok": False, "fehler": "nicht_erreichbar"}
        try:
            daten = r.json()
        except ValueError:
            daten = {}
        if r.status_code == 201:
            self._schluessel = None
            self._mit_schluessel = False
            return {"ok": True, "anmeldename": daten.get("anmeldename", "")}
        if r.status_code == 409:
            self._schluessel = None
            self._mit_schluessel = False
            return {"ok": False, "fehler": "schon_eingerichtet"}
        if r.status_code == 404:
            return {"ok": False, "fehler": "schluessel_abgelaufen"}
        code = daten.get("code") if isinstance(daten, dict) else None
        return {"ok": False, "fehler": code if isinstance(code, str) else "unbekannt"}

    # ------------------------------------------------------------------ Zustand
    def _setzen(self, art: str, **daten) -> None:
        self.zustand = {"art": art, "seit": time.time(), **daten}

    def _schreiben(self, text: str) -> None:
        zeile = f"{time.strftime('%d.%m. %H:%M:%S')}  {text}"
        self.zeilen.append(zeile)
        try:
            datei = pfade.protokolle() / "server.log"
            if datei.exists() and datei.stat().st_size > 2_000_000:
                os.replace(datei, datei.with_suffix(".log.1"))
            with datei.open("a", encoding="utf-8") as f:
                f.write(zeile + "\n")
        except OSError:
            pass

    def fuer_oberflaeche(self) -> dict:
        return {"zustand": self.zustand, "laeuft": self.laeuft(), "bereit": self.bereit.is_set(),
                "app": self.app_bereit.is_set(), "appFehlt": self.app_fehlt,
                "kontoMoeglich": bool(self._schluessel) and self.bereit.is_set()}
