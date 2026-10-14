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
    skript = nf.anlegen_skript(py)
    assert "-Program 'C:\\Users\\Jörg O''Neill\\AppData" in skript  # Apostroph verdoppelt, nichts sonst gedeutet
    assert "-Profile Private -RemoteAddress LocalSubnet" in skript and f"-LocalPort {nf.PORT}" in skript
    args = nf._kodiert(skript)
    assert base64.b64decode(args[-1]).decode("utf-16-le") == skript and "-EncodedCommand" in args


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
