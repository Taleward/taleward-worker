"""Fenster (pywebview) und Symbol im Infobereich (pystray). Die Oberfläche selbst ist HTML (ui/), ausgeliefert von
huelle.py; sie ruft die Methoden von ``Api`` auf und fragt den Zustand zweimal pro Sekunde ab.

Ein Fenster, zwei Ansichten: „Taleward Desktop“ (Alles auf diesem PC, großes Fenster mit Leiste, `desktop.html`) und
„Taleward Worker“ (Mit einem Server verbinden und die Einrichtung, kleines Fenster, `index.html`)."""
from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from taleward_worker import (VERSION, autostart, einzelplatz, fenster, hardware, huelle, mitnehmen, motor,
                             netzfreigabe, ollama, pfade, selbstupdate, server, serverupdate)
from taleward_worker.dienst import Dienst
from taleward_worker.einstellungen import Einstellungen

log = logging.getLogger("taleward_worker")
UPDATE_PRUEFEN_ALLE = 6 * 3600


class Api:
    """Alles, was die Oberfläche aufrufen darf (und nur das: die Brücke nimmt keine anderen Namen an, siehe
    fenster.bruecke_absichern). Rückgaben sind JSON-fähig.

    Folgenreiches (Betriebsart, Sicherung zurückspielen, Netzwerk-Freigabe, Installieren, Entfernen, Updates, Beenden)
    braucht zusätzlich den Prüfwert aus `zustand()`. Er gilt für einen Aufruf. Antworten der Brücke gehen nur an die
    Hauptseite – ein Rahmen, der trotz aller Regeln eine Nachricht absetzen könnte, kennt ihn nicht."""

    def __init__(self, app: "WorkerApp"):
        self._app = app
        self._pruefwert = secrets.token_urlsafe(18)

    def _gilt(self, wert) -> bool:
        ok = isinstance(wert, str) and secrets.compare_digest(wert.encode(), self._pruefwert.encode())
        if ok:
            self._pruefwert = secrets.token_urlsafe(18)
        else:
            log.warning("Aufruf ohne gültigen Prüfwert abgelehnt")
        return ok

    # -- Zustand
    def zustand(self) -> dict:
        a = self._app
        inst = motor.installiert()
        return {
            "version": VERSION, "system": "windows" if sys.platform == "win32" else "linux",
            "pruefwert": self._pruefwert, "ansicht": a.ansicht or a.ansicht_soll(),
            "einstellungen": a.e.fuer_oberflaeche(), "autostart": autostart.aktiv(),
            "dienst": a.dienst.fuer_oberflaeche(), "motor": inst, "profil": a.dienst.profil(),
            "installation": a.installation.stand() if a.installation else None,
            "hardware": a.hardware, "serverVersion": a.server_version, "serverName": a.server_name,
            "appUpdate": a.app_update.stand() if a.app_update else None, "trayVorhanden": a.tray is not None,
            "entwicklung": bool(os.environ.get("TALEWARD_MOTOR")),
            "ollama": a.ollama_stand(),
            "taleward": {**a.server.fuer_oberflaeche(), "netz": a.netz, "update": a.ep_update.fuer_oberflaeche(),
                         "sicherung": a.sicherung.stand()}
            if a.e.einzelplatz else None,
        }

    # -- Alles auf diesem PC
    def betrieb_waehlen(self, art: str) -> dict:
        a = self._app
        if art not in ("einzelplatz", "verbunden") or a.e.gekoppelt or a.e.art:
            return {"ok": False}
        a.e.betrieb = art
        a.e.speichern()
        return {"ok": True}

    def betrieb_wechseln(self, wert: str, ziel: str, regel_entfernen: bool = False) -> dict:
        """Zwischen „Alles auf diesem PC“ und „Mit einem Server verbinden“ wechseln – in beide Richtungen. Die
        Kampagnen von „Alles auf diesem PC“ bleiben auf dem PC und sind nach dem Zurückwechseln wieder da.
        `regel_entfernen` (nur zu „verbunden“, Haken im Dialog): danach die Netzwerk-Freigabe aus Windows nehmen."""
        a = self._app
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        if ziel not in ("einzelplatz", "verbunden") or ziel == a.e.art:
            return {"ok": False, "fehler": "unbekannt"}
        if a.ep_update.laeuft() or a.sicherung.laeuft() or (
                a.installation and a.installation.phase not in ("fertig", "fehler", "abgebrochen")):
            return {"ok": False, "fehler": "beschaeftigt"}
        if a.dienst.zustand.get("art") == "arbeitet":
            return {"ok": False, "fehler": "arbeitet"}
        threading.Thread(target=a.betrieb_wechseln, args=(ziel, bool(regel_entfernen) and ziel == "verbunden"),
                         daemon=True, name="betrieb").start()
        return {"ok": True}

    def konto_anlegen(self, name: str, passwort: str, passwort2: str) -> dict:
        a = self._app
        name = " ".join(str(name or "").split())[:128]
        if not a.e.einzelplatz or a.e.konto_angelegt:
            return {"ok": False, "fehler": "unbekannt"}
        if not name:
            return {"ok": False, "fehler": "name_missing"}
        if len(passwort or "") < einzelplatz.MIN_PASSWORT:
            return {"ok": False, "fehler": "password_too_short"}
        if passwort != passwort2:
            return {"ok": False, "fehler": "passwoerter_ungleich"}
        if not a.server.bereit.is_set():
            return {"ok": False, "fehler": "startet_noch"}
        r = a.server.konto_anlegen(name, passwort)
        if r.get("ok") or r.get("fehler") == "schon_eingerichtet":
            a.e.konto_angelegt = True
            a.e.anmeldename = r.get("anmeldename") or a.e.anmeldename
            a.e.speichern()
            return {"ok": True, "anmeldename": a.e.anmeldename}
        if r.get("fehler") == "schluessel_abgelaufen":
            a.server_neu_starten()  # neuer Schlüssel – der alte gilt nur 15 Minuten
        return r

    def taleward_oeffnen(self) -> dict:
        """Nach der Einrichtung: vom kleinen Fenster zu Taleward Desktop."""
        return {"ok": self._app.desktop_oeffnen()}

    def verwaltung_adresse(self, seite: str = "") -> dict:
        """Adresse der Verwaltung für den Rahmen in Taleward Desktop – nur feste Ziele, nur „Alles auf diesem PC“."""
        a = self._app
        if not a.e.einzelplatz or (seite or "") not in a.VERWALTUNG_SEITEN:
            return {"ok": False}
        pfad = a.VERWALTUNG_SEITEN[seite or ""].lstrip("/")
        return {"ok": True, "adresse": huelle.APP_HERKUNFT + "/verwaltung/" + pfad}

    def verarbeitung(self) -> dict | None:
        """Taleward Desktop: Stand der Verarbeitung vom Server dieses PCs (ohne Inhalte, siehe server.verarbeitung)."""
        return self._app.verarbeitung()

    def handy_adresse(self) -> dict:
        """Taleward Desktop: Adresse im Heimnetz zum Eintippen, falls ein Handy den QR-Code nicht liest."""
        a = self._app
        if not a.e.einzelplatz or not a.server.bereit.is_set():
            return {"adresse": None}
        return {"adresse": server.heimnetz_adresse(einzelplatz.ADRESSE)}

    def im_browser_oeffnen(self) -> None:
        if self._app.server.app_bereit.is_set():
            webbrowser.open(einzelplatz.APP_ADRESSE)

    def verwaltung_im_browser(self) -> None:
        """Ausweichlösung, solange die Verwaltung nicht im Fenster geht: feste Adresse dieses PCs im Browser."""
        if self._app.e.einzelplatz:
            webbrowser.open(huelle.APP_HERKUNFT + "/verwaltung/")

    def netz_freigeben(self, wert: str) -> dict:
        """Firewall-Regel anlegen (Windows fragt nach Administratorrechten)."""
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        ok = netzfreigabe.anlegen()
        self._app.netz_pruefen()
        return {"ok": ok}

    def netz_pruefen(self) -> dict:
        self._app.netz_pruefen()
        return self._app.netz or {}

    def taleward_neu_starten(self, wert: str) -> dict:
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        if not self._app.ep_update.laeuft():
            threading.Thread(target=self._app.server_neu_starten, daemon=True).start()
        return {"ok": True}

    def taleward_aktualisieren(self, wert: str) -> dict:
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        if not self._app.e.einzelplatz:
            return {"ok": False, "grund": "aktuell"}
        return self._app.ep_update.jetzt()

    # -- Sicherung und Hilfe-Datei
    def sicherung_speichern(self, passwort: str, passwort2: str) -> dict:
        a = self._app
        if not a.e.einzelplatz:
            return {"ok": False, "fehler": "unbekannt"}
        if len(passwort or "") < mitnehmen.MIN_PASSWORT:
            return {"ok": False, "fehler": "password_too_short"}
        if passwort != passwort2:
            return {"ok": False, "fehler": "passwoerter_ungleich"}
        ziel = a.datei_waehlen(speichern=True, name=f"taleward-sicherung-{time.strftime('%Y-%m-%d')}"
                               f"{mitnehmen.ENDUNG}")
        if not ziel:
            return {"ok": False, "fehler": "abgebrochen"}
        if ziel.suffix.lower() != mitnehmen.ENDUNG:
            ziel = ziel.with_name(ziel.name + mitnehmen.ENDUNG)
        return a.sicherung.speichern(ziel, passwort)

    def sicherung_waehlen(self) -> dict:
        a = self._app
        if not a.e.einzelplatz:
            return {"ok": False, "fehler": "unbekannt"}
        datei = a.datei_waehlen(speichern=False)
        if not datei:
            return {"ok": False, "fehler": "abgebrochen"}
        a.sicherung.gewaehlt = datei
        return {"ok": True, "datei": datei.name}

    def sicherung_zurueckspielen(self, wert: str, passwort: str) -> dict:
        a = self._app
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        if not a.e.einzelplatz or not passwort:
            return {"ok": False, "fehler": "passwort"}
        return a.sicherung.zurueckspielen(passwort)

    def hilfe_vorschau(self) -> dict:
        """Genau dieser Text wird gespeichert – nichts anderes."""
        self._app.hilfe = mitnehmen.hilfe_text(self._app)
        return {"text": self._app.hilfe}

    def hilfe_speichern(self) -> dict:
        a = self._app
        if not a.hilfe:
            return {"ok": False, "fehler": "keine_vorschau"}
        ziel = a.datei_waehlen(speichern=True, name=f"taleward-hilfe-{time.strftime('%Y-%m-%d')}.txt", text=True)
        if not ziel:
            return {"ok": False, "fehler": "abgebrochen"}
        try:
            ziel.write_text(a.hilfe, encoding="utf-8")
        except OSError:
            return {"ok": False, "fehler": "nicht_gespeichert"}
        a.hilfe = ""
        return {"ok": True, "datei": ziel.name}

    def hardware_pruefen(self) -> dict:
        self._app.hardware = hardware.bericht()
        return self._app.hardware

    # -- Einrichtung
    def server_pruefen(self, adresse: str) -> dict:
        try:
            a = server.adresse_normalisieren(adresse)
            info = server.pruefen(a)
        except server.ServerFehler as e:
            return {"ok": False, "fehler": e.code}
        return {"ok": True, "adresse": a, "name": info["name"], "unverschluesselt": server.unverschluesselt(a)}

    def koppeln(self, wert: str, adresse: str, code: str, name: str, unsicher: bool = False) -> dict:
        a = self._app
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        try:
            adr = server.adresse_normalisieren(adresse)
            if server.unverschluesselt(adr) and not unsicher:
                return {"ok": False, "fehler": "unverschluesselt"}
            d = server.koppeln(adr, code, name)
        except server.ServerFehler as e:
            return {"ok": False, "fehler": e.code, "text": e.text}
        a.dienst.stoppen()
        a.e.server, a.e.token, a.e.name, a.e.unsicher = adr, d["token"], d.get("name") or name, bool(unsicher)
        a.e.speichern()
        a.server_version = d.get("serverVersion") or a.server_version
        a.fassung_abfragen()
        return {"ok": True, "name": a.e.name, "serverVersion": a.server_version}

    def installieren(self, wert: str, testmodus: bool = False, prozessor: bool | None = None) -> dict:
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        if prozessor is not None:
            self._app.e.geraet = "cpu" if prozessor else "auto"
            self._app.e.speichern()
        self._app.installieren(bool(testmodus))
        return {"ok": True}

    def profil_vorschau(self, grenze_mb: int, modell: str | None = None, prozessor: bool | None = None) -> dict:
        """Was eine Einstellung bewirken würde (für den Schieberegler), ohne sie zu speichern."""
        a = self._app
        karten = hardware.grafikkarten()
        return hardware.profil(karten[0].vram_mb if karten else None, int(grenze_mb or 0), modell or a.e.modell,
                               prozessor=(a.e.geraet == "cpu") if prozessor is None else bool(prozessor))

    def installation_abbrechen(self) -> None:
        if self._app.installation:
            self._app.installation.abbrechen()

    # -- Betrieb
    def starten(self) -> None:
        self._app.dienst.starten()

    def stoppen(self) -> None:
        threading.Thread(target=self._app.dienst.stoppen, daemon=True).start()

    def pausieren(self, an: bool) -> None:
        self._app.dienst.pausieren(bool(an))
        self._app.tray_aktualisieren()

    def einstellung(self, name: str, wert) -> dict:
        a = self._app
        if name == "autostart":
            try:
                autostart.setzen(bool(wert))
            except OSError as e:
                return {"ok": False, "fehler": str(e)}
            a.e.autostart = bool(wert)
        elif name == "im_hintergrund":
            a.e.im_hintergrund = bool(wert)
        elif name == "auto_update":
            a.e.auto_update = bool(wert)
        elif name == "recaps_lokal":
            a.e.recaps_lokal = bool(wert)
            a.e.speichern()
            if a.e.recaps_lokal and not ollama.installiert() and not ollama.antwortet(ollama.FREMDE_ADRESSE):
                a.ollama_installieren()
            else:
                a.dienst.neu_starten_wenn_frei()
        elif name == "modell" and wert in ("auto", "large-v3", "large-v3-turbo"):
            a.e.modell = wert
            a.e.speichern()
            a.dienst.neu_starten_wenn_frei()
        elif name == "vram_grenze_mb":
            a.e.vram_grenze_mb = max(0, int(wert or 0))
            a.e.speichern()
            a.dienst.neu_starten_wenn_frei()
        elif name == "geraet" and wert in ("auto", "cpu"):
            a.e.geraet = wert
            a.e.speichern()
            if not a.motor_passt():  # KI-Paket nur für den Prozessor installiert → Grafikkarten-Fassung laden
                a.installieren(a.e.testmodus)
            else:
                a.dienst.neu_starten_wenn_frei()
        elif name == "sprache" and wert in ("", "de", "en"):
            a.e.sprache = wert
        elif name == "hinweis_hintergrund_gezeigt":
            a.e.hinweis_hintergrund_gezeigt = True
        elif name == "sicherung_erinnern":
            a.e.sicherung_erinnern = bool(wert)
        elif name == "leiste_schmal":
            a.e.leiste_schmal = bool(wert)
        else:
            return {"ok": False, "fehler": "unbekannt"}
        a.e.speichern()
        return {"ok": True}

    def app_aktualisieren(self, wert: str) -> dict:
        a = self._app
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        if not a.app_update:
            return {"ok": False}
        a.selbst_aktualisieren()
        return {"ok": True}

    def entkoppeln(self, wert: str) -> dict:
        """Mit einem (neuen) Kopplungscode wieder verbinden. Die Betriebsart wechselt „Betriebsart wechseln“."""
        a = self._app
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        if a.e.einzelplatz:
            return {"ok": False}  # der Worker gehört zu Taleward auf diesem PC
        a.dienst.stoppen()
        a.e.token = ""
        a.e.betrieb = "verbunden"
        a.e.speichern()
        return {"ok": True}

    def ki_entfernen(self, wert: str, modelle_auch: bool = True) -> dict:
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        self._app.dienst.stoppen()
        motor.entfernen(modelle_auch=bool(modelle_auch))
        self._app.e.motor_fassung = ""
        self._app.e.speichern()
        return {"ok": True}

    def ollama_entfernen(self, wert: str) -> dict:
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        self._ollama_entfernen()
        return {"ok": True}

    def _ollama_entfernen(self) -> None:
        a = self._app
        a.e.recaps_lokal = False
        a.e.speichern()
        if a.ollama_installation:
            a.ollama_installation.abbrechen()
        a.dienst.ollama.entfernen()
        a.dienst.neu_starten_wenn_frei()

    def speicherbelegung(self) -> dict:
        def mb(p):
            return pfade.ordnergroesse(p) // 2 ** 20 if p.exists() else 0
        return {"motorMb": mb(pfade.motor()) + mb(pfade.basis() / "python"), "modelleMb": mb(pfade.modelle()),
                "ollamaMb": mb(ollama.ordner()) + mb(ollama.modellordner()),
                "ordner": str(pfade.basis())}

    def protokoll(self) -> list[str]:
        z = list(self._app.dienst.protokoll.zeilen)
        inst = self._app.installation
        # Die Ausgabe der Installation nur, solange sie läuft oder wenn sie gescheitert ist – sonst stünde sie für
        # immer unter dem Protokoll des Workers, und dessen neue Zeilen wären nicht mehr unten zu sehen
        if inst and inst.phase != "fertig":
            z += ["", "— Installation —", *inst.zeilen[-200:]]
        return z[-800:]

    def ordner_oeffnen(self) -> None:
        ziel = str(pfade.basis())
        if sys.platform == "win32":
            os.startfile(ziel)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", ziel], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def link_oeffnen(self, url: str) -> None:
        """Nur https mit Host, ohne Zugangsdaten, Steuerzeichen und Backslashes (fenster.web_adresse)."""
        if isinstance(url, str) and url.startswith("https://") and fenster.web_adresse(url):
            webbrowser.open(url)

    def fenster_verstecken(self) -> None:
        self._app.verstecken()

    def beenden(self, wert: str) -> dict:
        if not self._gilt(wert):
            return {"ok": False, "fehler": "pruefwert"}
        threading.Thread(target=self._app.beenden, daemon=True).start()
        return {"ok": True}


class WorkerApp:
    def __init__(self, hintergrund: bool = False):
        self.e = Einstellungen.laden()
        self.dienst = Dienst(self.e)
        self._server: einzelplatz.ServerDienst | None = None  # erst bei „Alles auf diesem PC“
        self._update: serverupdate.ServerUpdate | None = None
        self.sicherung = mitnehmen.Sicherung(self)
        self.hilfe = ""  # zuletzt gezeigte Hilfe-Datei (genau diese wird gespeichert)
        self.huelle = huelle.Huelle(pfade.ressourcen() / "ui")
        self.ansicht = ""  # was das Fenster gerade zeigt: desktop | worker
        self._verarbeitung: tuple[float, dict | None] = (0.0, None)
        self.netz: dict | None = None  # Firewall und Netzprofil (Einzelplatz, Windows) – langsam, darum zwischengespeichert
        self.installation: motor.Installation | None = None
        self.ollama_installation: ollama.Installation | None = None
        self._fremd_geprueft: tuple[float, bool] = (0.0, False)
        self.hardware: dict | None = None
        self.server_version: str | None = None
        self.server_name: str | None = None
        self.app_update: selbstupdate.Aktualisierung | None = None
        self.hintergrund = hintergrund
        self.fenster = None
        self.tray = None
        self._beendet = threading.Event()

    # ------------------------------------------------------------------ Ablauf
    def fassung_abfragen(self) -> None:
        if not self.e.gekoppelt:
            return
        try:
            self.server_version = server.konfiguration(self.e.server, self.e.token).get("serverVersion")
            self.server_name = server.pruefen(self.e.server).get("name")
        except server.ServerFehler as e:
            if e.code == "abgelehnt":
                self.dienst._setzen("abgelehnt")

    # -- Alles auf diesem PC
    @property
    def server(self) -> einzelplatz.ServerDienst:
        """Der Server dieses PCs – nur im Betrieb „Alles auf diesem PC“ überhaupt angelegt."""
        if self._server is None:
            self._server = einzelplatz.ServerDienst()
            self._server.bei_bereit = self._worker_koppeln
        self._server.huelle = self.huelle.herkunft if self.huelle.anschluss else ""
        return self._server

    @property
    def ep_update(self) -> serverupdate.ServerUpdate:
        """Updates von Taleward auf diesem PC – wie der Server nur bei „Alles auf diesem PC“ angelegt."""
        if self._update is None:
            self._update = serverupdate.ServerUpdate(self)
        return self._update

    def taleward_starten(self) -> None:
        """Server aus dem KI-Paket starten; sobald er antwortet, koppelt sich der Worker (_worker_koppeln)."""
        if not self.e.einzelplatz or not (motor.installiert() or os.environ.get("TALEWARD_MOTOR")):
            return
        self.server.starten(startschluessel=not self.e.konto_angelegt)

    def betrieb_wechseln(self, ziel: str, regel_entfernen: bool = False) -> None:
        """Anhalten, was zur bisherigen Betriebsart gehört, und in die neue wechseln. Zu „Mit einem Server
        verbinden“ geht es mit der Verbindung von vorher weiter (sonst mit Adresse und Kopplungscode); zu „Alles auf
        diesem PC“ startet Taleward hier (beim ersten Mal mit Konto anlegen, sonst mit den Kampagnen von vorher)."""
        self.dienst.stoppen()
        if self._server is not None:
            self._server.stoppen()
        zurueck = False
        if ziel == "einzelplatz":
            self.e.verbindung_merken(self.server_name or "")
            self.e.token = ""
        else:
            zurueck = self.e.verbindung_zurueckholen()
            if not zurueck:
                self.e.server, self.e.token = "", ""
        self.e.betrieb = ziel
        self.e.speichern()
        self.server_version = self.server_name = None
        self.dienst.protokoll.schreiben("Betriebsart gewechselt: " + (
            "Alles auf diesem PC" if ziel == "einzelplatz" else "Mit einem Server verbinden"))
        if zurueck:
            self.dienst.protokoll.schreiben("Verbindung zum Server von vorher wieder eingesetzt")
            try:
                self._motor_pruefen(erster=True)  # passende Fassung prüfen, Worker starten
            except Exception:  # noqa: BLE001 – startet sonst mit der nächsten Pflege
                log.exception("Worker nach dem Zurückwechseln nicht gestartet")
        if regel_entfernen:
            entfernt = netzfreigabe.entfernen()
            self.dienst.protokoll.schreiben("Netzwerk-Freigabe entfernt" if entfernt
                                            else "Netzwerk-Freigabe nicht entfernt (abgelehnt oder gescheitert)")
            self.netz_pruefen()
        if ziel == "einzelplatz":
            self.hardware = hardware.bericht()
            if self.motor_passt_einzelplatz():
                self.taleward_starten()
            elif motor.installiert():
                self.installieren(self.e.testmodus)  # KI-Paket zu alt für „Alles auf diesem PC“ → neueste Fassung
        self.ansicht_anwenden()
        self.tray_aktualisieren()

    def motor_passt_einzelplatz(self) -> bool:
        if os.environ.get("TALEWARD_MOTOR"):
            return True
        fassung = str((motor.installiert() or {}).get("fassung") or "")
        return bool(fassung) and not selbstupdate.neuer(einzelplatz.MINDEST_SERVER, fassung)

    def datei_waehlen(self, speichern: bool, name: str = "", text: bool = False):
        """Dateidialog von Windows (bzw. des Systems). Liefert den Pfad oder None (abgebrochen)."""
        import webview

        arten = ("Textdatei (*.txt)",) if text else ("Taleward-Sicherung (*.taleward)",)
        dialog = getattr(webview, "FileDialog", None)
        typ = (dialog.SAVE if speichern else dialog.OPEN) if dialog else (
            webview.SAVE_DIALOG if speichern else webview.OPEN_DIALOG)
        try:
            ordner = str(pfade.dokumente())
            auswahl = self.fenster.create_file_dialog(typ, directory=ordner, save_filename=name, file_types=arten)
        except Exception as e:  # noqa: BLE001 – kein Dialog möglich
            log.warning("Dateidialog nicht möglich: %s", e)
            return None
        if not auswahl:
            return None
        return Path(auswahl if isinstance(auswahl, str) else auswahl[0])

    def netz_pruefen(self) -> None:
        if self.e.einzelplatz:
            try:
                self.netz = netzfreigabe.stand()
            except Exception:  # noqa: BLE001 – Anzeige ist Beiwerk
                log.debug("Netz nicht prüfbar", exc_info=True)

    def server_neu_starten(self) -> None:
        self.dienst.stoppen()
        self.server.stoppen()
        self.taleward_starten()

    def _worker_koppeln(self) -> None:
        """Bei jedem Serverstart: frischer Schlüssel aus der Datei, Worker damit (neu) starten."""
        token = self.server.worker_token()
        if not token:
            self.dienst.protokoll.schreiben("Taleward läuft, aber der Schlüssel für den Worker fehlt")
            return
        neu = token != self.e.token or self.e.server != einzelplatz.ADRESSE
        self.e.server, self.e.token, self.e.unsicher = einzelplatz.ADRESSE, token, False
        self.e.speichern()
        self.fassung_abfragen()
        if neu and self.dienst.laeuft():
            self.dienst.neu_starten()
        elif not self.dienst.laeuft():
            self.dienst.starten()
        self.tray_aktualisieren()

    def _netz_beim_ersten_mal(self, nach_installation: bool = False) -> None:
        """Nach dem Installieren, vor dem ersten Start: Firewall-Regel anlegen, damit Windows nicht seine eigene,
        viel weitere Rückfrage („Python“) zeigt. Lehnt jemand ab, fragt das Programm beim normalen Start nicht wieder
        von selbst. Nach einer (Neu-)Installation des KI-Pakets aber schon, wenn die Regel fehlt – etwa nach dem
        Deinstallieren und Neuinstallieren, das die Regel entfernt, die Einstellungen aber behält."""
        if not netzfreigabe.WINDOWS or (self.e.firewall_gefragt and not nach_installation):
            return
        self.e.firewall_gefragt = True
        self.e.speichern()
        try:
            stand = netzfreigabe.stand()
            if not stand.get("regel"):
                netzfreigabe.anlegen()
        except Exception as e:  # noqa: BLE001 – ohne Regel geht es weiter, die Oberfläche bietet „Erlauben“ an
            self.dienst.protokoll.schreiben(f"Netzwerk-Freigabe nicht möglich: {type(e).__name__}: {e}")
        self.netz_pruefen()

    VERWALTUNG_SEITEN = {"": "", "transkription#hf": "/transkription#hf"}  # nur feste Ziele aus der Oberfläche

    # -- Taleward Desktop / Taleward Worker
    def ansicht_soll(self) -> str:
        """Taleward Desktop, sobald „Alles auf diesem PC“ eingerichtet ist (Konto da); sonst das kleine Fenster
        (Taleward Worker bzw. die Einrichtung)."""
        bereit = motor.installiert() or os.environ.get("TALEWARD_MOTOR")
        return "desktop" if self.e.einzelplatz and self.e.konto_angelegt and bereit else "worker"

    def ansicht_anwenden(self) -> None:
        """Fenster auf die passende Ansicht umstellen: Seite, Titel, Größe (die letzte je Ansicht wird gemerkt)."""
        soll = self.ansicht_soll()
        w = self.fenster
        if w is None or soll == self.ansicht or getattr(w, "_taleward_dicht", False) is not True:
            return
        alt = self.ansicht
        try:
            if alt:
                setattr(self.e, "fenster_" + alt, [int(w.width), int(w.height)])
                self.e.speichern()
        except Exception:  # noqa: BLE001 – Größe merken ist Beiwerk
            pass
        self.ansicht = soll
        breite, hoehe, mindestens = fenstergroesse_fuer(soll, getattr(self.e, "fenster_" + soll), bildschirm_groesse())
        try:
            w.load_url(self.huelle.adresse(SEITEN[soll]))
            w.set_title(self.titel(soll))
            fenster.mindestgroesse(w, *mindestens)
            w.resize(breite, hoehe)
        except Exception:  # noqa: BLE001
            log.exception("Ansicht nicht umgestellt")

    def titel(self, ansicht: str) -> str:
        if ansicht == "desktop":
            return "Taleward Desktop"
        return "Taleward Worker" if self.e.art == "verbunden" else "Taleward"

    def desktop_oeffnen(self) -> bool:
        if self.ansicht_soll() != "desktop":
            return False
        self.ansicht_anwenden()
        self.bereich_zeigen("kampagnen")
        return True

    def bereich_zeigen(self, bereich: str) -> None:
        """Fenster nach vorn und (in Taleward Desktop) einen Bereich der Leiste wählen – nur feste Namen."""
        self.zeigen()
        if self.fenster is None or self.ansicht != "desktop" or bereich not in BEREICHE:
            return
        try:
            self.fenster.evaluate_js(f"window.talewardBereich && window.talewardBereich({json.dumps(bereich)})")
        except Exception:  # noqa: BLE001
            pass

    def verwaltung_oeffnen(self, seite: str = "") -> None:
        """Nur bei „Alles auf diesem PC“ (dort ist die Spielleitung Verwalter): im Fenster, Bereich Verwaltung."""
        if self.e.einzelplatz:
            self.bereich_zeigen("verwaltung")

    def verarbeitung(self) -> dict | None:
        """Stand der Verarbeitung vom Server dieses PCs, höchstens alle 3 Sekunden abgefragt."""
        if not (self.e.einzelplatz and self.e.gekoppelt):
            return None
        jetzt = time.monotonic()
        if jetzt - self._verarbeitung[0] > 3:
            self._verarbeitung = (jetzt, server.verarbeitung(self.e.server, self.e.token))
        return self._verarbeitung[1]

    def motor_passt(self) -> bool:
        inst = motor.installiert()
        if os.environ.get("TALEWARD_MOTOR"):
            return True
        if not inst:
            return False
        if bool(inst.get("testmodus")) != self.e.testmodus:
            return False
        if not self.e.testmodus and self.e.geraet != "cpu" and inst.get("backend") == "cpu":
            return False  # Grafikkarte gewünscht, installiert ist nur die Prozessor-Fassung
        if not self.e.testmodus and self.e.geraet != "cpu" and inst.get("torch", {}).get("cuda", "?") is None:
            return False  # als Grafikkarten-Fassung eingetragen, aber PyTorch kam ohne CUDA an → neu installieren
        return not self.server_version or inst.get("fassung") == self.server_version

    # -- Ollama (lokale Recaps)
    def ollama_installieren(self) -> None:
        if self.ollama_installation and self.ollama_installation.phase in ("laden", "entpacken"):
            return
        self.ollama_installation = ollama.Installation()
        faden = self.ollama_installation.starten()

        def danach():
            faden.join()
            if self.ollama_installation.phase == "fertig":
                self.dienst.neu_starten_wenn_frei()
        threading.Thread(target=danach, daemon=True).start()

    def ollama_stand(self) -> dict:
        jetzt = time.monotonic()
        if jetzt - self._fremd_geprueft[0] > 10:  # die Oberfläche fragt oft – das fremde Ollama selten prüfen
            self._fremd_geprueft = (jetzt, ollama.antwortet(ollama.FREMDE_ADRESSE, 0.5)
                                    if not self.dienst.ollama.laeuft() else False)
        inst = self.ollama_installation
        return {"an": self.e.recaps_lokal, "installiert": ollama.installiert(), "laeuft": self.dienst.ollama.laeuft(),
                "fremd": self._fremd_geprueft[1], "fassung": ollama.FASSUNG, "fehler": self.dienst.ollama.fehler,
                "moeglich": bool((self.hardware or {}).get("recapsMoeglich", True)),
                "installation": inst.stand() if inst and inst.phase != "fertig" else None,
                "sprachmodell": self.dienst.info.get("llm")}

    def installieren(self, testmodus: bool) -> None:
        if self.installation and self.installation.phase not in ("fertig", "fehler", "abgebrochen"):
            return
        if self._update is not None and self._update.laeuft():
            return
        self.e.testmodus = testmodus
        self.e.speichern()
        fehlt_gb = motor.platz_fehlt_gb(testmodus, "cpu" if self.e.geraet == "cpu" else "cuda")
        if fehlt_gb:
            # Gar nicht erst anfangen: ein voller Datenträger bricht mittendrin ab, und Windows selbst leidet
            self.installation = motor.Installation(self.server_version or "", testmodus=testmodus)
            self.installation.phase, self.installation.fehler = "fehler", "kein_platz"
            self.installation.fehlertext = f"Es fehlen etwa {fehlt_gb:.0f} GB freier Speicherplatz."
            self.dienst.protokoll.schreiben(f"Installation nicht gestartet: {self.installation.fehlertext}")
            return
        war_aktiv = {"an": False}

        def vor():
            war_aktiv["an"] = self.dienst.laeuft()
            self.dienst.stoppen()
            if self.e.einzelplatz:
                self.server.stoppen()  # der Server läuft aus dem KI-Paket, das gleich ersetzt wird

        def nach():
            if self.installation and self.installation.phase != "fehler":
                self.e.motor_fassung = self.installation.fassung
                self.e.motor_art = "test" if testmodus else "ki"
                self.e.speichern()
            if self.e.einzelplatz:
                self._netz_beim_ersten_mal(nach_installation=self.installation is not None
                                           and self.installation.phase != "fehler")
                self.taleward_starten()
            elif self.e.gekoppelt:
                self.dienst.starten()

        backend = "cpu" if self.e.geraet == "cpu" else "cuda"
        # Alles auf diesem PC: kein Server nennt die Fassung. Erstinstallation → die neueste freigegebene von GitHub;
        # Neu installieren → dieselbe Fassung wie bisher (eine neue kommt nur über das Update mit Sicherung)
        if self.e.einzelplatz:
            fassung = str((motor.installiert() or {}).get("fassung") or "") if not testmodus else ""
            if fassung and selbstupdate.neuer(einzelplatz.MINDEST_SERVER, fassung):
                fassung = ""  # z. B. nach dem Wechsel von „Mit einem Server verbinden“: zu alt → neueste
        else:
            fassung = self.server_version or ""
        self.installation = motor.Installation(fassung, testmodus=testmodus, backend=backend,
                                               vor_tausch=vor, nach_tausch=nach,
                                               neueste=self.e.einzelplatz and not fassung)
        self.installation.starten()

    def _motor_pruefen(self, erster: bool) -> None:
        """Serverfassung abfragen; passt das KI-Paket nicht mehr, im Hintergrund nachziehen (nur ohne Auftrag)."""
        if self.e.einzelplatz:
            # Alles auf diesem PC: Taleward starten, der Worker folgt; Updates mit Sicherung und Rückweg
            if self.ep_update.laeuft():
                return  # das Update hält Taleward bewusst an
            laeuft_installation = self.installation and self.installation.phase not in ("fertig", "fehler",
                                                                                         "abgebrochen")
            if not laeuft_installation and not self.server.laeuft() and self.server.zustand.get("art") in (
                    "gestoppt",) and (motor.installiert() or os.environ.get("TALEWARD_MOTOR")):
                self.taleward_starten()
            if not laeuft_installation:
                self.ep_update.wenn_faellig()
            return
        self.fassung_abfragen()
        if not self.e.gekoppelt:
            return
        if self.motor_passt():
            if erster and not self.dienst.laeuft():
                self.dienst.starten()
        elif motor.installiert() or os.environ.get("TALEWARD_MOTOR"):
            # Neue Serverfassung: Motor im Hintergrund aktualisieren (alter läuft bis zum Tausch weiter)
            if erster and not self.dienst.laeuft():
                self.dienst.starten()
            laeuft_installation = self.installation and self.installation.phase not in ("fertig", "fehler",
                                                                                         "abgebrochen")
            if self.dienst.zustand.get("art") != "arbeitet" and not laeuft_installation:
                self.installieren(self.e.testmodus)

    def _ollama_pruefen(self) -> None:
        """Lokale Recaps gewünscht, aber Ollama fehlt oder hat eine alte Fassung (nach einem App-Update)? Dann
        nachladen – sonst wanderten die Recaps still auf den Server, und die Oberfläche zeigte nichts."""
        if not self.e.recaps_lokal or not self.e.gekoppelt or self.e.testmodus:
            return
        if ollama.installiert() or ollama.antwortet(ollama.FREMDE_ADRESSE):
            return
        if self.ollama_installation and self.ollama_installation.phase not in ("fertig", "fehler"):
            return
        if self.ollama_installation and self.ollama_installation.phase == "fehler":
            return  # nicht im Kreis laden – der Nutzer sieht den Fehler und kann neu anstoßen
        self.dienst.protokoll.schreiben("Ollama fehlt oder ist veraltet – wird geladen")
        self.ollama_installieren()

    def _hintergrund_pflege(self) -> None:
        """Beim Start: Fassung prüfen, ggf. Motor aktualisieren, Worker starten. Danach alle 5 Minuten die
        Serverfassung (ein kleiner Abruf – so zieht das KI-Paket kurz nach einem Server-Update nach) und alle
        6 Stunden, ob es eine neue App gibt."""
        naechste_app_pruefung = 0.0
        erster = True
        while not self._beendet.is_set():
            try:
                self._motor_pruefen(erster)
                self._ollama_pruefen()
                self.netz_pruefen()
                if time.time() >= naechste_app_pruefung and self.e.gekoppelt:
                    self.app_update_pruefen()
                    naechste_app_pruefung = time.time() + UPDATE_PRUEFEN_ALLE
                erster = False
                self.tray_aktualisieren()
            except Exception as e:  # nie sterben – sonst startet der Worker nie und niemand erfährt warum
                self.dienst.protokoll.schreiben(f"Fehler in der Hintergrundpflege: {type(e).__name__}: {e}")
                log.exception("Hintergrundpflege")
            if self._beendet.wait(300):
                return
            try:
                self.app_update_wenn_frei()
            except Exception as e:
                self.dienst.protokoll.schreiben(f"Fehler beim App-Update: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ Selbst-Update der App
    def app_update_pruefen(self) -> None:
        angebot = selbstupdate.abfragen(self.e.server, self.e.token)
        if angebot is None:
            if self.app_update and self.app_update.phase in ("bereit", "fehler"):
                self.app_update = None
            return
        if not self.app_update or self.app_update.angebot.get("version") != angebot.get("version"):
            self.app_update = selbstupdate.Aktualisierung(angebot, self.e.server, self.e.token)
        self.app_update_wenn_frei()

    def app_update_wenn_frei(self) -> None:
        """Automatisch nur, wenn gerade kein Auftrag läuft und das KI-Paket nicht installiert wird."""
        u = self.app_update
        if not u or u.phase != "bereit" or not self.e.auto_update or not selbstupdate.selbst_installierbar():
            return
        beschaeftigt = self.dienst.zustand.get("art") == "arbeitet" or (
            self.installation and self.installation.phase not in ("fertig", "fehler", "abgebrochen")) or (
            self._update is not None and self._update.laeuft())
        if not beschaeftigt:
            self.selbst_aktualisieren()

    def selbst_aktualisieren(self) -> None:
        if self.app_update and self.app_update.phase in ("bereit", "fehler"):
            self.app_update.phase, self.app_update.fehler = "bereit", ""
            self.app_update.starten(self.dienst.stoppen, self.beenden)

    # ------------------------------------------------------------------ Fenster und Infobereich
    def zeigen(self) -> None:
        if self.fenster:
            self.fenster.show()
            self.fenster.restore()

    def verstecken(self) -> None:
        if self.fenster:
            if self.tray is not None:
                self.fenster.hide()
                if not self.e.hinweis_hintergrund_gezeigt:
                    self.e.hinweis_hintergrund_gezeigt = True
                    self.e.speichern()
                    en = self.e.sprache.startswith("en")
                    try:
                        self.tray.notify("Taleward keeps running in the background. Right-click the icon to quit."
                                         if en else "Taleward läuft im Hintergrund weiter. Beenden über "
                                         "Rechtsklick auf das Symbol.", "Taleward")
                    except Exception:  # noqa: BLE001 – Hinweis ist Beiwerk
                        pass
            else:
                self.fenster.minimize()

    def _abmeldung_beachten(self) -> None:
        """Windows: Beim Herunterfahren oder Abmelden nicht im Weg stehen. Das Fenster lehnt Schließen sonst ab
        (es soll ja in den Infobereich wandern) – Windows zeigte dann „Taleward Worker verhindert das
        Herunterfahren“. SessionEnding kommt vor der Schließanfrage; danach lässt _beim_schliessen sie durch."""
        if sys.platform != "win32":
            return
        try:
            import clr  # pythonnet, kommt mit pywebview

            clr.AddReference("System")
            from Microsoft.Win32 import SystemEvents  # type: ignore[import-not-found]

            def ende(_sender, _args):
                self._beendet.set()
                threading.Thread(target=self.beenden, daemon=True, name="abmeldung").start()

            SystemEvents.SessionEnding += ende
        except Exception as e:  # noqa: BLE001 – dann bleibt es beim alten Verhalten
            log.debug("SessionEnding nicht abonniert: %s", e)

    def beenden(self) -> None:
        self._beendet.set()
        try:
            if self.fenster is not None and self.ansicht:
                setattr(self.e, "fenster_" + self.ansicht, [int(self.fenster.width), int(self.fenster.height)])
                self.e.speichern()
        except Exception:  # noqa: BLE001 – Größe merken ist Beiwerk
            pass
        for inst in (self.installation, self.ollama_installation):
            if inst is not None:
                inst.abbrechen()  # sonst schreibt uv nach dem Ende der App minutenlang weiter in motor-neu
        self.dienst.stoppen()
        self.dienst.ollama.stoppen()
        if self._server is not None:
            self._server.stoppen()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.fenster:
            self.fenster.destroy()

    def _beim_schliessen(self):
        if self._beendet.is_set():
            return True
        if self.e.im_hintergrund and self.e.gekoppelt:
            self.verstecken()
            return False
        threading.Thread(target=self.beenden, daemon=True).start()
        return False

    def tray_aktualisieren(self) -> None:
        if self.tray is None:
            return
        try:
            from taleward_worker.symbole import symbol

            art = self.dienst.zustand.get("art", "gestoppt")
            self.tray.icon = symbol(art)
            self.tray.title = tray_titel(self.dienst.fuer_oberflaeche(), self.e.sprache,
                                         self._update is not None and self._update.laeuft())
            self.tray.update_menu()
        except Exception:  # noqa: BLE001 – Infobereich ist Beiwerk
            log.debug("Infobereich nicht aktualisiert", exc_info=True)

    def _tray_starten(self) -> None:
        try:
            import pystray

            from taleward_worker.symbole import symbol
        except Exception:  # noqa: BLE001
            return
        de = not self.e.sprache.startswith("en")

        def pause_text(_):
            return (("Fortsetzen" if de else "Resume") if self.e.pausiert else ("Pausieren" if de else "Pause"))

        def app_oeffnen():
            self.bereich_zeigen("kampagnen")

        def mit_app() -> bool:
            return bool(self.e.einzelplatz and self.e.konto_angelegt)

        menue = pystray.Menu(
            # Bei „Alles auf diesem PC“ ist die App das Hauptfenster (Doppelklick), das Programm der zweite Punkt
            pystray.MenuItem("Taleward öffnen" if de else "Open Taleward", app_oeffnen,
                             visible=lambda _: mit_app(), default=mit_app()),
            pystray.MenuItem("Einstellungen und Status" if de else "Settings and status",
                             lambda: self.bereich_zeigen("uebersicht"), default=not mit_app()),
            pystray.MenuItem("Verwaltung" if de else "Administration", lambda: self.verwaltung_oeffnen(),
                             visible=lambda _: mit_app()),
            pystray.MenuItem(pause_text, lambda: (self.dienst.pausieren(not self.e.pausiert),
                                                  self.tray_aktualisieren())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden" if de else "Quit", lambda: threading.Thread(target=self.beenden,
                                                                                   daemon=True).start()),
        )
        try:
            icon = pystray.Icon("taleward-worker", symbol("gestoppt"), "Taleward", menue)
            icon.run_detached()
        except Exception:  # noqa: BLE001 – z. B. Wayland ohne Infobereich
            return
        if sys.platform != "win32":
            # X11 ohne Infobereich (z. B. GNOME ohne Erweiterung): pystray „läuft“, das Symbol ist aber unsichtbar.
            # Dann lieber kein Symbol – sonst wäre ein verstecktes Fenster nicht mehr erreichbar.
            time.sleep(1.0)
            if hasattr(icon, "_systray_manager") and not icon._systray_manager:
                try:
                    icon.stop()
                except Exception:  # noqa: BLE001
                    pass
                return
        self.tray = icon

    def _tray_nachfuehren(self) -> None:
        letzte = None
        hilfe_vorher = False
        while not self._beendet.wait(2):
            jetzt = (self.dienst.zustand.get("art"), self.e.pausiert, round(self.dienst.zustand.get("p") or 0, 1))
            if jetzt != letzte:
                letzte = jetzt
                self.tray_aktualisieren()
            # Braucht etwas Hilfe, kommt das Programmfenster von selbst nach vorn – einmal je Vorfall. Nicht in Taleward
            # Desktop: Dort läuft womöglich gerade eine Runde im selben Fenster; die Leiste zeigt es (keine Störung)
            hilfe = self.braucht_hilfe()
            if hilfe and not hilfe_vorher and self.ansicht != "desktop":
                try:
                    self.zeigen()
                except Exception:  # noqa: BLE001 – Fenster gibt es (noch) nicht
                    pass
            hilfe_vorher = hilfe

    def braucht_hilfe(self) -> bool:
        """Zustände, die der Nutzer selbst lösen muss. Wartet der Worker nur auf das Sprechermodell, nicht."""
        z = self.dienst.zustand
        if z.get("art") == "fehler" and z.get("code") != "modell":
            return True
        return bool(self.e.einzelplatz and self._server is not None
                    and self._server.zustand.get("art") == "fehler")

    def run(self) -> None:
        import webview

        from taleward_worker import einzeln

        if einzeln.andere_wecken() or not einzeln.sperren():
            return
        einzeln.lauschen(self.zeigen, self.beenden)
        self._abmeldung_beachten()
        if not webview2_vorhanden():
            webview2_fehlt_melden()
            return
        self.hardware = hardware.bericht()
        fenster.absichern()  # vor dem ersten Fenster: an den Browser nur noch http(s)
        try:
            self.huelle.starten()  # vor dem Server: er bekommt die Herkunft der Oberfläche mit (frame-ancestors)
        except OSError:
            log.exception("Oberfläche nicht startbar")
            meldung_zeigen("Taleward konnte nicht starten: kein freier Anschluss auf diesem PC. Starte den PC neu "
                           "und versuch es dann noch einmal.")
            return
        if not fenster.bruecke_absichern(self.huelle.herkunft, fenster.api_namen(Api)):
            meldung_zeigen("Taleward konnte sein Fenster nicht sicher öffnen. Bitte installiere Taleward neu.")
            self.huelle.stoppen()
            return
        self._tray_starten()
        versteckt = self.hintergrund and self.e.gekoppelt
        self.ansicht = self.ansicht_soll()
        breite, hoehe, mindestens = fenstergroesse_fuer(self.ansicht, getattr(self.e, "fenster_" + self.ansicht),
                                                        bildschirm_groesse())
        self.fenster = fenster.hauptfenster(
            self.titel(self.ansicht), self.huelle.adresse(SEITEN[self.ansicht]), self.huelle.herkunft, Api(self),
            width=breite, height=hoehe, min_size=mindestens, text_select=True,
            hidden=versteckt and self.tray is not None, minimized=versteckt and self.tray is None)
        self.fenster.events.closing += self._beim_schliessen
        threading.Thread(target=self._hintergrund_pflege, daemon=True, name="pflege").start()
        threading.Thread(target=self._tray_nachfuehren, daemon=True, name="tray").start()
        # Dauerhafter Speicher im Benutzerordner (derselbe wie für das frühere App-Fenster): Die App im Rahmen merkt
        # sich ihre Anmeldung und sichert Aufnahmen bis zum Hochladen. Downloads (Umzugsdatei, „Kapitel teilen“) mit
        # Speichern-Dialog. Neue Fenster gehen nie ins Programm (fenster.bruecke_absichern).
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        speicher = pfade.fenster_speicher()
        fenster.zwischenspeicher_leeren(speicher, VERSION)
        try:  # Programmdatei heißt seit 0.3.9 Taleward.exe: vorhandenen Autostart-Eintrag mitnehmen
            if autostart.nachfuehren():
                self.dienst.protokoll.schreiben("Autostart auf das umbenannte Programm umgestellt")
        except OSError:
            pass
        webview.start(debug=bool(os.environ.get("TALEWARD_DEBUG")), private_mode=False, storage_path=str(speicher))
        self.huelle.stoppen()
        self._beendet.set()
        self.dienst.stoppen()
        if self._server is not None:
            self._server.stoppen()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass


def tray_titel(d: dict, sprache: str, update: bool = False) -> str:
    en = sprache.startswith("en")
    if update:
        return "Taleward – " + ("Updating …" if en else "Wird aktualisiert …")
    z = d["zustand"]
    art = z.get("art")
    texte = {
        "warte": ("Bereit – wartet auf Aufnahmen", "Ready – waiting for recordings"),
        "arbeitet": (f"Arbeitet … {round((z.get('p') or 0) * 100)} %", f"Working … {round((z.get('p') or 0) * 100)}%"),
        "pausiert": ("Pausiert", "Paused"),
        "server_pausiert": ("Auf dem Server pausiert", "Paused on the server"),
        "getrennt": ("Keine Verbindung zum Server", "No connection to the server"),
        "startet": ("Startet …", "Starting …"),
        "fehler": ("Braucht deine Hilfe", "Needs your attention"),
        "abgelehnt": ("Nicht mehr gekoppelt", "No longer paired"),
        "abgestuerzt": ("Startet neu …", "Restarting …"),
        "gestoppt": ("Angehalten", "Stopped"),
    }
    de_text, en_text = texte.get(art, ("Bereit", "Ready"))
    return "Taleward – " + (en_text if en else de_text)


# ---------------------------------------------------------------------- Fenster und Browser-Kern
FENSTER = (560, 760)
FENSTER_MINDESTENS = (460, 600)
DESKTOP = (1280, 820)
DESKTOP_MINDESTENS = (900, 600)
SEITEN = {"desktop": "desktop.html", "worker": "index.html"}
BEREICHE = ("kampagnen", "schirm", "uebersicht", "verarbeitung", "sicherung", "einstellungen", "hilfe", "verwaltung")


def bildschirm_groesse() -> tuple[int, int] | None:
    try:
        import webview

        s = webview.screens[0] if webview.screens else None
        return (int(s.width), int(s.height)) if s else None
    except Exception:  # noqa: BLE001 – dann die Vorgabe
        return None


def fenstergroesse_fuer(ansicht: str, gemerkt, bildschirm: tuple[int, int] | None) -> tuple[int, int, tuple[int, int]]:
    """Größe je Ansicht: zuletzt gemerkt, sonst die Vorgabe – immer ganz auf dem Bildschirm (Taskleiste und Rahmen
    abgezogen) und nie unter der Mindestgröße, außer der Bildschirm ist selbst kleiner."""
    if ansicht != "desktop":
        breite, hoehe, mindestens = fenstergroesse(bildschirm[1] if bildschirm else None)
        if isinstance(gemerkt, list) and len(gemerkt) == 2 and all(isinstance(x, int) for x in gemerkt):
            breite, hoehe = max(mindestens[0], gemerkt[0]), max(mindestens[1], gemerkt[1])
            if bildschirm:
                breite, hoehe = min(breite, bildschirm[0]), min(hoehe, max(400, bildschirm[1] - 80))
        return breite, hoehe, mindestens
    breite, hoehe = DESKTOP
    if isinstance(gemerkt, list) and len(gemerkt) == 2 and all(isinstance(x, int) for x in gemerkt):
        breite, hoehe = gemerkt
    mindestens = DESKTOP_MINDESTENS
    if bildschirm:
        nutzbar_b, nutzbar_h = max(400, bildschirm[0] - 20), max(400, bildschirm[1] - 80)
        mindestens = (min(mindestens[0], nutzbar_b), min(mindestens[1], nutzbar_h))
        breite, hoehe = min(breite, nutzbar_b), min(hoehe, nutzbar_h)
    return max(breite, mindestens[0]), max(hoehe, mindestens[1]), mindestens


def meldung_zeigen(text: str) -> None:
    log.error(text)
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Taleward", 0x10)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
WEBVIEW2_LINK = "https://developer.microsoft.com/microsoft-edge/webview2/"


def bildschirm_hoehe() -> int | None:
    try:
        import webview

        return int(webview.screens[0].height) if webview.screens else None
    except Exception:  # noqa: BLE001 – dann die Vorgabe
        return None


def fenstergroesse(bildschirm: int | None) -> tuple[int, int, tuple[int, int]]:
    """Fenstergröße so, dass es auch auf 1366×768 bei 125 % Skalierung (≈ 614 logische Pixel) ganz sichtbar ist –
    die Knöpfe „Weiter“ und „Installieren“ sitzen unten."""
    breite, hoehe = FENSTER
    if not bildschirm:
        return breite, hoehe, FENSTER_MINDESTENS
    nutzbar = max(400, bildschirm - 80)  # Taskleiste und Fensterrahmen
    return breite, min(hoehe, nutzbar), (FENSTER_MINDESTENS[0], min(FENSTER_MINDESTENS[1], nutzbar))


def webview2_vorhanden() -> bool:
    """Windows: pywebview fiele ohne WebView2 still auf den alten Internet-Explorer-Kern zurück, der unsere
    Oberfläche nicht ausführen kann – das Fenster bliebe leer, ohne jede Meldung."""
    if sys.platform != "win32":
        return True
    try:
        from webview.platforms import winforms

        return bool(getattr(winforms, "is_chromium", True))
    except Exception:  # noqa: BLE001 – lieber starten als fälschlich blockieren
        return True


def webview2_fehlt_melden() -> None:
    log.error("WebView2 fehlt – Oberfläche kann nicht starten")
    if sys.platform != "win32":
        return
    try:
        import ctypes

        antwort = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None, "Taleward braucht die Microsoft-Komponente „WebView2“, die auf diesem PC fehlt.\n\n"
            "Jetzt die Download-Seite öffnen? (Installieren, dann Taleward erneut starten.)",
            "Taleward", 0x14)  # MB_YESNO | MB_ICONERROR
        if antwort == 6:  # IDYES
            webbrowser.open(WEBVIEW2_LINK)
    except Exception:  # noqa: BLE001
        pass
