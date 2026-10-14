"""„Alles auf diesem PC“: Server starten und überwachen, Konto über den Startschlüssel, Programmfenster-Regeln."""
import json
import socket
import sys
import time

import httpx
import pytest

# Ein kleiner Ersatz-Server, der sich verhält wie `chronik einzelplatz` (Server ab 0.4.96)
ERSATZ = r'''
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

modus = os.environ.get("ERSATZ_MODUS", "")
zaehler = Path(os.environ["ERSATZ_ZAEHLER"])
n = int(zaehler.read_text()) if zaehler.exists() else 0
zaehler.write_text(str(n + 1))
if modus == "belegt":
    print("ERROR: [Errno 98] error while attempting to bind: address already in use", flush=True)
    sys.exit(1)
if modus == "absturz" and n == 0:
    sys.exit(3)
schluessel = None
datei = os.environ.get("TALEWARD_STARTSCHLUESSEL_DATEI")
if datei:
    schluessel = Path(datei).read_text(encoding="utf-8").strip()
    Path(datei).unlink()
Path(os.environ["EINZELPLATZ_WORKER_DATEI"]).write_text(f"wk.ersatz.{n}\n", encoding="utf-8")
port = int(os.environ["ERSATZ_PORT"])
stopp = Path(os.environ["EINZELPLATZ_STOPP_DATEI"])

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _json(self, code, daten):
        roh = json.dumps(daten).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh))); self.end_headers(); self.wfile.write(roh)
    def do_GET(self):
        self._json(200 if self.path == "/api/v1/health" else 404, {"status": "ok"})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/verwaltung/einzelplatz/konto" or not schluessel or body.get("schluessel") != schluessel:
            return self._json(404, {"code": "not_found"})
        if len(body.get("passwort", "")) < 8:
            return self._json(400, {"code": "password_too_short"})
        self._json(201, {"anmeldename": "anna"})

s = HTTPServer(("127.0.0.1", port), H)
def wache():
    while not stopp.exists():
        time.sleep(0.05)
    stopp.unlink()
    print("Shutting down", flush=True)
    threading.Thread(target=s.shutdown).start()
threading.Thread(target=wache, daemon=True).start()
print("Uvicorn running", flush=True)
s.serve_forever()
'''


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def ersatz(tmp_path, monkeypatch):
    from taleward_worker import einzelplatz

    skript = tmp_path / "ersatz.py"
    skript.write_text(ERSATZ, encoding="utf-8")
    port = _freier_port()
    monkeypatch.setenv("ERSATZ_PORT", str(port))
    monkeypatch.setenv("ERSATZ_ZAEHLER", str(tmp_path / "zaehler"))
    monkeypatch.setattr(einzelplatz, "ADRESSE", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(einzelplatz, "WARTEZEITEN", [0.2, 0.2])
    dienste = []

    def neu():
        d = einzelplatz.ServerDienst(befehl=[sys.executable, str(skript)])
        dienste.append(d)
        return d
    yield neu
    for d in dienste:
        d.stoppen(warten=3)


def test_start_konto_und_geordnetes_ende(ersatz, monkeypatch):
    from taleward_worker import pfade

    d = ersatz()
    gekoppelt = []
    d.bei_bereit = lambda: gekoppelt.append(d.worker_token())
    d.starten(startschluessel=True)
    assert d.warten_bis_bereit(15)
    assert d.zustand["art"] == "laeuft"
    assert not pfade.startschluessel().exists()  # der Server hat sie gelesen und gelöscht
    for _ in range(50):
        if gekoppelt:
            break
        time.sleep(0.05)
    assert gekoppelt == ["wk.ersatz.0"]
    # Der Schlüssel steht in keiner Zeile des Protokolls und nicht in der Umgebung des Servers
    schluessel = d._schluessel
    assert schluessel and len(schluessel) >= 43
    assert all(schluessel not in z for z in d.zeilen)
    assert schluessel not in json.dumps(d.umgebung())
    assert d.fuer_oberflaeche()["kontoMoeglich"]
    assert d.konto_anlegen("Anna", "kurz") == {"ok": False, "fehler": "password_too_short"}
    assert d.konto_anlegen("Anna", "Lindwurm-Kessel-7") == {"ok": True, "anmeldename": "anna"}
    assert d.konto_anlegen("Anna", "Lindwurm-Kessel-7")["fehler"] == "kein_schluessel"
    t0 = time.monotonic()
    d.stoppen()
    assert time.monotonic() - t0 < 5 and not d.laeuft() and d.zustand["art"] == "gestoppt"
    assert any("Shutting down" in z for z in d.zeilen)  # über die Stoppdatei, nicht hart beendet


def test_ohne_konto_kein_schluessel_mehr(ersatz):
    from taleward_worker import pfade

    d = ersatz()
    d.starten(startschluessel=False)
    assert d.warten_bis_bereit(15)
    assert "TALEWARD_STARTSCHLUESSEL_DATEI" not in d.umgebung()
    assert not pfade.startschluessel().exists()
    assert d.konto_anlegen("Anna", "Lindwurm-Kessel-7")["fehler"] == "kein_schluessel"


def test_neustart_nach_absturz_mit_frischem_worker_schluessel(ersatz, monkeypatch):
    monkeypatch.setenv("ERSATZ_MODUS", "absturz")
    d = ersatz()
    gekoppelt = []
    d.bei_bereit = lambda: gekoppelt.append(d.worker_token())
    d.starten()
    assert d.warten_bis_bereit(15)
    for _ in range(50):
        if gekoppelt:
            break
        time.sleep(0.05)
    assert gekoppelt == ["wk.ersatz.1"]  # zweiter Start, neuer Schlüssel
    assert any("unerwartet beendet" in z for z in d.zeilen)


def test_belegter_anschluss_wird_erkannt(ersatz, monkeypatch):
    monkeypatch.setenv("ERSATZ_MODUS", "belegt")
    d = ersatz()
    d.starten()
    for _ in range(100):
        if d.zustand["art"] == "fehler":
            break
        time.sleep(0.05)
    assert d.zustand == {**d.zustand, "art": "fehler", "code": "anschluss_belegt"}
    assert not d.warten_bis_bereit(0.5)


def test_umgebung_des_servers(monkeypatch):
    from taleward_worker import einzelplatz, pfade

    monkeypatch.setenv("DATABASE_URL", "sqlite:///fremd.db")
    monkeypatch.setenv("TALEWARD_STARTSCHLUESSEL", "von-aussen-" + "x" * 40)
    monkeypatch.setenv("PUBLIC_URL", "https://fremd.example")
    env = einzelplatz.ServerDienst(befehl=["x"]).umgebung()
    assert env["TALEWARD_EINZELPLATZ"] == "1" and env["DATA_DIR"] == str(pfade.einzelplatz_daten())
    assert env["EINZELPLATZ_WORKER_DATEI"] == str(pfade.worker_schluessel())
    assert env["LOCAL_WORKER_AUTOSTART"] == "false"
    for name in ("DATABASE_URL", "TALEWARD_STARTSCHLUESSEL", "PUBLIC_URL", "TALEWARD_STARTSCHLUESSEL_DATEI"):
        assert name not in env


# ---------------------------------------------------------------- Neueste freigegebene Fassung
def test_neueste_fassung(monkeypatch):
    from taleward_worker import freigabe, motor

    freigegeben = {"v0.4.95", "v0.4.94"}
    geprueft = []

    def von_github(repo, tag, user_agent=""):
        geprueft.append(tag)
        if tag not in freigegeben:
            raise freigabe.FreigabeFehler("nicht freigegeben")
        return freigabe.Freigabe(repo, tag, "a" * 40)
    monkeypatch.setattr(freigabe, "von_github", von_github)
    releases = [{"tag_name": "v0.4.96"}, {"tag_name": "v0.4.95"}, {"tag_name": "v0.4.94"},
                {"tag_name": "v0.5.0", "prerelease": True}, {"tag_name": "worker-v9.9.9"}, {"tag_name": "v0.4.100",
                                                                                         "draft": True}]
    k = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=releases)))
    assert motor.neueste_fassung(k) == "0.4.95"  # 0.4.96 ist noch nicht freigegeben
    assert geprueft == ["v0.4.96", "v0.4.95"]
    # nie unter die laufende Fassung
    assert motor.neueste_fassung(k, mindestens="0.4.97") == "0.4.97"
    freigegeben.clear()
    with pytest.raises(motor.MotorFehler) as e:
        motor.neueste_fassung(k)
    assert e.value.code == "nicht_freigegeben"


def test_neueste_fassung_ohne_netz():
    from taleward_worker import motor

    def kaputt(r):
        raise httpx.ConnectError("weg")
    k = httpx.Client(transport=httpx.MockTransport(kaputt))
    with pytest.raises(motor.MotorFehler) as e:
        motor.neueste_fassung(k)
    assert e.value.code == "github_nicht_erreichbar"


def test_betriebsart_mit_rueckfall():
    from taleward_worker.einstellungen import Einstellungen

    assert Einstellungen().art == ""
    assert Einstellungen(server="https://x.example", token="wk.a.b").art == "verbunden"
    assert Einstellungen(server="https://x.example").art == ""  # entkoppelt: wieder die Wahl
    e = Einstellungen(betrieb="einzelplatz")
    assert e.art == "einzelplatz" and e.einzelplatz and e.fuer_oberflaeche()["art"] == "einzelplatz"


def test_einzelplatz_braucht_neue_serverfassung(monkeypatch):
    from taleward_worker import motor

    monkeypatch.setattr(motor, "neueste_fassung", lambda: "0.4.95")
    inst = motor.Installation("", neueste=True)
    inst._lauf()
    assert inst.phase == "fehler" and inst.fehler == "einzelplatz_zu_alt"


def test_oberflaeche_wahl_und_konto(monkeypatch):
    from taleward_worker import einzelplatz, pfade
    from taleward_worker.oberflaeche import Api, WorkerApp

    app = WorkerApp()
    api = Api(app)
    assert api.zustand()["taleward"] is None
    assert api.betrieb_waehlen("irgendwas") == {"ok": False}
    assert api.betrieb_waehlen("einzelplatz") == {"ok": True} and app.e.einzelplatz
    assert api.zustand()["taleward"]["zustand"]["art"] == "gestoppt"
    assert api.konto_anlegen("", "Lindwurm-Kessel-7", "Lindwurm-Kessel-7")["fehler"] == "name_missing"
    assert api.konto_anlegen("Anna", "kurz", "kurz")["fehler"] == "password_too_short"
    assert api.konto_anlegen("Anna", "Lindwurm-Kessel-7", "anders-1234")["fehler"] == "passwoerter_ungleich"
    assert api.konto_anlegen("Anna", "Lindwurm-Kessel-7", "Lindwurm-Kessel-7")["fehler"] == "startet_noch"
    # Server bereit, Konto gelingt → gemerkt, ohne Passwort zu speichern
    app.server.bereit.set()
    monkeypatch.setattr(app.server, "konto_anlegen", lambda n, p, b=None: {"ok": True, "anmeldename": "anna"})
    assert api.konto_anlegen("Anna", "Lindwurm-Kessel-7", "Lindwurm-Kessel-7") == {"ok": True, "anmeldename": "anna"}
    assert app.e.konto_angelegt and app.e.anmeldename == "anna"
    assert "Lindwurm" not in pfade.einstellungen().read_text(encoding="utf-8")
    # Worker koppelt sich mit dem Schlüssel aus der Datei
    gestartet = []
    monkeypatch.setattr(app.dienst, "starten", lambda: gestartet.append(1))
    monkeypatch.setattr(app, "fassung_abfragen", lambda: None)
    pfade.worker_schluessel().write_text("wk.abc.def\n", encoding="utf-8")
    app._worker_koppeln()
    assert (app.e.server, app.e.token, gestartet) == (einzelplatz.ADRESSE, "wk.abc.def", [1])
    # Neu koppeln gibt es im Einzelplatz nicht
    api.entkoppeln()
    assert app.e.token == "wk.abc.def"
    assert api.betrieb_waehlen("verbunden") == {"ok": False}
