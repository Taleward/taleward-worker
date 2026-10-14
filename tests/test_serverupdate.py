"""Updates für „Alles auf diesem PC“: Sicherung vorher, Prüfung danach, Rückweg bei Fehlern (E7)."""
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ersatz für `chronik einzelplatz`: meldet die Fassung aus einer Datei (= „installiertes KI-Paket“).
# Fassung „x.y.z-absturz“: startet, beendet sich aber nach einer Sekunde.
ERSATZ = r'''
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

fassung = Path(os.environ["ERSATZ_FASSUNG"]).read_text().strip()
Path(os.environ["EINZELPLATZ_WORKER_DATEI"]).write_text("wk.ersatz.1\n", encoding="utf-8")
port = int(os.environ["ERSATZ_PORT"])
stopp = Path(os.environ["EINZELPLATZ_STOPP_DATEI"])
beschaeftigt = Path(os.environ["ERSATZ_BUSY"])

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _json(self, code, daten):
        roh = json.dumps(daten).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh))); self.end_headers(); self.wfile.write(roh)
    def do_GET(self):
        if self.path == "/api/v1/health":
            return self._json(200, {"status": "ok"})
        if self.headers.get("Authorization") != "Bearer wk.ersatz.1":
            return self._json(401, {})
        if self.path == "/worker/v1/config":
            return self._json(200, {"serverVersion": fassung.split("-")[0]})
        if self.path == "/worker/v1/busy":
            grund = beschaeftigt.read_text().strip() if beschaeftigt.exists() else ""
            return self._json(200, {"busy": bool(grund), "reason": grund or None})
        self._json(404, {})

s = HTTPServer(("127.0.0.1", port), H)
def wache():
    while not stopp.exists():
        time.sleep(0.05)
    stopp.unlink()
    threading.Thread(target=s.shutdown).start()
threading.Thread(target=wache, daemon=True).start()
if fassung.endswith("-absturz"):
    threading.Timer(1.0, lambda: os._exit(3)).start()
print("Uvicorn running", flush=True)
s.serve_forever()
'''


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ErsatzInstallation:
    """Wie motor.Installation, nur ohne Download: „tauscht“ die Fassungsdatei."""
    neu_inhalt = None  # was nach dem Tausch in der Fassungsdatei steht (Standard: die Fassung)
    scheitern = False

    def __init__(self, fassung, testmodus=False, backend="cuda", alt_behalten=False, **_):
        self.fassung, self.alt_behalten = fassung, alt_behalten
        self.vor_tausch = None
        self.phase, self.fehler, self.anteil, self.weg = "start", "", 0.0, "gleich"
        self.protokoll = []

    def stand_festhalten(self, ziel: Path):
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(_datei().read_text())

    def starten(self):
        def lauf():
            try:
                self.vor_tausch()
                if self.scheitern:
                    raise RuntimeError("Tausch gescheitert")
                _datei().write_text(type(self).neu_inhalt or self.fassung)
                self.phase = "fertig"
            except Exception as e:  # noqa: BLE001
                self.phase, self.fehler = "fehler", getattr(e, "code", "unerwartet")
        t = threading.Thread(target=lauf)
        t.start()
        return t

    def zurueck(self, vorher: Path):
        self.protokoll.append("zurueck")
        _datei().write_text(vorher.read_text())

    def alt_entfernen(self):
        self.protokoll.append("alt_entfernt")


_DATEI = {}


def _datei() -> Path:
    return _DATEI["fassung"]


@pytest.fixture()
def umgebung(tmp_path, monkeypatch):
    from taleward_worker import einzelplatz, motor, serverupdate
    from taleward_worker.einstellungen import Einstellungen

    skript = tmp_path / "ersatz.py"
    skript.write_text(ERSATZ, encoding="utf-8")
    port = _freier_port()
    _DATEI["fassung"] = tmp_path / "fassung.txt"
    _datei().write_text("0.4.97")
    monkeypatch.setenv("ERSATZ_PORT", str(port))
    monkeypatch.setenv("ERSATZ_FASSUNG", str(_datei()))
    monkeypatch.setenv("ERSATZ_BUSY", str(tmp_path / "busy.txt"))
    monkeypatch.setattr(einzelplatz, "ADRESSE", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(einzelplatz, "WARTEZEITEN", [0.2, 0.2])
    monkeypatch.setattr(einzelplatz, "START_FRIST_S", 8)
    monkeypatch.setattr(serverupdate, "STABIL_S", 2)
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": _datei().read_text().split("-")[0]})
    monkeypatch.setattr(motor, "neueste_fassung", lambda mindestens=None: "0.4.98")
    ErsatzInstallation.neu_inhalt, ErsatzInstallation.scheitern = None, False
    installationen = []

    def neue_installation(*a, **k):
        i = ErsatzInstallation(*a, **k)
        installationen.append(i)
        return i
    monkeypatch.setattr(motor, "Installation", neue_installation)

    protokoll = []
    dienst = SimpleNamespace(zustand={"art": "warte"}, protokoll=SimpleNamespace(schreiben=protokoll.append),
                             stoppen=lambda: None)
    e = Einstellungen(betrieb="einzelplatz", konto_angelegt=True, motor_fassung="0.4.97")
    srv = einzelplatz.ServerDienst(befehl=[sys.executable, str(skript)])
    app = SimpleNamespace(e=e, server=srv, dienst=dienst, installation=None)
    u = serverupdate.ServerUpdate(app)
    aufrufe = []

    def cli(*argumente):
        aufrufe.append(argumente)
        if argumente[0] == "sicherung":
            if (tmp_path / "sicherung_scheitert").exists():
                return SimpleNamespace(returncode=1, stdout="", stderr="Platte voll")
            datei = tmp_path / "taleward-sicherung-20261014-200000-manuell.zip"
            datei.write_text("db")
            return SimpleNamespace(returncode=0, stdout=f"Sicherung angelegt: {datei}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="Wiederhergestellt.", stderr="")
    u.cli = cli
    srv.starten()
    assert srv.warten_bis_bereit(10)
    yield SimpleNamespace(u=u, app=app, srv=srv, aufrufe=aufrufe, protokoll=protokoll, tmp=tmp_path,
                          installationen=installationen)
    srv.stoppen(warten=3)


def _warten(u, frist=30):
    ende = time.monotonic() + frist
    while u.laeuft() and time.monotonic() < ende:
        time.sleep(0.1)
    assert not u.laeuft()


def test_update_mit_sicherung_und_pruefung(umgebung):
    u, app = umgebung.u, umgebung.app
    assert u.pruefen() == "0.4.98"
    u.starten("0.4.98")
    _warten(u)
    assert u.phase == "fertig", umgebung.protokoll
    assert app.e.motor_fassung == "0.4.98" and app.e.update_gescheitert == ""
    assert umgebung.aufrufe == [("sicherung",)]  # gesichert mit der alten Fassung, nichts zurückgespielt
    assert umgebung.installationen[0].alt_behalten
    assert umgebung.installationen[0].protokoll == ["alt_entfernt"]
    assert umgebung.srv.warten_bis_bereit(5) and u.angebot is None


def test_absturz_nach_dem_start_nimmt_das_update_zurueck(umgebung):
    u, app = umgebung.u, umgebung.app
    ErsatzInstallation.neu_inhalt = "0.4.98-absturz"
    u.pruefen()
    u.starten("0.4.98")
    _warten(u)
    assert u.phase == "zurueckgenommen", umgebung.protokoll
    assert app.e.update_gescheitert == "0.4.98" and app.e.motor_fassung == "0.4.97"
    assert umgebung.installationen[0].protokoll == ["zurueck"]
    sicherung = str(umgebung.tmp / "taleward-sicherung-20261014-200000-manuell.zip")
    assert umgebung.aufrufe == [("sicherung",), ("wiederherstellen", sicherung, "--ja")]
    assert umgebung.srv.warten_bis_bereit(10)  # alte Fassung läuft wieder
    assert _datei().read_text() == "0.4.97"
    # nicht wieder von selbst – erst „Jetzt aktualisieren“
    u._naechste = 0
    u.wenn_faellig()
    assert not u.laeuft() and len(umgebung.installationen) == 1


def test_falsche_fassung_gilt_als_gescheitert(umgebung):
    u = umgebung.u
    ErsatzInstallation.neu_inhalt = "0.4.97"  # „getauscht“, aber es läuft die alte
    u.pruefen()
    u.starten("0.4.98")
    _warten(u)
    assert u.phase == "zurueckgenommen"
    assert any("erwartet 0.4.98" in z for z in umgebung.protokoll)


def test_ohne_sicherung_kein_tausch(umgebung):
    u, app = umgebung.u, umgebung.app
    (umgebung.tmp / "sicherung_scheitert").write_text("1")
    u.pruefen()
    u.starten("0.4.98")
    _warten(u)
    assert u.phase == "fehler" and _datei().read_text() == "0.4.97"
    assert umgebung.installationen[0].protokoll == []  # nichts getauscht, nichts zurückzunehmen
    assert app.e.update_gescheitert == ""  # später noch einmal versuchen
    assert umgebung.srv.warten_bis_bereit(10)
    assert u._wieder_ab > time.monotonic()  # aber nicht gleich wieder


def test_nur_wenn_nichts_laeuft(umgebung):
    u, app = umgebung.u, umgebung.app
    u._naechste = 0
    (umgebung.tmp / "busy.txt").write_text("upload")
    assert u.beschaeftigt() == "upload"
    u.wenn_faellig()
    assert u.angebot == "0.4.98" and not u.laeuft() and not umgebung.installationen
    assert u.jetzt() == {"ok": False, "grund": "upload"}
    (umgebung.tmp / "busy.txt").unlink()
    app.dienst.zustand = {"art": "arbeitet"}
    assert u.beschaeftigt() == "job"
    app.dienst.zustand = {"art": "warte"}
    app.e.auto_update = False
    u.wenn_faellig()
    assert not u.laeuft() and not umgebung.installationen  # ohne „automatisch“ nur auf Knopfdruck
    assert u.jetzt() == {"ok": True}
    _warten(u)
    assert u.phase == "fertig"


def test_ohne_konto_und_ohne_neuere_fassung_nichts(umgebung, monkeypatch):
    from taleward_worker import motor

    u, app = umgebung.u, umgebung.app
    monkeypatch.setattr(motor, "neueste_fassung", lambda mindestens=None: mindestens)
    assert u.pruefen() is None
    assert u.jetzt() == {"ok": False, "grund": "aktuell"}
    app.e.konto_angelegt = False
    u._naechste = 0
    u.wenn_faellig()
    assert u._naechste == 0  # vor dem ersten Konto wird gar nicht erst nachgesehen


def test_oberflaeche_zeigt_den_stand(umgebung):
    d = umgebung.u.fuer_oberflaeche()
    assert set(d) == {"phase", "angebot", "alt", "neu", "gescheitert", "anteil"}
    json.dumps(d)
