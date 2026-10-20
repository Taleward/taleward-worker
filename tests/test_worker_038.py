"""Worker 0.3.8: Programmfenster ohne alte Seiten, Entwickleransicht nur auf Wunsch, Warten auf das Sprechermodell,
ein Fenster für „Alles auf diesem PC“."""
import json
import sys
import time
import types

from taleward_worker.dienst import VORSILBE, Dienst
from taleward_worker.einstellungen import Einstellungen


def _warten(bedingung, frist=6.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return False


def test_zwischenspeicher_nur_nach_fassungswechsel_und_nur_der_seiten(tmp_path):
    from taleward_worker import fenster

    profil = tmp_path / "EBWebView" / "Default"
    for name in ("Cache", "Code Cache", "GPUCache", "Local Storage", "IndexedDB"):
        (profil / name).mkdir(parents=True)
        (profil / name / "datei").write_text("x")
    assert fenster.zwischenspeicher_leeren(tmp_path, "0.3.8")
    assert not (profil / "Cache").exists() and not (profil / "Code Cache").exists()
    assert (profil / "Local Storage" / "datei").exists() and (profil / "IndexedDB" / "datei").exists()
    (profil / "Cache").mkdir()
    assert not fenster.zwischenspeicher_leeren(tmp_path, "0.3.8")  # gleiche Fassung: nichts anfassen
    assert (profil / "Cache").exists()
    assert fenster.zwischenspeicher_leeren(tmp_path, "0.3.9") and not (profil / "Cache").exists()


def test_freier_anschluss_wechselt():
    from taleward_worker import fenster

    anschluesse = {fenster.freier_anschluss() for _ in range(5)}
    assert all(1024 <= a <= 65535 for a in anschluesse) and len(anschluesse) > 1
    assert 42001 not in anschluesse or len(anschluesse) > 1


def test_entwickleransicht_nur_mit_taleward_debug(monkeypatch):
    from taleward_worker import fenster

    from tests.test_programmfenster import _webview2_attrappe

    for debug, erwartet in ((None, False), ("1", True)):
        core, form = _webview2_attrappe(monkeypatch)
        monkeypatch.setattr(fenster.sys, "platform", "win32")
        if debug:
            monkeypatch.setenv("TALEWARD_DEBUG", debug)
        else:
            monkeypatch.delenv("TALEWARD_DEBUG", raising=False)
        w = types.SimpleNamespace(native=form)
        fenster.abdichten(w, "http://localhost:51234", "http://localhost:51234/desktop.html")
        assert core.Settings.AreDevToolsEnabled is erwartet


def test_fehlt_nur_das_sprechermodell_versucht_der_worker_es_bald_wieder():
    meldung = {"ereignis": "fehler", "message": "Das Sprechermodell liegt noch nicht auf dem Server, und hier ist kein "
                                           "Hugging-Face-Zugang eingetragen."}
    skript = f"print({VORSILBE + json.dumps(meldung)!r}, flush=True)"
    d = Dienst(Einstellungen(server="x", token="t"), befehl=[sys.executable, "-c", skript])
    d.starten()
    try:
        assert _warten(lambda: d.zustand.get("art") == "fehler" and d.zustand.get("neuerVersuch"))
        assert d.zustand["code"] == "modell"
        assert 30 < d.zustand["neuerVersuch"] - time.time() <= 61
    finally:
        d.stoppen()


def test_anderer_einrichtungsfehler_wartet_laenger():
    meldung = {"ereignis": "fehler", "message": "PyTorch sieht keine Grafikkarte."}
    skript = f"print({VORSILBE + json.dumps(meldung)!r}, flush=True)"
    d = Dienst(Einstellungen(server="x", token="t"), befehl=[sys.executable, "-c", skript])
    d.starten()
    try:
        assert _warten(lambda: d.zustand.get("art") == "fehler" and d.zustand.get("neuerVersuch"))
        assert d.zustand["code"] == "einrichtung" and d.zustand["neuerVersuch"] - time.time() > 300
    finally:
        d.stoppen()


def _app(monkeypatch, **einstellungen):
    from taleward_worker import hardware
    from taleward_worker.oberflaeche import Api, WorkerApp

    monkeypatch.setattr(hardware, "bericht", lambda: {"grafikkarte": None})
    app = WorkerApp()
    for k, v in einstellungen.items():
        setattr(app.e, k, v)
    return app, Api(app)


def test_verwaltung_nur_bei_alles_auf_diesem_pc(monkeypatch):
    app, api = _app(monkeypatch, betrieb="verbunden", server="https://taleward.example", token="wk.1.x")
    gezeigt = []
    monkeypatch.setattr(app, "bereich_zeigen", lambda b: gezeigt.append(b))
    app.verwaltung_oeffnen()
    assert gezeigt == [] and api.verwaltung_adresse() == {"ok": False}
    app.e.betrieb = "einzelplatz"
    app.verwaltung_oeffnen()
    assert gezeigt == ["verwaltung"]  # im Fenster, nicht im Browser
    assert api.verwaltung_adresse() == {"ok": True, "adresse": "http://localhost:8000/verwaltung/"}
    assert api.verwaltung_adresse("transkription#hf")["adresse"] == "http://localhost:8000/verwaltung/transkription#hf"
    assert api.verwaltung_adresse("https://boese.example/") == {"ok": False}


def test_taleward_oeffnen_wechselt_zu_taleward_desktop(monkeypatch):
    from taleward_worker import motor

    app, api = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=False)
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.112"})
    angewendet = []
    monkeypatch.setattr(app, "ansicht_anwenden", lambda: angewendet.append(app.ansicht_soll()))
    monkeypatch.setattr(app, "bereich_zeigen", lambda b: angewendet.append(b))
    assert api.taleward_oeffnen() == {"ok": False} and angewendet == []  # noch kein Konto: kleines Fenster bleibt
    app.e.konto_angelegt = True
    assert api.taleward_oeffnen() == {"ok": True} and angewendet == ["desktop", "kampagnen"]


def test_hilfe_noetig_holt_das_programm_nach_vorn(monkeypatch):
    app, _ = _app(monkeypatch, betrieb="verbunden")
    app.dienst.zustand = {"art": "fehler", "code": "modell"}
    assert not app.braucht_hilfe()  # wartet nur auf das Modell
    app.dienst.zustand = {"art": "fehler", "code": "einrichtung"}
    assert app.braucht_hilfe()
    app.dienst.zustand = {"art": "warte"}
    assert not app.braucht_hilfe()


def test_dokumente_ordner(monkeypatch, tmp_path):
    from taleward_worker import pfade

    monkeypatch.setattr(pfade, "WINDOWS", False)
    monkeypatch.setattr(pfade.Path, "home", classmethod(lambda cls: tmp_path))
    assert pfade.dokumente() == tmp_path
    (tmp_path / "Documents").mkdir()
    assert pfade.dokumente() == tmp_path / "Documents"
