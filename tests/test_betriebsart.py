"""Betriebsart wechseln, in beide Richtungen (ab Worker 0.3.4)."""
import time
from types import SimpleNamespace


def _warten(bedingung, frist=5.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.02)
    return bedingung()


def _app(monkeypatch, **einstellungen):
    from taleward_worker import hardware
    from taleward_worker.oberflaeche import Api, WorkerApp

    monkeypatch.setattr(hardware, "bericht", lambda: {"grafikkarte": None})
    app = WorkerApp()
    for k, v in einstellungen.items():
        setattr(app.e, k, v)
    app.e.speichern()
    return app, Api(app)


def test_wechsel_von_server_zu_alles_auf_diesem_pc(monkeypatch):
    from taleward_worker import motor

    app, api = _app(monkeypatch, betrieb="verbunden", server="https://verein.invalid", token="wk.1.x")
    gestoppt, installiert, gestartet = [], [], []
    monkeypatch.setattr(app.dienst, "stoppen", lambda *a, **k: gestoppt.append(1))
    monkeypatch.setattr(app, "installieren", lambda testmodus: installiert.append(testmodus))
    monkeypatch.setattr(app, "taleward_starten", lambda: gestartet.append(1))
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.93"})  # für den Verein, zu alt
    assert api.betrieb_wechseln("verbunden") == {"ok": False, "fehler": "unbekannt"}  # ist schon so
    assert api.betrieb_wechseln("einzelplatz") == {"ok": True}
    assert _warten(lambda: app.e.einzelplatz)
    assert _warten(lambda: installiert == [False])
    assert gestoppt and not app.e.token and not gestartet
    # KI-Paket neu genug → Taleward startet gleich
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.104"})
    app.e.betrieb = "verbunden"
    app.betrieb_wechseln("einzelplatz")
    assert gestartet == [1]


def test_wechsel_zurueck_zum_server_behaelt_die_kampagnen(monkeypatch):
    from taleward_worker import pfade

    app, api = _app(monkeypatch, betrieb="einzelplatz", server="http://127.0.0.1:8000", token="wk.1.x",
                    konto_angelegt=True, anmeldename="anna")
    (pfade.einzelplatz_daten() / "chronik.db").write_text("kampagnen")
    gestoppt = []
    monkeypatch.setattr(app.dienst, "stoppen", lambda *a, **k: gestoppt.append("worker"))
    monkeypatch.setattr(app.server, "stoppen", lambda *a, **k: gestoppt.append("taleward"))
    app.betrieb_wechseln("verbunden")
    assert gestoppt == ["worker", "taleward"]
    assert (app.e.art, app.e.server, app.e.token, app.e.gekoppelt) == ("verbunden", "", "", False)
    assert app.e.konto_angelegt  # beim Zurückwechseln ist alles wieder da
    assert (pfade.einzelplatz_daten() / "chronik.db").read_text() == "kampagnen"


def test_kein_wechsel_mitten_in_der_arbeit(monkeypatch):
    app, api = _app(monkeypatch, betrieb="verbunden", server="https://verein.invalid", token="wk.1.x")
    app.dienst.zustand = {"art": "arbeitet"}
    assert api.betrieb_wechseln("einzelplatz") == {"ok": False, "fehler": "arbeitet"}
    app.dienst.zustand = {"art": "warte"}
    app.installation = SimpleNamespace(phase="pakete")
    assert api.betrieb_wechseln("einzelplatz") == {"ok": False, "fehler": "beschaeftigt"}
    assert app.e.art == "verbunden"


def test_neu_koppeln_bleibt_beim_server(monkeypatch):
    app, api = _app(monkeypatch, betrieb="verbunden", server="https://verein.invalid", token="wk.1.x")
    monkeypatch.setattr(app.dienst, "stoppen", lambda *a, **k: None)
    api.entkoppeln()
    assert app.e.art == "verbunden" and not app.e.gekoppelt and app.e.server == "https://verein.invalid"


def test_alles_auf_diesem_pc_installiert_nie_eine_zu_alte_fassung(monkeypatch):
    from taleward_worker import motor

    app, api = _app(monkeypatch, betrieb="einzelplatz")
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.93"})
    monkeypatch.setattr(motor, "platz_fehlt_gb", lambda *a: 0)
    erzeugt = []

    class Inst:
        def __init__(self, fassung, **k):
            erzeugt.append((fassung, k.get("neueste")))
            self.phase = "start"

        def starten(self):
            pass
    monkeypatch.setattr(motor, "Installation", Inst)
    app.installieren(False)
    assert erzeugt == [("", True)]


def test_verbindung_zum_server_bleibt_ueber_den_wechsel_erhalten(monkeypatch):
    """Server → Alles auf diesem PC → Server: kein neuer Kopplungscode nötig."""
    from taleward_worker import mitnehmen, motor
    from taleward_worker.einstellungen import Einstellungen

    app, api = _app(monkeypatch, betrieb="verbunden", server="https://verein.invalid", token="wk.1.geheim",
                    unsicher=False)
    app.server_name = "Spielgruppe Nordlicht"
    monkeypatch.setattr(app.dienst, "stoppen", lambda *a, **k: None)
    monkeypatch.setattr(app, "taleward_starten", lambda: None)
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.104"})

    app.betrieb_wechseln("einzelplatz")
    assert app.e.einzelplatz and app.e.token == ""
    assert (app.e.verbunden_server, app.e.verbunden_token, app.e.verbunden_name) == (
        "https://verein.invalid", "wk.1.geheim", "Spielgruppe Nordlicht")
    # Taleward auf diesem PC koppelt seinen eigenen Worker – die beiseitegelegte Verbindung bleibt
    app.e.server, app.e.token = "http://127.0.0.1:8000", "wk.lokal"
    app.e.speichern()

    # übersteht einen Neustart des Programms, Schlüssel nie in Oberfläche oder Hilfe-Datei
    geladen = Einstellungen.laden()
    assert geladen.verbunden_token == "wk.1.geheim"
    sicht = geladen.fuer_oberflaeche()
    assert "verbunden_token" not in sicht and sicht["verbundenGemerkt"] is True
    assert "wk.1.geheim" not in str(sicht)
    assert "wk.1.geheim" not in mitnehmen.hilfe_text(app)

    geprueft = []
    monkeypatch.setattr(app, "_motor_pruefen", lambda erster: geprueft.append(erster))
    app.betrieb_wechseln("verbunden")
    assert (app.e.art, app.e.server, app.e.token, app.e.gekoppelt) == (
        "verbunden", "https://verein.invalid", "wk.1.geheim", True)
    assert not app.e.verbunden_server and not app.e.verbunden_token
    assert geprueft == [True]  # Worker startet von selbst wieder


def test_ohne_frühere_verbindung_geht_es_mit_kopplungscode_weiter(monkeypatch):
    app, api = _app(monkeypatch, betrieb="einzelplatz", server="http://127.0.0.1:8000", token="wk.lokal")
    monkeypatch.setattr(app.dienst, "stoppen", lambda *a, **k: None)
    monkeypatch.setattr(app.server, "stoppen", lambda *a, **k: None)
    app.betrieb_wechseln("verbunden")
    assert (app.e.server, app.e.token, app.e.gekoppelt) == ("", "", False)


def test_eigener_worker_wird_nie_als_server_verbindung_gemerkt(monkeypatch):
    app, api = _app(monkeypatch, betrieb="einzelplatz", server="http://127.0.0.1:8000", token="wk.lokal")
    assert app.e.verbindung_merken("x") is False
    assert not app.e.verbunden_token
