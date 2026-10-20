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


# ---------------------------------------------------------------- WebView2-Regeln
def _core(monkeypatch):
    from tests.test_programmfenster import _webview2_attrappe

    core, form = _webview2_attrappe(monkeypatch)
    from taleward_worker import fenster

    monkeypatch.setattr(fenster.sys, "platform", "win32")
    w = types.SimpleNamespace(native=form)
    fenster.abdichten(w, HERKUNFT, HERKUNFT + "/desktop.html")
    return core


def test_hauptseite_nur_die_huelle(monkeypatch):
    from taleward_worker import fenster

    core = _core(monkeypatch)
    extern = []
    monkeypatch.setattr(fenster, "extern_oeffnen", lambda u: extern.append(u))
    for uri, erlaubt in ((HERKUNFT + "/index.html", True), ("http://localhost:8000/app/", False),
                         ("https://boese.example/", False), ("http://127.0.0.1:51234/", False)):
        args = types.SimpleNamespace(Uri=uri, Cancel=False)
        core.NavigationStarting.handler[0](None, args)
        assert args.Cancel is (not erlaubt), uri
    assert extern == ["http://localhost:8000/app/", "https://boese.example/", "http://127.0.0.1:51234/"]


def test_rahmen_nur_der_server_dieses_pcs(monkeypatch):
    core = _core(monkeypatch)
    for uri, erlaubt in (("http://localhost:8000/app/", True), ("http://127.0.0.1:8000/verwaltung/", True),
                         (HERKUNFT + "/desktop.html", False), ("https://boese.example/", False)):
        args = types.SimpleNamespace(Uri=uri, Cancel=False)
        core.FrameNavigationStarting.handler[0](None, args)
        assert args.Cancel is (not erlaubt), uri


@pytest.mark.parametrize("uri,art,erlaubt", [
    ("http://localhost:8000/app/#/aufnahme", "mic", True), ("http://localhost:8000", "mic", True),
    ("http://localhost:8000/verwaltung/", "mic", False), (HERKUNFT + "/desktop.html", "mic", False),
    ("http://localhost:8000/app/", "kamera", False), ("https://boese.example/app/", "mic", False),
])
def test_mikrofon_nur_fuer_die_app(monkeypatch, uri, art, erlaubt):
    core = _core(monkeypatch)
    args = types.SimpleNamespace(Uri=uri, PermissionKind=art, State=None)
    core.PermissionRequested.handler[0](None, args)
    assert args.State == ("allow" if erlaubt else "deny")


def test_ohne_regeln_keine_oberflaeche(monkeypatch):
    from taleward_worker import fenster

    monkeypatch.setattr(fenster.sys, "platform", "win32")
    geladen = []
    w = types.SimpleNamespace(native=None, load_url=lambda u: geladen.append(("url", u)),
                              load_html=lambda h: geladen.append(("html", h)))
    fenster.abdichten(w, HERKUNFT, HERKUNFT + "/desktop.html")  # System fehlt hier: Regeln scheitern
    assert [a for a, _ in geladen] == ["html"] and "nicht sicher" in geladen[0][1]
    fenster.abdichten(w, HERKUNFT, HERKUNFT + "/desktop.html")  # das Laden der Hinweisseite ändert nichts
    assert len(geladen) == 1


# ---------------------------------------------------------------- die Hülle (feste Dateien)
@pytest.fixture()
def huelle():
    from taleward_worker.huelle import Huelle

    h = Huelle(UI)
    h.starten()
    yield h
    h.stoppen()


def _get(h, pfad, host=None, methode="GET"):
    import httpx

    return httpx.request(methode, f"http://127.0.0.1:{h.anschluss}{pfad}",
                         headers={"Host": host or f"localhost:{h.anschluss}"})


def test_huelle_liefert_feste_dateien_mit_strenger_csp(huelle):
    from taleward_worker.huelle import CSP

    r = _get(huelle, "/desktop.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert csp == CSP
    for teil in ("default-src 'self'", "script-src 'self'", "frame-src http://localhost:8000;", "object-src 'none'",
                 "base-uri 'none'", "frame-ancestors 'none'"):
        assert teil in csp
    assert "unsafe" not in csp and "*" not in csp
    assert "set-cookie" not in r.headers and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert huelle.herkunft == f"http://localhost:{huelle.anschluss}"


@pytest.mark.parametrize("pfad,status", [
    ("/", 200), ("/index.html", 200), ("/bruecke.js", 200), ("/fonts/OFL.txt", 200), ("/marke/favicon.ico", 200),
    ("/../pyproject.toml", 404), ("/%2e%2e/huelle.py", 404), ("/..%2fhuelle.py", 404), ("/fonts", 404),
    ("/fenster.py", 404), ("/gibtsnicht.html", 404), ("/%5c..%5chuelle.py", 404),
])
def test_huelle_nur_aus_dem_ordner(huelle, pfad, status):
    assert _get(huelle, pfad).status_code == status


def test_huelle_nur_mit_eigenem_namen_und_nur_lesen(huelle):
    a = huelle.anschluss
    for host in (f"127.0.0.1:{a}", f"[::1]:{a}", f"LOCALHOST:{a}"):
        assert _get(huelle, "/index.html", host).status_code == 200
    for host in ("boese.example", f"boese.example:{a}", "localhost:8000", f"localhost.boese.example:{a}",
                 f"localhost:{a}.boese.example"):
        assert _get(huelle, "/index.html", host).status_code == 421
    for methode in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        assert _get(huelle, "/index.html", methode=methode).status_code == 405


def test_huelle_nimmt_einen_anderen_anschluss_wenn_ipv6_belegt(monkeypatch):
    import errno

    from taleward_worker import huelle as modul

    gebunden = []
    echt = modul._Server.server_bind
    echt_init = modul._Server.__init__
    belegt = {"mal": 1}

    def anlegen(self, adresse, handler, familie):
        if familie != socket.AF_INET6:
            return echt_init(self, adresse, handler, familie)
        # in diesem Container gibt es kein IPv6: Server ohne echte Steckdose
        self.address_family, self.server_address = familie, adresse
        self.socket = types.SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(modul._Server, "__init__", anlegen)

    def binden(self):
        if self.address_family == socket.AF_INET6:
            if belegt["mal"]:
                belegt["mal"] -= 1
                raise OSError(errno.EADDRINUSE, "belegt")
            gebunden.append(("v6", self.server_address[1]))
            return None  # in diesem Container gibt es kein IPv6 – „gebunden“ nur gemerkt
        echt(self)
        gebunden.append(("v4", self.server_address[1]))

    monkeypatch.setattr(modul._Server, "server_bind", binden)
    monkeypatch.setattr(modul._Server, "server_activate", lambda self: None if self.address_family == socket.AF_INET6
                        else modul.ThreadingHTTPServer.server_activate(self))
    monkeypatch.setattr(modul.threading, "Thread", lambda **k: types.SimpleNamespace(start=lambda: None))
    h = modul.Huelle(UI)
    h.starten()
    try:
        v4 = [p for a, p in gebunden if a == "v4"]
        assert len(v4) == 2 and gebunden[-1] == ("v6", h.anschluss) and h.anschluss == v4[1]
        assert len(h._server) == 2
    finally:
        h._server[0].server_close()


def test_huelle_ohne_ipv6_nur_v4(monkeypatch):
    import errno

    from taleward_worker import huelle as modul

    echt = modul._Server.server_bind

    def binden(self):
        if self.address_family == socket.AF_INET6:
            raise OSError(errno.EADDRNOTAVAIL, "kein IPv6")
        echt(self)

    monkeypatch.setattr(modul._Server, "server_bind", binden)
    h = modul.Huelle(UI)
    h.starten()
    try:
        assert len(h._server) == 1 and h.anschluss
    finally:
        h.stoppen()


def test_huelle_exklusiv_gebunden(huelle):
    """Kein SO_REUSEADDR: ein zweites Programm kann denselben Anschluss nicht mitbinden."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s.bind(("127.0.0.1", huelle.anschluss))
    finally:
        s.close()
    assert huelle._server[0].allow_reuse_address is False


# ---------------------------------------------------------------- Oberfläche: Rahmen, CSP-taugliches HTML, Texte
def _html(name):
    return (UI / name).read_text(encoding="utf-8")


def test_rahmen_ohne_weg_nach_oben():
    html = _html("desktop.html")
    rahmen = re.findall(r"<iframe[^>]*>", html)
    assert len(rahmen) == 2
    for r in rahmen:
        sandbox = set(re.search(r'sandbox="([^"]*)"', r).group(1).split())
        assert sandbox == {"allow-scripts", "allow-same-origin", "allow-forms", "allow-downloads", "allow-popups",
                           "allow-popups-to-escape-sandbox", "allow-modals"}
        assert "top-navigation" not in r and "src=" not in r.replace("srcdoc", "")  # src setzt erst das Skript
    app = [r for r in rahmen if 'id="rahmen-app"' in r][0]
    verwaltung = [r for r in rahmen if 'id="rahmen-verwaltung"' in r][0]
    assert 'allow="microphone; clipboard-write"' in app
    assert "microphone" not in verwaltung
    js = _html("desktop.js")
    assert 'const APP = "http://localhost:8000/app/"' in js
    assert 'const DISCORD = "https://discord.gg/TtJR9FcMbX"' in js and "disabled" not in html.split('id="hi-discord"')[1][:40]


@pytest.mark.parametrize("name", ["desktop.html", "index.html"])
def test_html_ohne_eingebettete_skripte_und_stile(name):
    html = _html(name)
    assert "<style" not in html and " style=" not in html and "javascript:" not in html
    assert re.findall(r"<script(?![^>]*\ssrc=)[^>]*>", html) == []
    assert re.search(r"\son[a-z]+=", html) is None
    assert '<script src="bruecke.js"></script>' in html


def test_oberflaechen_nutzen_die_bruecke():
    for name in ("app.js", "desktop.js"):
        js = _html(name)
        assert "window.pywebview.api" not in js and "TalewardBruecke.api" in js
        assert "new Function" not in js and "eval(" not in js


def _texte():
    import subprocess

    skript = ("global.window={};" + _html("texte.js") + _html("desktop-texte.js")
              + "console.log(JSON.stringify({t:window.TEXTE,d:window.DTEXTE}))")
    try:
        aus = subprocess.run(["node", "-e", skript], capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        pytest.skip(f"node nicht nutzbar: {e}")
    return json.loads(aus)


def test_desktop_texte_vollstaendig_und_im_ton():
    t = _texte()
    d = t["d"]
    assert set(d["de"]) == set(d["en"])
    # Deutsch: Schlüssel ohne Unterstrich sind selbst der Text (wie in texte.js); Englisch muss es immer geben
    schluessel = set(re.findall(r'data-t="([^"]+)"', _html("desktop.html")))
    schluessel |= set(re.findall(r'\bt\("([^"]+)"', _html("desktop.js")))
    schluessel = {k for k in schluessel if not k.endswith("_")}  # Vorsilben für zusammengesetzte Schlüssel
    for k in schluessel:
        assert k in d["en"] or k in t["t"]["en"], (k, "en")
        assert k in d["de"] or k in t["t"]["de"] or "_" not in k, (k, "de")
    for sprache in ("de", "en"):
        for k, v in d[sprache].items():
            assert "!" not in v, k
            woerter = ["KI-Paket", "Ollama", "Firewall", "Protokoll", "Port ", "Upload", "Chunk", "Pipeline", "AI pack"]
            if sprache == "de":
                woerter += ["Rechner", "Session"]  # Englisch: „session“ ist das Wort für Runde
            for wort in woerter:
                assert wort not in v, (k, wort)
            assert re.search(r"\bWorker\b", v.replace("Taleward Worker", "")) is None, k


# ---------------------------------------------------------------- Prüfwert für Folgenreiches
def _app(monkeypatch, **einstellungen):
    from taleward_worker import hardware
    from taleward_worker.oberflaeche import Api, WorkerApp

    monkeypatch.setattr(hardware, "bericht", lambda: {"grafikkarte": None})
    app = WorkerApp()
    for k, v in einstellungen.items():
        setattr(app.e, k, v)
    return app, Api(app)


def test_folgenreiches_nur_mit_pruefwert(monkeypatch):
    from taleward_worker import oberflaeche

    app, api = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=True)
    beendet = []
    monkeypatch.setattr(app, "beenden", lambda: beendet.append(1))
    monkeypatch.setattr(oberflaeche.threading, "Thread", lambda target, **k: types.SimpleNamespace(start=target))
    monkeypatch.setattr(oberflaeche.netzfreigabe, "anlegen", lambda: beendet.append("regel") or True)
    monkeypatch.setattr(app, "netz_pruefen", lambda: None)
    for aufruf in (lambda w: api.beenden(w), lambda w: api.netz_freigeben(w), lambda w: api.betrieb_wechseln(w, "verbunden"),
                   lambda w: api.sicherung_zurueckspielen(w, "pw"), lambda w: api.installieren(w),
                   lambda w: api.ki_entfernen(w), lambda w: api.entkoppeln(w), lambda w: api.ollama_entfernen(w),
                   lambda w: api.taleward_aktualisieren(w), lambda w: api.app_aktualisieren(w),
                   lambda w: api.taleward_neu_starten(w), lambda w: api.koppeln(w, "https://boese.example", "ABCD-EFGH", "x")):
        for falsch in ("", "falsch", None, 1, api._pruefwert + "x"):
            assert aufruf(falsch) == {"ok": False, "fehler": "pruefwert"}
    assert beendet == []
    wert = api.zustand()["pruefwert"]
    assert api.beenden(wert) == {"ok": True} and beendet == [1]
    assert api.beenden(wert) == {"ok": False, "fehler": "pruefwert"}  # gilt nur einmal
    assert api.netz_freigeben(api.zustand()["pruefwert"]) == {"ok": True} and beendet == [1, "regel"]


def test_einstellungen_von_taleward_desktop(monkeypatch):
    app, api = _app(monkeypatch, betrieb="einzelplatz")
    monkeypatch.setattr(app.e, "speichern", lambda: None)
    assert api.einstellung("leiste_schmal", True) == {"ok": True} and app.e.leiste_schmal is True
    assert api.einstellung("sicherung_erinnern", False) == {"ok": True} and app.e.sicherung_erinnern is False
    z = api.zustand()
    assert z["einstellungen"]["sicherung_zuletzt"] == 0 and z["ansicht"] in ("desktop", "worker")


def test_ansicht_je_betriebsart(monkeypatch):
    from taleward_worker import motor

    app, _ = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=True)
    monkeypatch.delenv("TALEWARD_MOTOR", raising=False)
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.112"})
    assert app.ansicht_soll() == "desktop" and app.titel("desktop") == "Taleward Desktop"
    app.e.konto_angelegt = False
    assert app.ansicht_soll() == "worker"  # Einrichtung im kleinen Fenster
    app.e.betrieb, app.e.konto_angelegt = "verbunden", True
    assert app.ansicht_soll() == "worker" and app.titel("worker") == "Taleward Worker"
    monkeypatch.setattr(motor, "installiert", lambda: None)
    app.e.betrieb = "einzelplatz"
    assert app.ansicht_soll() == "worker"


def test_ansicht_wechseln_stellt_fenster_um(monkeypatch):
    from taleward_worker import fenster, motor

    app, _ = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=True)
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.112"})
    monkeypatch.setattr(app.e, "speichern", lambda: None)
    app.huelle.anschluss = 51234
    aufrufe = []
    app.fenster = types.SimpleNamespace(
        _taleward_dicht=True, width=560, height=760, load_url=lambda u: aufrufe.append(("url", u)),
        set_title=lambda t: aufrufe.append(("titel", t)), resize=lambda b, h: aufrufe.append(("groesse", b, h)))
    monkeypatch.setattr(fenster, "mindestgroesse", lambda w, b, h: aufrufe.append(("mindestens", b, h)))
    app.ansicht = "worker"
    app.ansicht_anwenden()
    assert ("url", "http://localhost:51234/desktop.html") in aufrufe and ("titel", "Taleward Desktop") in aufrufe
    assert ("mindestens", 900, 600) in aufrufe and app.e.fenster_worker == [560, 760]
    aufrufe.clear()
    app.ansicht_anwenden()  # schon so: nichts
    assert aufrufe == []
    app.fenster._taleward_dicht = "fehler"
    app.ansicht, aufrufe[:] = "worker", []
    app.ansicht_anwenden()  # ohne Regeln nie die Oberfläche laden
    assert aufrufe == []


@pytest.mark.parametrize("ansicht,gemerkt,bildschirm,erwartet", [
    ("desktop", [], (1920, 1080), (1280, 820, (900, 600))),
    ("desktop", [], (1366, 768), (1280, 688, (900, 600))),
    ("desktop", [1500, 900], (1920, 1080), (1500, 900, (900, 600))),
    ("desktop", [400, 300], (1920, 1080), (900, 600, (900, 600))),
    ("desktop", [], (1024, 600), (1004, 520, (900, 520))),
    ("desktop", [], None, (1280, 820, (900, 600))),
    ("worker", [], (1920, 1080), (560, 760, (460, 600))),
])
def test_fenstergroesse_je_ansicht(ansicht, gemerkt, bildschirm, erwartet):
    from taleward_worker.oberflaeche import fenstergroesse_fuer

    assert fenstergroesse_fuer(ansicht, gemerkt, bildschirm) == erwartet


# ---------------------------------------------------------------- Server bekommt die Herkunft der Oberfläche
def test_server_bekommt_die_herkunft_der_oberflaeche(monkeypatch):
    from taleward_worker import einzelplatz

    monkeypatch.setenv("EINZELPLATZ_HUELLE", "http://localhost:1")  # von außen: nie
    d = einzelplatz.ServerDienst(befehl=["x"])
    assert "EINZELPLATZ_HUELLE" not in d.umgebung()
    d.huelle = HERKUNFT
    assert d.umgebung()["EINZELPLATZ_HUELLE"] == HERKUNFT


def test_programm_gibt_die_herkunft_weiter(monkeypatch):
    app, _ = _app(monkeypatch, betrieb="einzelplatz")
    assert app.server.huelle == ""
    app.huelle.anschluss = 51234
    assert app.server.huelle == HERKUNFT


def test_verarbeitung_nur_einzelplatz_und_selten(monkeypatch):
    from taleward_worker import oberflaeche

    app, api = _app(monkeypatch, betrieb="verbunden", server="https://verein.example", token="wk.1.x")
    abrufe = []
    monkeypatch.setattr(oberflaeche.server, "verarbeitung", lambda a, t: abrufe.append(a) or {"queued": 1})
    assert api.verarbeitung() is None and abrufe == []  # beim Verbinden gehört das dem Betreiber des Servers
    app.e.betrieb, app.e.server = "einzelplatz", "http://127.0.0.1:8000"
    assert api.verarbeitung() == {"queued": 1} and api.verarbeitung() == {"queued": 1}
    assert abrufe == ["http://127.0.0.1:8000"]


def test_heimnetz_adresse_nur_private_form(monkeypatch):
    import httpx

    from taleward_worker import server

    def antwort(wert):
        def handler(_req):
            return httpx.Response(200, json={"publicUrl": wert})
        return httpx.MockTransport(handler)

    echt = httpx.Client
    for wert, erwartet in (("http://192.168.178.24:8000", "http://192.168.178.24:8000"),
                           ("https://boese.example", None), ("javascript:alert(1)", None), (None, None),
                           ("http://192.168.178.24:8000/x", None)):
        monkeypatch.setattr(server.httpx, "Client", lambda *a, w=wert, **k: echt(transport=antwort(w)))
        assert server.heimnetz_adresse("http://127.0.0.1:8000") == erwartet, wert


def test_sicherung_merkt_den_zeitpunkt(monkeypatch, tmp_path):
    import subprocess

    from taleward_worker import mitnehmen

    app, _ = _app(monkeypatch, betrieb="einzelplatz")
    monkeypatch.setattr(app.e, "speichern", lambda: None)
    ziel = tmp_path / "s.taleward"
    monkeypatch.setattr(type(app), "ep_update", property(lambda self: types.SimpleNamespace(
        cli=lambda *a, **k: subprocess.CompletedProcess(a, 0, f"Sicherung gespeichert: {ziel}\n", ""))))
    s = mitnehmen.Sicherung(app)
    s._speichern(ziel, "geheim-geheim")
    assert s.phase == "fertig" and app.e.sicherung_zuletzt > 0
