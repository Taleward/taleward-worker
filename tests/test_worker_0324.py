"""Worker 0.3.24: Messwerte ohne Steuerzeichen und ohne riesige oder unendliche Zahlen; certifi und Pillow aktuell."""
from taleward_worker.dienst import messwerte_bereinigen


def test_messwerte_bereinigt():
    m = messwerte_bereinigen({"einlesenTokS": 10 ** 400, "schreibenTokS": float("inf"), "kontext": 32768,
                              "modell": "gemma" + chr(10) + "Fremde Zeile" + chr(0x202E),
                              "stufen": {"erkennung": float("inf"), "einlesen": 3, "ausrichtung": 10 ** 400}})
    assert "einlesenTokS" not in m and "schreibenTokS" not in m and m["kontext"] == 32768.0
    assert chr(10) not in m["modell"] and chr(0x202E) not in m["modell"] and m["modell"] == "gemma Fremde Zeile"
    assert m["stufen"] == {"einlesen": 3.0}


def test_pakete_aktuell():
    from pathlib import Path

    lock = (Path(__file__).resolve().parent.parent / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "certifi"\nversion = "2026.10.19"' in lock and 'name = "pillow"\nversion = "13.0.0"' in lock
