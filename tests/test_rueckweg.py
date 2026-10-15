"""Rückweg des KI-Pakets nach einem gescheiterten Update von „Alles auf diesem PC“ (motor.Installation)."""
import json
from pathlib import Path

import pytest


def test_rueckweg_baut_die_alte_fassung_nur_mit_pruefsummen(eigener_ordner, monkeypatch):
    """Kleines/gleiches Update gescheitert: die alte Fassung wie bei einer Installation – ihre Pakete mit Prüfsummen,
    das Bauwerkzeug mit Prüfsummen, der Code ohne Paketverzeichnis. Kein `uv pip sync` der Liste ohne Prüfsummen."""
    from taleward_worker import motor, pfade

    geholt = []
    monkeypatch.setattr(motor, "quelle", lambda f: geholt.append(f) or {"ref": f"v{f}", "genau": True,
                                                                         "paket": f"alt-{f}.zip"})
    monkeypatch.setattr(motor, "installierte_pakete", lambda py, umgebung: {"packaging"})
    monkeypatch.setattr(motor, "pytorch_pruefsummen", lambda q: pytest.fail("PyTorch bleibt beim kleinen Rückweg"))
    aufrufe, listen = [], []

    def _uv(self, *a, **k):
        aufrufe.append(a)
        if "-r" in a:
            listen.append(Path(a[a.index("-r") + 1]).read_text())
    monkeypatch.setattr(motor.Installation, "_uv", _uv)
    pfade.motor().mkdir(parents=True, exist_ok=True)
    vorher = pfade.basis() / "update" / "vorher.txt"
    vorher.parent.mkdir(parents=True)
    vorher.write_text("taleward-server @ https://github.com/Taleward/taleward-server/archive/abc.zip\n")
    vorher.with_suffix(".json").write_text(json.dumps({"fassung": "0.4.101"}))
    inst = motor.Installation("0.4.102", alt_behalten=True)
    inst.weg = "klein"
    inst.zurueck(vorher, "0.4.101")
    assert geholt == ["0.4.101"]
    assert all(a[:2] == ("pip", "install") for a in aufrufe) and not any("sync" in a for a in aufrufe)
    for a in aufrufe:
        assert "--require-hashes" in a or {"--no-index", "--no-build-isolation"} <= set(a), a
    assert "six==1.17.0" in listen[0] and "--hash=sha256:" in listen[0]  # Pakete aus uv.lock der alten Fassung
    assert "hatchling==" in listen[1] and "packaging==" not in listen[1]  # Bauwerkzeug: nur, was fehlt
    assert aufrufe[-1][-1] == "alt-0.4.101.zip" and "--reinstall" in aufrufe[-1]
    assert json.loads((pfade.motor() / "taleward-motor.json").read_text())["fassung"] == "0.4.101"
    # ohne Angabe: die Fassung aus der Kennzeichnung von vorher
    geholt.clear()
    inst.zurueck(vorher)
    assert geholt == ["0.4.101"]
    vorher.with_suffix(".json").unlink()
    with pytest.raises(motor.MotorFehler):
        inst.zurueck(vorher)


def test_rueckweg_nach_grossem_update_ohne_download(eigener_ordner, monkeypatch):
    from taleward_worker import motor, pfade

    monkeypatch.setattr(motor.Installation, "_uv", lambda *a, **k: pytest.fail("kein Download beim Rückweg"))
    alt = pfade.basis() / "motor-alt"
    alt.mkdir(parents=True)
    (alt / "taleward-motor.json").write_text(json.dumps({"fassung": "alt"}))
    pfade.motor().mkdir(parents=True)
    (pfade.motor() / "taleward-motor.json").write_text(json.dumps({"fassung": "neu"}))
    inst = motor.Installation("neu", alt_behalten=True)
    inst.weg = "gross"
    inst.zurueck(pfade.basis() / "update" / "vorher.txt")
    assert json.loads((pfade.motor() / "taleward-motor.json").read_text())["fassung"] == "alt"
    assert not alt.exists() and not (pfade.basis() / "motor-kaputt").exists()
