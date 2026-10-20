"""Worker 0.3.17: flach wie App und Verwaltung – ruhende Karten ohne Schatten, nur Schwebendes (Rückfragen, Hinweise)
behält einen."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "taleward_worker" / "ui"


def test_karten_ohne_schatten():
    css = (UI / "app.css").read_text(encoding="utf-8")
    assert css.count("--shadow-card:0 0 #0000;") == 2  # hell und dunkel
    for regel in (".karte {", ".zahl {"):
        block = css[css.index(regel):css.index("}", css.index(regel))]
        assert "border:1px solid var(--line)" in block and "var(--shadow-card)" in block
    assert re.search(r"\.dialog \{[^}]*box-shadow:var\(--shadow-float\)", css)
    assert re.search(r"\.toast \{[^}]*box-shadow:var\(--shadow-float\)", css)
