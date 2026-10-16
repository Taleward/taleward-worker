"""Worker 0.3.9: Programmdatei Taleward.exe, Stimmen erkennen einrichten, Fenster im dunklen Thema, Firewall-Regel nach
dem Neuinstallieren."""
import re
import sys
import types
from pathlib import Path

from tests.test_worker_038 import _app

WURZEL = Path(__file__).resolve().parent.parent


def test_installer_legt_taleward_exe_an_und_kennt_den_alten_namen():
    iss = (WURZEL / "packaging/windows/taleward-worker.iss").read_text(encoding="utf-8")
    assert 'DestName: "Taleward.exe"' in iss and 'Excludes: "TalewardWorker.exe"' in iss
    assert 'Type: files; Name: "{app}\\TalewardWorker.exe"' in iss  # alte Datei beim Update weg
    for zeile in re.findall(r'^(?:Name|Filename|  ValueData|UninstallDisplayIcon).*$', iss, re.M):
        assert "TalewardWorker.exe" not in zeile, zeile  # Verknüpfungen, Start, Autostart: neuer Name
    assert "LaeuftAls('Taleward.exe') or LaeuftAls('TalewardWorker.exe')" in iss
    assert "taskkill /IM Taleward.exe" in iss and "taskkill /IM TalewardWorker.exe" in iss
    assert "OutputBaseFilename=TalewardWorker-Setup" in iss  # Name des Installers bleibt (Selbst-Update alter Fassungen)


def test_autostart_zieht_auf_das_umbenannte_programm_um(monkeypatch):
    from taleward_worker import autostart

    reg = {}

    class Schluessel:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def query(_k, name):
        if name not in reg:
            raise OSError
        return reg[name], 1

    winreg = types.SimpleNamespace(HKEY_CURRENT_USER=0, KEY_SET_VALUE=2, REG_SZ=1,
                                   OpenKey=lambda *a: Schluessel(), QueryValueEx=query,
                                   SetValueEx=lambda _k, name, _r, _t, wert: reg.__setitem__(name, wert),
                                   DeleteValue=lambda _k, name: reg.pop(name))
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", r"C:\Users\a\AppData\Local\Programs\Taleward Worker\Taleward.exe")
    assert not autostart.nachfuehren()  # kein Eintrag: bleibt aus
    assert reg == {}
    reg[autostart.NAME] = r'"C:\Users\a\AppData\Local\Programs\Taleward Worker\TalewardWorker.exe" --hintergrund'
    assert autostart.nachfuehren()
    assert reg[autostart.NAME].endswith('Taleward.exe" --hintergrund') and "TalewardWorker" not in reg[autostart.NAME]
    assert not autostart.nachfuehren()  # passt schon


def test_fenster_im_dunklen_thema_ohne_hellen_blitz(monkeypatch):
    from taleward_worker import fenster

    monkeypatch.setattr(fenster, "dunkel", lambda: True)
    assert fenster.papier() == "#121D22" and "#121D22" in fenster.leer()
    monkeypatch.setattr(fenster, "dunkel", lambda: False)
    assert fenster.papier() == "#F2E8D5" and "#F2E8D5" in fenster.leer()


def test_stimmen_erkennen_einrichten_oeffnet_die_richtige_stelle(monkeypatch):
    from taleward_worker import oberflaeche

    geoeffnet = []
    monkeypatch.setattr(oberflaeche.webbrowser, "open", lambda url, *a, **k: geoeffnet.append(url))
    app, api = _app(monkeypatch, betrieb="einzelplatz")
    api.verwaltung_oeffnen("transkription#hf")
    api.verwaltung_oeffnen("../../irgendwohin")  # nur feste Ziele
    api.verwaltung_oeffnen()
    assert geoeffnet == ["http://127.0.0.1:8000/verwaltung/transkription#hf", "http://127.0.0.1:8000/verwaltung",
                         "http://127.0.0.1:8000/verwaltung"]


def test_firewall_regel_nach_neuinstallation_wieder_angeboten(monkeypatch):
    from taleward_worker import netzfreigabe

    app, _ = _app(monkeypatch, betrieb="einzelplatz", firewall_gefragt=True)
    angelegt = []
    monkeypatch.setattr(netzfreigabe, "WINDOWS", True)
    monkeypatch.setattr(netzfreigabe, "stand", lambda: {"regel": False})
    monkeypatch.setattr(netzfreigabe, "anlegen", lambda: angelegt.append(1) or True)
    monkeypatch.setattr(app, "netz_pruefen", lambda: None)
    monkeypatch.setattr(app.e, "speichern", lambda: None)
    app._netz_beim_ersten_mal()
    assert angelegt == []  # beim normalen Start nicht wieder fragen
    app._netz_beim_ersten_mal(nach_installation=True)
    assert angelegt == [1]


def test_texte_deutsch_englisch():
    texte = (WURZEL / "taleward_worker/ui/texte.js").read_text(encoding="utf-8")
    assert texte.count("st_modell_hier:") == 2 and texte.count("st_modell_hier_text:") == 2
    en = texte[texte.index("en: {") if "en: {" in texte else len(texte) // 2:]
    assert "recaps on this PC" not in en and "a recap takes hours" not in en and '"Recaps": "Recaps"' not in en
    assert "Sprechermodell" not in re.search(r'st_modell_hier_text: "([^"]*)"', texte).group(1)
