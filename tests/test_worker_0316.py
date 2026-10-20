"""Worker 0.3.16: Die Verwaltung im Fenster erst, wenn der Server läuft und neu genug ist – sonst ein Hinweis mit Update
und dem Weg über den Browser (statt „Verbindung verweigert“ im Rahmen)."""
import types
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "taleward_worker" / "ui"


def test_verwaltung_mit_hinweis_statt_leerem_rahmen():
    html = (UI / "desktop.html").read_text(encoding="utf-8")
    js = (UI / "desktop.js").read_text(encoding="utf-8")
    texte = (UI / "desktop-texte.js").read_text(encoding="utf-8")
    assert 'id="verwaltung-hinweis"' in html and html.index('id="verwaltung-hinweis"') < html.index('id="rahmen-verwaltung"')
    assert "function verwaltungHinweis()" in js and "if (!verwaltungHinweis() || verwaltungLaden.laeuft) return;" in js
    assert "api.verwaltung_im_browser()" in js
    for k in ("d_verwaltung_alt_text:", "d_verwaltung_startet_text:", "d_upd_noch_nicht:"):
        assert texte.count(k) == 2, k  # Deutsch und Englisch


def test_verwaltung_im_browser_nur_bei_alles_auf_diesem_pc(monkeypatch):
    from taleward_worker import oberflaeche

    geoeffnet = []
    monkeypatch.setattr(oberflaeche.webbrowser, "open", lambda u, *a, **k: geoeffnet.append(u))
    api = types.SimpleNamespace(_app=types.SimpleNamespace(e=types.SimpleNamespace(einzelplatz=False)))
    oberflaeche.Api.verwaltung_im_browser(api)
    assert geoeffnet == []
    api._app.e.einzelplatz = True
    oberflaeche.Api.verwaltung_im_browser(api)
    assert geoeffnet == ["http://localhost:8000/verwaltung/"]
