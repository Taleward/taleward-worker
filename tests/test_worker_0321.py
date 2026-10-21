"""Worker 0.3.21: Mikrofon in Taleward Desktop – die App im Rahmen bekommt es, die Verwaltung nie."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "taleward_worker" / "ui"


def test_mikrofon_im_app_rahmen():
    """Taleward Desktop: Die App läuft im Rahmen; WebView2 meldet ihre Mikrofon-Anfrage mit der Herkunft der
    Oberfläche. Nur der App-Rahmen (Name beim Anlegen) bekommt das Mikrofon, die Verwaltung nie."""
    from taleward_worker import fenster

    huelle = "http://localhost:49731"
    assert fenster.mikrofon_im_app_rahmen("http://localhost:49731/", huelle)
    assert fenster.mikrofon_im_app_rahmen("http://localhost:8000/app/", huelle)
    assert not fenster.mikrofon_im_app_rahmen("http://localhost:8000/verwaltung/", huelle)
    assert not fenster.mikrofon_im_app_rahmen("http://localhost:49732/", huelle)
    assert not fenster.mikrofon_im_app_rahmen("https://example.org/", huelle)
    html = (UI / "desktop.html").read_text(encoding="utf-8")
    app = re.search(r'<iframe id="rahmen-app"[^>]*>', html).group(0)
    assert f'name="{fenster.APP_RAHMEN}"' in app and 'allow="microphone' in app
    verwaltung = re.search(r'<iframe id="rahmen-verwaltung"[^>]*>', html).group(0)
    assert "name=" not in verwaltung and "microphone" not in verwaltung
    quelle = (Path(fenster.__file__)).read_text(encoding="utf-8")
    assert "core.FrameCreated += rahmen_erzeugt" in quelle and "a.Handled = True" in quelle
