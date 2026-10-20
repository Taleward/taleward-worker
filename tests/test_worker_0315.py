"""Worker 0.3.15: Autostart nur für dieselbe Installation, Links nur über web_adresse, Ollama fest eingestellt,
Messwerte des letzten Auftrags, Hinweis zum Schlafenlassen."""
import types

import pytest

ORDNER = r"C:\Users\a\AppData\Local\Programs\Taleward Worker"


@pytest.mark.parametrize("eintrag,ok", [
    (rf'"{ORDNER}\TalewardWorker.exe" --hintergrund', True),
    (rf'"{ORDNER.upper()}\talewardworker.exe" --hintergrund', True),          # Windows: Groß-/Kleinschreibung egal
    (r'"C:\Users\a\Downloads\TalewardWorker.exe" --hintergrund', False),      # andere Kopie
    (rf'"{ORDNER}\Anderes.exe" --hintergrund', False),
    (r"TalewardWorker.exe --hintergrund", False),                              # ohne Ordner
    (r"C:\Programme\TW\TalewardWorker.exe --hintergrund", False),
])
def test_alter_eintrag_nur_im_selben_ordner(eintrag, ok):
    from taleward_worker import autostart

    assert autostart.alter_eintrag_hier(eintrag, ORDNER + r"\Taleward.exe") is ok


def test_zweite_kopie_uebernimmt_den_autostart_nicht(monkeypatch):
    from taleward_worker import autostart

    reg = {autostart.NAME: rf'"{ORDNER}\Taleward.exe" --hintergrund'}
    geschrieben = []
    monkeypatch.setattr(autostart, "eintrag", lambda: reg[autostart.NAME])
    monkeypatch.setattr(autostart, "setzen", lambda an: geschrieben.append(an))
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", r"C:\Users\a\Downloads\Taleward.exe")
    assert not autostart.nachfuehren() and geschrieben == []
    reg[autostart.NAME] = rf'"{ORDNER}\TalewardWorker.exe" --hintergrund'
    assert not autostart.nachfuehren() and geschrieben == []   # alter Eintrag, aber andere Installation


@pytest.mark.parametrize("url,ok", [
    ("https://taleward.org/", True), ("https://discord.gg/TtJR9FcMbX", True),
    ("http://taleward.org/", False), ("https://a.example\\@b.example/", False), ("https://a@b.example/", False),
    ("https://a.example/" + chr(0x202E), False), ("https://", False), (None, False),
])
def test_link_oeffnen_nur_sichere_adressen(monkeypatch, url, ok):
    from taleward_worker import oberflaeche

    geoeffnet = []
    monkeypatch.setattr(oberflaeche.webbrowser, "open", lambda u, *a, **k: geoeffnet.append(u))
    oberflaeche.Api.link_oeffnen(types.SimpleNamespace(), url)
    assert (geoeffnet == [url]) is ok


def test_mitgeliefertes_ollama_fest_eingestellt():
    from taleward_worker.ollama import OLLAMA_UMGEBUNG

    assert OLLAMA_UMGEBUNG["OLLAMA_MAX_LOADED_MODELS"] == "1" and OLLAMA_UMGEBUNG["OLLAMA_NUM_PARALLEL"] == "1"
    assert OLLAMA_UMGEBUNG["OLLAMA_FLASH_ATTENTION"] == "1" and OLLAMA_UMGEBUNG["OLLAMA_KV_CACHE_TYPE"] == "q8_0"


def test_messwerte_bereinigt_und_als_text():
    from taleward_worker.dienst import messwerte_bereinigen, messwerte_text

    roh = {"art": "kapitel", "modell": "gemma4:12b", "quantisierung": "Q4_K_M", "kontext": 32768,
           "einlesenTokS": 851.23, "schreibenTokS": 41.96, "ganzAufKarte": False, "abgestuft": True,
           "karte": "NVIDIA GeForce RTX 4060 Ti", "kartenspeicherMb": 16380, "fremd": "<script>",
           "stufen": "kaputt", "kontextX": float("inf")}
    m = messwerte_bereinigen(roh)
    assert "fremd" not in m and "stufen" not in m and m["einlesenTokS"] == 851.2
    text = messwerte_text(roh)
    assert text.startswith("gemma4:12b (Q4_K_M), Kontext 32768 · Einlesen 851 Token/s · Schreiben 42,0 Token/s")
    assert "teils im Arbeitsspeicher" in text and "kleiner gewählt" in text and "RTX 4060 Ti (16,0 GB)" in text
    abschrift = {"art": "abschrift", "minutenJeStunde": 6.04, "stufen": {"erkennung": 78, "sprechertrennung": 126,
                                                                          "unbekannt": 5, "einlesen": -1}}
    assert messwerte_text(abschrift) == ("6,0 min Rechenzeit je Aufnahmestunde · Erkennung 1,3 min · "
                                         "Sprechertrennung 2,1 min")
    assert messwerte_bereinigen("kein dict") == {} and messwerte_text({}) == ""


def test_statistik_merkt_die_messwerte(tmp_path, monkeypatch):
    from taleward_worker import dienst, pfade

    monkeypatch.setattr(pfade, "basis", lambda: tmp_path)
    st = dienst.Statistik()
    st.erledigt(300, 3600, 5000, {"art": "abschrift", "minutenJeStunde": 5, "boese": 1})
    m = dienst.Statistik().fuer_oberflaeche()["messwerte"]
    assert m["art"] == "abschrift" and m["minutenJeStunde"] == 5.0 and "boese" not in m and m["zeit"] > 0


def test_desktop_zeigt_messwerte_und_schlaf_hinweis():
    from pathlib import Path

    ui = Path(__file__).resolve().parent.parent / "taleward_worker" / "ui"
    html, js = (ui / "desktop.html").read_text(encoding="utf-8"), (ui / "desktop.js").read_text(encoding="utf-8")
    texte = (ui / "desktop-texte.js").read_text(encoding="utf-8")
    assert 'id="vb-messung"' in html and "messungZeigen()" in js and 'data-t="d_s5_frage"' in html
    assert texte.count("d_s5_antwort:") == 2 and texte.count("d_letzter_auftrag:") == 2
    assert "schlafen" in texte and "sleep" in texte
