"""Programmfenster, Netzwerk-Freigabe und Ollama für „Alles auf diesem PC“."""


def test_eigene_seite():
    from taleward_worker.fenster import eigene_seite

    for gut in ("http://localhost:8000/app/", "http://127.0.0.1:8000/app/#/kampagnen", "http://localhost:8000/"):
        assert eigene_seite(gut), gut
    for boese in ("https://localhost:8000/app/", "http://localhost:8001/app/", "http://localhost/app/",
                  "http://192.168.178.24:8000/app/", "http://localhost.boese.example:8000/",
                  "http://user@localhost:8000/", "file:///C:/Windows", "javascript:alert(1)", "", "about:blank"):
        assert not eigene_seite(boese), boese


def test_keine_bruecke():
    from taleward_worker.fenster import KeineBruecke

    k = KeineBruecke()
    assert getattr(k, "__class__", None) is None and getattr(k, "beenden", None) is None and dir(k) == []


# ---------------------------------------------------------------- Netzwerk-Freigabe (Windows)
def test_firewall_regel_nur_wie_vorgesehen():
    import base64
    from pathlib import Path

    from taleward_worker import netzfreigabe as nf

    py = Path(r"C:\Users\Jörg O'Neill\AppData\Local\Taleward Worker\python\cpython-3.11\python.exe")
    gut = {"Program": str(py), "Port": "8000", "Protocol": "TCP", "Enabled": "True", "Action": "Allow",
           "Profile": "Private", "Remote": "LocalSubnet"}
    assert nf.regel_passt(gut, py)
    assert nf.regel_passt({**gut, "Program": str(py).upper()}, py)  # Windows-Pfade ohne Groß-/Kleinschreibung
    for name, wert in (("Port", "Any"), ("Profile", "Any"), ("Profile", "Private, Public"), ("Remote", "Any"),
                       ("Enabled", "False"), ("Action", "Block"), ("Protocol", "UDP"), ("Program", "C:\\x.exe")):
        assert not nf.regel_passt({**gut, name: wert}, py), name
    assert not nf.regel_passt(None, py) and not nf.regel_passt(gut, None)
    assert not nf.regel_passt({**gut, "Fremd": 1}, py)  # daneben noch eine Windows-Regel für dasselbe Python
    assert nf.regel_passt({**gut, "Fremd": 0}, py)
    skript = nf.anlegen_skript(py)
    assert "Jörg" not in skript and "O'Neill" not in skript  # Pfad nur als Base64 – nichts darin wird gedeutet
    assert nf._ps(str(py)) in skript and "-Name " + nf._ps("Taleward-Einzelplatz") in skript
    assert "-Profile Private" in skript and "-RemoteAddress LocalSubnet" in skript and f"-LocalPort {nf.PORT}" in skript
    assert "Remove-NetFirewallRule -DisplayName" not in skript  # nie alle Regeln eines Anzeigenamens
    args = nf._kodiert(skript)
    assert base64.b64decode(args[-1]).decode("utf-16-le") == skript and "-EncodedCommand" in args


def test_powershell_zeichenkette_ohne_deutung():
    import base64
    import re

    from taleward_worker import netzfreigabe as nf

    for text in ("C:\\Jörg’s ‚Ordner‛\\x.exe", "a'b\"c`$d", "Taleward"):
        roh = re.fullmatch(r"\(\[Text\.Encoding\]::UTF8\.GetString\(\[Convert\]::FromBase64String\('([A-Za-z0-9+/=]*)'\)\)\)",
                           nf._ps(text)).group(1)
        assert base64.b64decode(roh).decode("utf-8") == text


def test_firewall_ausserhalb_von_windows(monkeypatch):
    from taleward_worker import netzfreigabe as nf

    monkeypatch.setattr(nf, "WINDOWS", False)
    assert nf.stand() == {"noetig": False} and nf.anlegen() and nf.netzprofile() == []


def test_offenes_fremdes_ollama_nicht_im_einzelplatz(monkeypatch):
    from taleward_worker import ollama

    d = ollama.OllamaDienst()
    monkeypatch.setattr(ollama, "antwortet", lambda url, zeit=2.0: url == ollama.FREMDE_ADRESSE or "192.168" in url)
    monkeypatch.setattr(ollama, "eigene_netzadressen", lambda: ["192.168.178.24"])
    monkeypatch.setattr(ollama, "installiert", lambda: False)
    assert d.adresse(True) == ollama.FREMDE_ADRESSE                   # mit einem Server verbunden: wie bisher
    assert d.adresse(True, nur_geschlossen=True) == ollama.AUS         # im Netz offen → nicht mitbenutzen
    monkeypatch.setattr(ollama, "antwortet", lambda url, zeit=2.0: url == ollama.FREMDE_ADRESSE)
    assert d.adresse(True, nur_geschlossen=True) == ollama.FREMDE_ADRESSE  # nur auf diesem PC → in Ordnung


def test_neue_fenster_nur_mit_web_adressen(monkeypatch):
    """window.open & Co. landen bei pywebview in webbrowser.open – nach absichern() nur noch http(s)."""
    import webbrowser

    from taleward_worker import fenster

    geoeffnet = []
    monkeypatch.setattr(fenster, "_ORIGINAL_OEFFNEN", lambda url, new=0, autoraise=True: geoeffnet.append(url) or True)
    for name in ("open", "open_new", "open_new_tab"):
        monkeypatch.setattr(webbrowser, name, getattr(webbrowser, name))  # nach dem Test wiederherstellen
    fenster.absichern()
    for boese in ("file:///C:/Windows/System32/calc.exe", "\\\\server\\freigabe\\x.exe", "//server/freigabe/x.exe",
                  "ms-msdt:/id PCWDiagnostic", "search-ms:query=x", "javascript:alert(1)", "C:\\Windows\\x.exe",
                  "https://user:pw@taleward.org/", "https://taleward.org/\\..\\x", "https://taleward.org/a b",
                  "https://taleward.org/\n", None, ""):
        assert webbrowser.open(boese) is False, boese
        assert webbrowser.open_new_tab(boese) is False, boese
    assert webbrowser.open("https://taleward.org/hilfe") and webbrowser.open_new("http://localhost:8000/app/")
    assert geoeffnet == ["https://taleward.org/hilfe", "http://localhost:8000/app/"]


def test_pywebview_oeffnet_ueber_webbrowser_open():
    """absichern() greift nur, solange pywebview webbrowser.open zur Laufzeit nachschlägt (nicht vorab importiert)."""
    from pathlib import Path

    import webview

    for datei in (Path(webview.__file__).parent / "platforms").glob("*.py"):
        text = datei.read_text(encoding="utf-8")
        assert "from webbrowser import" not in text, datei.name
        if "webbrowser" in text:
            assert "import webbrowser" in text, datei.name


def test_ollama_ohne_fremde_einstellungen(monkeypatch, tmp_path):
    import subprocess

    from taleward_worker import ollama

    monkeypatch.setenv("OLLAMA_ORIGINS", "*")
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    monkeypatch.setattr(ollama, "programm", lambda: tmp_path / "ollama")
    monkeypatch.setattr(ollama, "antwortet", lambda url, zeit=2.0: True)
    umgebung = {}

    class P:
        returncode = None

        def poll(self):
            return None
    monkeypatch.setattr(subprocess, "Popen", lambda befehl, env=None, **k: umgebung.update(env) or P())
    monkeypatch.setattr(ollama.prozesse, "zuordnen", lambda p: None)
    d = ollama.OllamaDienst()
    assert d.starten(warten_s=1)
    d._prozess = None  # Ersatzprozess – beim Beenden nichts aufräumen
    assert umgebung["OLLAMA_HOST"] == f"127.0.0.1:{ollama.EIGENER_PORT}" and "OLLAMA_ORIGINS" not in umgebung


def test_offenes_ollama_auf_dem_eigenen_port_nicht_im_einzelplatz(monkeypatch):
    from taleward_worker import ollama

    d = ollama.OllamaDienst()
    monkeypatch.setattr(ollama, "antwortet", lambda url, zeit=2.0: str(ollama.EIGENER_PORT) in url)  # kein fremdes
    monkeypatch.setattr(ollama, "eigene_netzadressen", lambda: ["192.168.178.24"])
    monkeypatch.setattr(ollama, "installiert", lambda: False)
    assert d.adresse(True) == d.eigene_adresse                    # mit einem Server verbunden: wie bisher
    assert d.adresse(True, nur_geschlossen=True) == ollama.AUS    # auch im Heimnetz offen → nicht übernehmen


# ---------------------------------------------------------------- 0.3.5: Regeln vor der App, fremde Schemata
def test_web_adresse_nur_ascii():
    from taleward_worker.fenster import web_adresse

    assert not web_adresse("https://täleward.org/") and not web_adresse("https://taleward.org/straße")
    assert web_adresse("https://xn--tleward-b1a.org/hilfe")


class _Ereignis:
    def __init__(self):
        self.handler = []

    def __iadd__(self, h):
        self.handler.append(h)
        return self


class _Fenster:
    def __init__(self, **k):
        self.k = k
        self.events = type("E", (), {})()
        self.events.loaded, self.events.closed = _Ereignis(), _Ereignis()
        self.geladen = []

    def load_url(self, url):
        self.geladen.append(url)


def test_app_fenster_laedt_erst_eine_leere_seite(monkeypatch):
    import webview

    from taleward_worker import fenster

    erzeugt = []
    monkeypatch.setattr(webview, "create_window", lambda titel, url=None, **k: erzeugt.append(
        _Fenster(url=url, **k)) or erzeugt[-1])
    monkeypatch.setattr(fenster.sys, "platform", "linux")
    w = fenster.app_fenster("http://localhost:8000/app/")
    assert w.k["url"] is None and "<body" in w.k["html"]  # nicht gleich die App
    for h in w.events.loaded.handler:
        h()
    assert w.geladen == ["http://localhost:8000/app/"]
    for h in w.events.loaded.handler:  # weitere Ladevorgänge ändern nichts
        h()
    assert w.geladen == ["http://localhost:8000/app/"]
    import pytest

    with pytest.raises(ValueError):
        fenster.app_fenster("https://boese.example/")


def _webview2_attrappe(monkeypatch, mit_schema_ereignis=True):
    import sys
    import types

    class Core:
        def __init__(self):
            self.NavigationStarting, self.FrameNavigationStarting = _Ereignis(), _Ereignis()
            self.PermissionRequested = _Ereignis()
            if mit_schema_ereignis:
                self.LaunchingExternalUriScheme = _Ereignis()
            self.Settings = types.SimpleNamespace()
            self.aufrufe = []

        def Navigate(self, url):
            self.aufrufe.append(("navigate", url, len(self.NavigationStarting.handler)))

    core = Core()
    form = types.SimpleNamespace(browser=types.SimpleNamespace(webview=types.SimpleNamespace(CoreWebView2=core)),
                                 Invoke=lambda aktion: aktion())
    system = types.ModuleType("System")
    system.Action = lambda f: f
    kern = types.ModuleType("Microsoft.Web.WebView2.Core")
    kern.CoreWebView2PermissionKind = types.SimpleNamespace(Microphone="mic")
    kern.CoreWebView2PermissionState = types.SimpleNamespace(Allow="allow", Deny="deny")
    for name, modul in (("System", system), ("Microsoft", types.ModuleType("Microsoft")),
                        ("Microsoft.Web", types.ModuleType("Microsoft.Web")),
                        ("Microsoft.Web.WebView2", types.ModuleType("Microsoft.Web.WebView2")),
                        ("Microsoft.Web.WebView2.Core", kern)):
        monkeypatch.setitem(sys.modules, name, modul)
    return core, form


def test_unter_windows_regeln_vor_der_app_und_keine_fremden_schemata(monkeypatch):
    import types

    from taleward_worker import fenster

    core, form = _webview2_attrappe(monkeypatch)
    monkeypatch.setattr(fenster.sys, "platform", "win32")
    w = types.SimpleNamespace(native=form)
    fenster.abdichten(w, "http://localhost:8000/app/")
    assert w._taleward_dicht and core.Settings.AreDevToolsEnabled is False
    assert core.aufrufe == [("navigate", "http://localhost:8000/app/", 1)]  # App erst nach den Regeln
    args = types.SimpleNamespace(Uri="ms-settings:privacy", Cancel=False)
    core.LaunchingExternalUriScheme.handler[0](None, args)
    assert args.Cancel is True
    fenster.abdichten(w, "http://localhost:8000/app/")  # zweites Laden: nichts mehr
    assert len(core.aufrufe) == 1


def test_aeltere_webview2_ohne_schema_ereignis(monkeypatch):
    import types

    from taleward_worker import fenster

    core, form = _webview2_attrappe(monkeypatch, mit_schema_ereignis=False)
    monkeypatch.setattr(fenster.sys, "platform", "win32")
    w = types.SimpleNamespace(native=form, destroy=lambda: (_ for _ in ()).throw(AssertionError("geschlossen")))
    fenster.abdichten(w, "http://localhost:8000/app/")
    assert w._taleward_dicht and core.aufrufe


def test_firewall_nur_fuer_python_im_datenordner(monkeypatch, tmp_path):
    from taleward_worker import netzfreigabe as nf

    monkeypatch.setenv("TALEWARD_WORKER_HOME", str(tmp_path / "Taleward Worker"))
    basis = tmp_path / "Taleward Worker"
    py = basis / "python" / "cpython-3.11" / "bin" / "python3.11"
    py.parent.mkdir(parents=True)
    py.write_text("")
    assert nf.im_datenordner(py)
    fremd = tmp_path / "anderswo" / "python.exe"
    fremd.parent.mkdir()
    fremd.write_text("")
    assert not nf.im_datenordner(fremd)
    assert not nf.im_datenordner(basis / ".." / "anderswo" / "python.exe")  # nicht über ..
    assert not nf.im_datenordner(basis / "python" / "cmd.exe")              # nur ein Python
    (tmp_path / "Taleward Worker-boese").mkdir()
    assert not nf.im_datenordner(tmp_path / "Taleward Worker-boese" / "python.exe")  # gleicher Anfang zählt nicht


def test_firewall_suche_ohne_platzhalter():
    """[ ] im Benutzernamen: nicht über -Program suchen (Platzhalter), sondern exakt vergleichen."""
    from pathlib import Path

    from taleward_worker import netzfreigabe as nf

    py = Path(r"C:\Users\Kai [Spiele]\AppData\Local\Taleward Worker\python\python.exe")
    skript = nf.anlegen_skript(py)
    assert "$_.Program -eq " + nf._ps(str(py)) in skript and "Get-NetFirewallApplicationFilter -All" in skript
    assert "Get-NetFirewallApplicationFilter -Program" not in skript


def test_firewall_regel_entfernen(monkeypatch, tmp_path):
    from taleward_worker import netzfreigabe as nf

    skript = nf.entfernen_skript()
    assert "-Name " + nf._ps(nf.REGEL_NAME) in skript and "Remove-NetFirewallRule -DisplayName" not in skript
    monkeypatch.setenv("TALEWARD_WORKER_HOME", str(tmp_path))
    (tmp_path / "firewall.txt").write_text(nf.REGEL_NAME)
    monkeypatch.setattr(nf, "WINDOWS", True)
    monkeypatch.setattr(nf, "_als_admin", lambda programm, parameter: True)
    monkeypatch.setattr(nf, "regel_lesen", lambda programm=None: None)
    assert nf.entfernen() and not (tmp_path / "firewall.txt").exists()
    (tmp_path / "firewall.txt").write_text(nf.REGEL_NAME)
    monkeypatch.setattr(nf, "_als_admin", lambda programm, parameter: False)  # „Nein“ bei der Rückfrage
    assert not nf.entfernen() and (tmp_path / "firewall.txt").exists()
