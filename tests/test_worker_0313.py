"""Worker 0.3.13: Taleward Desktop – ein Fenster. Die Oberfläche (Hülle) zeigt App und Verwaltung in Rahmen.

Geprüft wird, was die Sicherheit trägt: Die Brücke nimmt nur Nachrichten der Hülle und nur Namen der Api an, neue
Fenster gehen nie ins Programm, Mikrofon nur für die App, die Hülle liefert nur feste Dateien mit strenger CSP und bindet
beide Adressen, Folgenreiches braucht den Prüfwert.
"""
import json
import re
import socket
import sys
import types
from pathlib import Path

import pytest

HERKUNFT = "http://localhost:51234"
UI = Path(__file__).resolve().parent.parent / "taleward_worker" / "ui"


# ---------------------------------------------------------------- Herkunft und Nachrichten
@pytest.mark.parametrize("uri,ok", [
    ("http://localhost:51234/desktop.html", True), ("http://localhost:51234/", True), ("http://localhost:51234", True),
    ("http://localhost:51234/index.html?x#y", True),
    ("http://127.0.0.1:51234/desktop.html", False), ("http://[::1]:51234/", False), ("http://localhost:8000/app/", False),
    ("http://localhost:51235/", False), ("https://localhost:51234/", False), ("http://user@localhost:51234/", False),
    ("http://localhost.boese.example:51234/", False), ("about:blank", False), ("", False), (None, False),
    ("http://localhost:51234\\@boese.example/", False), ("data:text/html,x", False),
])
def test_von_huelle(uri, ok):
    from taleward_worker.fenster import von_huelle

    assert von_huelle(uri, HERKUNFT) is ok


def _nachricht(name, params="[]", kennung="1"):
    return json.dumps([name, params, kennung])


@pytest.mark.parametrize("nachricht,quelle,ok", [
    (_nachricht("zustand"), HERKUNFT + "/desktop.html", True),
    (_nachricht("beenden"), HERKUNFT + "/index.html", True),
    (_nachricht("zustand"), "http://localhost:8000/app/#/schirm", False),        # die App im Rahmen
    (_nachricht("zustand"), "http://localhost:8000/verwaltung/", False),         # die Verwaltung im Rahmen
    (_nachricht("zustand"), "http://127.0.0.1:51234/desktop.html", False),
    (_nachricht("_app.beenden"), HERKUNFT + "/desktop.html", False),             # über Attribute ins Programm
    (_nachricht("_app"), HERKUNFT + "/desktop.html", False),
    (_nachricht("zustand.__class__"), HERKUNFT + "/desktop.html", False),
    (_nachricht("_pywebviewAlert"), HERKUNFT + "/desktop.html", False),
    (_nachricht("pywebviewMoveWindow"), HERKUNFT + "/desktop.html", False),
    (_nachricht("pywebviewStateUpdate"), HERKUNFT + "/desktop.html", False),
    (_nachricht("unbekannt"), HERKUNFT + "/desktop.html", False),
    ('"FilesDropped"', HERKUNFT + "/desktop.html", False),
    ("kein json", HERKUNFT + "/desktop.html", False),
    (json.dumps(["zustand", [], "1"]), HERKUNFT + "/desktop.html", False),
])
def test_nachricht_erlaubt(nachricht, quelle, ok):
    from taleward_worker import fenster
    from taleward_worker.oberflaeche import Api

    assert fenster.nachricht_erlaubt(nachricht, quelle, HERKUNFT, fenster.api_namen(Api)) is ok


def test_api_namen_ohne_interna():
    from taleward_worker import fenster
    from taleward_worker.oberflaeche import Api

    namen = fenster.api_namen(Api)
    assert {"zustand", "beenden", "betrieb_wechseln", "verarbeitung", "verwaltung_adresse"} <= namen
    assert not any(n.startswith("_") for n in namen) and "_gilt" not in namen and "_ollama_entfernen" not in namen


def _edge_attrappe(monkeypatch):
    """pywebviews WebView2-Klasse, so weit sie hier zählt (Ereignisse hängt sie beim Anlegen an)."""
    aufgerufen, geoeffnet = [], []

    class EdgeChrome:
        def __init__(self):
            self.empfaenger = self.on_script_notify  # wie pywebview: WebMessageReceived += self.on_script_notify
            self.neue_fenster = self.on_new_window_request

        def on_script_notify(self, _sender, args):
            aufgerufen.append(args.get_WebMessageAsJson())

        def on_new_window_request(self, _sender, args):
            raise AssertionError("pywebviews eigener Weg darf nicht mehr laufen")

    edge = types.ModuleType("webview.platforms.edgechromium")
    edge.EdgeChrome = EdgeChrome
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", edge)
    import webview.platforms

    monkeypatch.setattr(webview.platforms, "edgechromium", edge, raising=False)
    from taleward_worker import fenster

    monkeypatch.setattr(fenster.sys, "platform", "win32")
    monkeypatch.setattr(fenster, "nur_web_oeffnen", lambda url, *a: geoeffnet.append(url) or True)
    monkeypatch.setattr(fenster.threading, "Thread", lambda target, args, daemon: types.SimpleNamespace(
        start=lambda: target(*args)))
    return EdgeChrome, aufgerufen, geoeffnet


class _Nachricht:
    def __init__(self, quelle, inhalt):
        self.Source, self._inhalt = quelle, inhalt

    def get_WebMessageAsJson(self):  # noqa: N802
        return self._inhalt


def test_bruecke_nimmt_nur_die_huelle_an(monkeypatch, caplog):
    from taleward_worker import fenster
    from taleward_worker.oberflaeche import Api

    EdgeChrome, aufgerufen, _ = _edge_attrappe(monkeypatch)
    assert fenster.bruecke_absichern(HERKUNFT, fenster.api_namen(Api))
    e = EdgeChrome()
    e.empfaenger(None, _Nachricht(HERKUNFT + "/desktop.html", _nachricht("zustand")))
    with caplog.at_level("WARNING", logger="taleward_worker"):
        e.empfaenger(None, _Nachricht("http://localhost:8000/app/", _nachricht("beenden", '["geheim"]')))
        e.empfaenger(None, _Nachricht(HERKUNFT + "/desktop.html", _nachricht("_app.beenden")))
    assert aufgerufen == [_nachricht("zustand")]
    assert "verworfen" in caplog.text and "geheim" not in caplog.text  # Protokollzeile ohne Inhalt
    assert fenster.bruecke_absichern(HERKUNFT, fenster.api_namen(Api))  # zweimal: nicht doppelt umhüllen
    e.empfaenger(None, _Nachricht(HERKUNFT + "/", _nachricht("zustand")))
    assert len(aufgerufen) == 2


def test_neue_fenster_nie_im_programm(monkeypatch):
    from taleward_worker import fenster

    EdgeChrome, _, geoeffnet = _edge_attrappe(monkeypatch)
    assert fenster.bruecke_absichern(HERKUNFT, frozenset())
    e = EdgeChrome()
    erledigt = []
    for uri in ("https://taleward.org/datenschutz", "file:///C:/Windows/System32/calc.exe", "http://localhost:51234/"):
        args = types.SimpleNamespace(set_Handled=lambda wert: erledigt.append(wert), get_Uri=lambda u=uri: u)
        e.neue_fenster(None, args)
    assert erledigt == [True, True, True]
    assert geoeffnet == ["https://taleward.org/datenschutz", "http://localhost:51234/"]  # nur http(s), im Browser


def test_unbekanntes_pywebview_startet_nicht(monkeypatch):
    from taleward_worker import fenster

    edge = types.ModuleType("webview.platforms.edgechromium")
    edge.EdgeChrome = type("EdgeChrome", (), {})
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", edge)
    import webview.platforms

    monkeypatch.setattr(webview.platforms, "edgechromium", edge, raising=False)
    monkeypatch.setattr(fenster.sys, "platform", "win32")
    assert fenster.bruecke_absichern(HERKUNFT, frozenset()) is False


def test_pywebview_aufbau_wie_erwartet():
    """Der Filter hängt an pywebviews Aufbau (6.x): Empfang beim Anlegen gebunden, Stellvertreter der Brücke aus
    _jsApiCallback/_checkValue. Ändert ein Update das, muss dieser Test scheitern."""
    import webview

    wurzel = Path(webview.__file__).parent
    edge = (wurzel / "platforms" / "edgechromium.py").read_text(encoding="utf-8")
    assert "self.webview.WebMessageReceived += self.on_script_notify" in edge
    assert "NewWindowRequested += self.on_new_window_request" in edge
    assert "FrameCreated" not in edge and "AddHostObjectToScript" not in edge
    api = (wurzel / "js" / "api.js").read_text(encoding="utf-8")
    for name in ("_jsApiCallback:", "_checkValue:", "_returnValuesCallbacks:"):
        assert name in api
