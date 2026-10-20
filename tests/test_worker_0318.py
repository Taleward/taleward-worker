"""Worker 0.3.18: Leiste des Programmfensters auf Pergament wie die Verwaltung – 1-px-Linie zum Inhalt, aktiver Bereich
als ruhige Fläche, normales Logo (im dunklen Thema das helle)."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "taleward_worker" / "ui"


def _regel(css: str, kopf: str) -> str:
    i = css.index(kopf)
    return css[i:css.index("}", i)]


def test_leiste_auf_pergament():
    css = (UI / "desktop.css").read_text(encoding="utf-8")
    leiste = _regel(css, ".leiste {")
    assert "background:var(--paper)" in leiste and "border-right:1px solid var(--line)" in leiste
    assert "var(--tinte)" not in leiste and "radial-gradient" not in leiste
    assert "background:var(--paper-sunken)" in _regel(css, '.bereiche button[aria-current="page"] {')
    assert not re.search(r"rgba\((255|243)", css)  # keine Weiß-Töne für eine dunkle Leiste mehr


def test_logo_hell_und_dunkel():
    html = (UI / "desktop.html").read_text(encoding="utf-8")
    assert 'src="marke/taleward-lockup.svg"' in html
    assert '<source srcset="marke/taleward-lockup-inverse.svg" media="(prefers-color-scheme: dark)">' in html
    assert (UI / "marke" / "taleward-lockup.svg").read_text(encoding="utf-8").lstrip().startswith("<")
