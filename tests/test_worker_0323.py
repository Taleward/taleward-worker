"""Worker 0.3.23: Fehlt das Sprechermodell noch, lädt der Server (ab 0.4.117) es meist gerade vom Taleward-Spiegel –
die ersten 15 Minuten zeigt das Programm „wird eingerichtet“ statt „braucht deine Hilfe“."""
import re
from pathlib import Path

from taleward_worker.dienst import Dienst

UI = Path(__file__).resolve().parent.parent / "taleward_worker" / "ui"


def test_modell_seit_bleibt_ueber_versuche():
    d = Dienst.__new__(Dienst)
    d._setzen("fehler", code="modell", text="Sprechermodell …")
    seit = d.zustand["modellSeit"]
    d._setzen("startet", modelle=True)  # nächster Versuch
    d._setzen("fehler", code="modell", text="Sprechermodell …")
    assert d.zustand["modellSeit"] == seit
    d._setzen("warte")
    d._setzen("fehler", code="modell", text="Sprechermodell …")
    assert d.zustand["modellSeit"] >= seit
    d._setzen("fehler", code="einrichtung", text="anders")
    assert "modellSeit" not in d.zustand


def test_texte_und_weichen():
    js = (UI / "desktop.js").read_text(encoding="utf-8")
    assert "modellLaedtNoch(d, S.serverVersion)" in js and "[0, 4, 117]" in js and "< 900" in js
    app = (UI / "app.js").read_text(encoding="utf-8")
    assert "modellLaedtNoch(z)" in app and 'v[2] >= 117' in app
    texte = (UI / "desktop-texte.js").read_text(encoding="utf-8")
    de, en = texte.split("\n  en: {")
    for k in ("d_z_stimmen_laedt", "d_z_stimmen_laedt_text"):
        assert k + ":" in de and k + ":" in en
    t2 = (UI / "texte.js").read_text(encoding="utf-8")
    assert len(re.findall(r"st_modell_laedt_text:", t2)) == 2
