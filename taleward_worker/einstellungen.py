"""Einstellungen der App (JSON im Benutzerordner). Der Worker-Schlüssel steht nur hier und ist nur für den
Benutzer lesbar – unter Windows zusätzlich mit dem Windows-Datenschutz geschützt (geschuetzt.py)."""
from __future__ import annotations

import json
import os
import shutil
import socket
import time
from dataclasses import asdict, dataclass, field, fields

from taleward_worker import geschuetzt, pfade


@dataclass
class Einstellungen:
    server: str = ""
    token: str = ""
    name: str = field(default_factory=socket.gethostname)
    unsicher: bool = False          # HTTP im Heimnetz erlaubt
    testmodus: bool = False         # ohne KI (Platzhaltertext) – zum Ausprobieren ohne Grafikkarte
    modell: str = "auto"            # auto | large-v3 | large-v3-turbo
    geraet: str = "auto"            # auto (Grafikkarte, wenn nutzbar) | cpu (nur Prozessor)
    vram_grenze_mb: int = 0         # höchstens so viel Grafikspeicher für Taleward (0 = alles)
    autostart: bool = False
    im_hintergrund: bool = True     # Fenster schließen = weiterlaufen (Symbol im Infobereich)
    auto_update: bool = True        # neue Fassungen der App selbst installieren, sobald der Worker frei ist
    recaps_lokal: bool = False      # Recaps auch hier schreiben (Ollama auf diesem PC)
    pausiert: bool = False
    motor_fassung: str = ""         # installierte Fassung des KI-Pakets (= Serverfassung)
    motor_art: str = ""             # "ki" oder "test"
    sprache: str = ""               # leer = Systemsprache
    betrieb: str = ""               # "" = noch nicht gewählt | verbunden | einzelplatz („Alles auf diesem PC“)
    konto_angelegt: bool = False    # Einzelplatz: Konto der SL angelegt
    anmeldename: str = ""           # Einzelplatz: zum Anzeigen („Dein Anmeldename“)
    firewall_gefragt: bool = False  # Einzelplatz: Netzwerk-Freigabe einmal von selbst angeboten
    update_gescheitert: str = ""    # Einzelplatz: Server-Fassung, deren Update zurückgenommen wurde (nicht von selbst wieder)
    hinweis_hintergrund_gezeigt: bool = False
    # Verbindung zu einem Server, beiseitegelegt während „Alles auf diesem PC“ – beim Zurückwechseln wieder da
    verbunden_server: str = ""
    verbunden_token: str = ""
    verbunden_unsicher: bool = False
    verbunden_name: str = ""        # Name des Servers zum Anzeigen

    def verbindung_merken(self, servername: str = "") -> bool:
        """Vor dem Wechsel zu „Alles auf diesem PC“: die bestehende Server-Verbindung beiseitelegen."""
        if self.art != "verbunden" or not self.gekoppelt:
            return False
        self.verbunden_server, self.verbunden_token = self.server, self.token
        self.verbunden_unsicher, self.verbunden_name = self.unsicher, servername or ""
        return True

    def verbindung_zurueckholen(self) -> bool:
        """Beim Wechsel zu „Mit einem Server verbinden“: die beiseitegelegte Verbindung wieder einsetzen."""
        if not (self.verbunden_server and self.verbunden_token):
            return False
        self.server, self.token, self.unsicher = self.verbunden_server, self.verbunden_token, self.verbunden_unsicher
        self.verbunden_server = self.verbunden_token = self.verbunden_name = ""
        self.verbunden_unsicher = False
        return True

    @property
    def gekoppelt(self) -> bool:
        return bool(self.server and self.token)

    @property
    def einzelplatz(self) -> bool:
        return self.betrieb == "einzelplatz"

    @property
    def art(self) -> str:
        """Betriebsart mit Rückfall für ältere Einstellungen (vor 0.3.0 gab es nur „verbunden“)."""
        if self.betrieb in ("verbunden", "einzelplatz"):
            return self.betrieb
        return "verbunden" if self.server and self.token else ""

    def speichern(self) -> None:
        """Sicher schreiben: erst in eine Nebendatei, auf die Platte zwingen (fsync – sonst kann nach einem
        Stromausfall eine leere Datei übrig bleiben und der Kopplungs-Token wäre weg), dann tauschen. Unter Windows
        kann der Tausch kurz an Virenscanner oder Indexdienst scheitern – darum mit Wiederholung."""
        ziel = pfade.einstellungen()
        tmp = ziel.with_suffix(".tmp")
        daten = asdict(self)
        daten["token"] = geschuetzt.text_schuetzen(self.token)
        daten["verbunden_token"] = geschuetzt.text_schuetzen(self.verbunden_token) if self.verbunden_token else ""
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(daten, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        sicherung = ziel.with_suffix(".bak")
        if ziel.exists():
            _mit_geduld(lambda: shutil.copyfile(ziel, sicherung))
        _mit_geduld(lambda: os.replace(tmp, ziel))

    @classmethod
    def laden(cls) -> "Einstellungen":
        for pfad in (pfade.einstellungen(), pfade.einstellungen().with_suffix(".bak")):
            try:
                daten = json.loads(pfad.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(daten, dict):
                bekannt = {f.name for f in fields(cls)}
                e = cls(**{k: v for k, v in daten.items() if k in bekannt})
                e.token = geschuetzt.text_oeffnen(str(e.token or ""))
                e.verbunden_token = geschuetzt.text_oeffnen(str(e.verbunden_token or ""))
                return e
        return cls()

    def fuer_oberflaeche(self) -> dict:
        d = asdict(self)
        d.pop("token")
        d.pop("verbunden_token")
        d["verbundenGemerkt"] = bool(self.verbunden_server and self.verbunden_token)
        d["gekoppelt"] = self.gekoppelt
        d["art"] = self.art
        return d


def _mit_geduld(aktion, versuche: int = 10, pause: float = 0.1) -> None:
    for i in range(versuche):
        try:
            aktion()
            return
        except PermissionError:
            if i == versuche - 1:
                raise
            time.sleep(pause)
