"""Schlüssel unter Windows mit dem Windows-Datenschutz (ab Worker 0.3.4)."""
import json

import pytest


@pytest.fixture()
def windows(monkeypatch):
    from taleward_worker import geschuetzt

    monkeypatch.setattr(geschuetzt, "verfuegbar", lambda: True)
    monkeypatch.setattr(geschuetzt, "schuetzen", lambda b: b"DIESER" + b[::-1])

    def oeffnen(b):
        if not b.startswith(b"DIESER"):
            raise OSError("anderer PC")
        return b[6:][::-1]
    monkeypatch.setattr(geschuetzt, "oeffnen", oeffnen)
    return geschuetzt


def test_kopplungsschluessel_geschuetzt(windows):
    from taleward_worker import pfade
    from taleward_worker.einstellungen import Einstellungen

    e = Einstellungen(server="https://beispiel.invalid", token="wk.1.geheim")
    e.speichern()
    roh = json.loads(pfade.einstellungen().read_text(encoding="utf-8"))
    assert roh["token"].startswith("dpapi:") and "geheim" not in pfade.einstellungen().read_text(encoding="utf-8")
    assert Einstellungen.laden().token == "wk.1.geheim" and e.token == "wk.1.geheim"
    # aus einer älteren Fassung (Klartext) weiter lesbar
    roh["token"] = "wk.2.alt"
    pfade.einstellungen().write_text(json.dumps(roh), encoding="utf-8")
    assert Einstellungen.laden().token == "wk.2.alt"
    # von einem anderen PC kopiert: nicht lesbar → nicht gekoppelt
    roh["token"] = "dpapi:QU5ERVJFUg=="
    pfade.einstellungen().write_text(json.dumps(roh), encoding="utf-8")
    assert Einstellungen.laden().token == "" and not Einstellungen.laden().gekoppelt


def test_ohne_windows_bleibt_der_schluessel_wie_er_ist():
    from taleward_worker import pfade
    from taleward_worker.einstellungen import Einstellungen

    Einstellungen(server="https://beispiel.invalid", token="wk.1.x").speichern()
    assert json.loads(pfade.einstellungen().read_text(encoding="utf-8"))["token"] == "wk.1.x"


def test_worker_schluessel_von_taleward_geschuetzt(windows):
    from taleward_worker import einzelplatz, pfade

    d = einzelplatz.ServerDienst()
    assert d.umgebung()["EINZELPLATZ_WORKER_GESCHUETZT"] == "true"
    pfade.worker_schluessel().write_text(windows.text_schuetzen("wk.7.frisch") + "\n", encoding="utf-8")
    assert d.worker_token() == "wk.7.frisch"
    pfade.worker_schluessel().write_text("wk.8.klartext\n", encoding="utf-8")  # Server vor 0.4.100
    assert d.worker_token() == "wk.8.klartext"
    pfade.worker_schluessel().write_text("dpapi:QU5ERVJFUg==\n", encoding="utf-8")
    assert d.worker_token() is None
    assert not __import__("taleward_worker.selbstupdate").selbstupdate.neuer("0.4.100", einzelplatz.MINDEST_SERVER)


def test_ohne_funktionierenden_datenschutz_wird_trotzdem_gespeichert(windows, monkeypatch):
    from taleward_worker.einstellungen import Einstellungen

    monkeypatch.setattr(windows, "schuetzen", lambda b: (_ for _ in ()).throw(OSError("geht nicht")))
    Einstellungen(server="https://beispiel.invalid", token="wk.1.x").speichern()
    assert Einstellungen.laden().token == "wk.1.x"
