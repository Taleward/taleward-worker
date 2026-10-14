"""Deinstallation: eigene Frage nach den Kampagnen (ab Worker 0.3.4)."""
from pathlib import Path


def test_deinstallation_fragt_nach_den_kampagnen():
    iss = (Path(__file__).parent.parent / "packaging" / "windows" / "taleward-worker.iss").read_text(encoding="utf-8")
    assert "{localappdata}\\Taleward\\Daten" in iss
    teil = iss[iss.index("(not UninstallSilent) and DirExists(Kampagnen)"):]
    teil = teil[:teil.index("end;")]
    # eigene Frage, standardmäßig „Nein“, und eine zweite Rückfrage vor dem endgültigen Löschen
    assert "KampagnenLoeschen" in teil and "KampagnenWirklich" in teil and teil.count("MB_DEFBUTTON2") == 2
    assert "not UninstallSilent" in teil
    for sprache in ("de", "en"):
        assert f"{sprache}.KampagnenLoeschen=" in iss and f"{sprache}.KampagnenWirklich=" in iss
