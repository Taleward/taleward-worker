"""Rückweg des KI-Pakets nach einem gescheiterten Update von „Alles auf diesem PC“ (motor.Installation)."""
import json
import sys
from pathlib import Path

import pytest


def _rad(ordner: Path, fassung: str) -> Path:
    """Kleinstes Python-Paket als Wheel – „Taleward-Code“ in zwei Fassungen, ohne Netz installierbar."""
    import base64
    import hashlib
    import zipfile

    name = f"taleward_probe-{fassung}"
    dateien = {"taleward_probe/__init__.py": f"FASSUNG = {fassung!r}\n",
               f"{name}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: taleward-probe\nVersion: {fassung}\n",
               f"{name}.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = []
    for pfad, inhalt in dateien.items():
        roh = inhalt.encode()
        h = base64.urlsafe_b64encode(hashlib.sha256(roh).digest()).rstrip(b"=").decode()
        record.append(f"{pfad},sha256={h},{len(roh)}")
    record.append(f"{name}.dist-info/RECORD,,")
    ziel = ordner / f"{name}-py3-none-any.whl"
    with zipfile.ZipFile(ziel, "w") as z:
        for pfad, inhalt in dateien.items():
            z.writestr(pfad, inhalt)
        z.writestr(f"{name}.dist-info/RECORD", "\n".join(record) + "\n")
    return ziel


def test_rueckweg_ueber_die_paketliste(eigener_ordner, monkeypatch):
    import shutil
    import subprocess

    from taleward_worker import motor, pfade

    if shutil.which("uv") is None:
        pytest.skip("uv fehlt")
    monkeypatch.setattr(motor, "uv_programm", lambda: shutil.which("uv"))
    pfade.basis().mkdir(parents=True, exist_ok=True)
    subprocess.run(["uv", "venv", "-q", "--python", sys.executable, str(pfade.motor())], check=True)
    eins, zwei = _rad(eigener_ordner, "1.0"), _rad(eigener_ordner, "2.0")
    py = str(pfade.motor_python())

    def fassung():
        return subprocess.run([py, "-c", "import taleward_probe as t; print(t.FASSUNG)"], capture_output=True,
                              text=True).stdout.strip()
    subprocess.run(["uv", "pip", "install", "-q", "--python", py, str(eins)], check=True)
    (pfade.motor() / "taleward-motor.json").write_text(json.dumps({"fassung": "1.0"}))
    inst = motor.Installation("2.0", backend="cpu", alt_behalten=True)
    vorher = pfade.basis() / "update" / "vorher.txt"
    inst.stand_festhalten(vorher)
    assert "taleward-probe @ file:" in vorher.read_text()
    subprocess.run(["uv", "pip", "install", "-q", "--python", py, str(zwei)], check=True)  # „Update“ im Motor
    (pfade.motor() / "taleward-motor.json").write_text(json.dumps({"fassung": "2.0"}))
    assert fassung() == "2.0"
    inst.weg = "gleich"
    inst.zurueck(vorher)
    assert fassung() == "1.0"
    assert motor.installiert()["fassung"] == "1.0"


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
