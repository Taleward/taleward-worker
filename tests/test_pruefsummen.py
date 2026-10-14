"""KI-Paket mit Prüfsummen aus uv.lock der freigegebenen Fassung (ab Worker 0.3.4)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from taleward_worker.motor import pruefsummen as ECHTE_PRUEFSUMMEN  # vor dem Ersatz aus conftest.py

DATEN = Path(__file__).parent / "daten" / "kleines-projekt"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv fehlt")
def test_paketlisten_mit_pruefsummen_aus_uv_lock(tmp_path, monkeypatch):
    from taleward_worker import motor

    monkeypatch.setattr(motor, "uv_programm", lambda: shutil.which("uv"))
    q = {"projekt": str(DATEN / "pyproject.toml"), "lock": str(DATEN / "uv.lock")}
    basis, ki = ECHTE_PRUEFSUMMEN(q, umgebung=motor.saubere_umgebung() | {"UV_NO_CONFIG": "1"},
                                           ordner=tmp_path / "lock")
    assert [n for n, _ in motor.eintraege(basis)] == ["six"]
    assert [n for n, _ in motor.eintraege(ki)] == ["idna", "six"]
    assert "--hash=sha256:4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274" in basis
    # ohne gültige uv.lock keine Liste – und kein Rückfall auf eine Liste ohne Prüfsummen
    kaputt = tmp_path / "kaputt.lock"
    kaputt.write_text("das ist keine uv.lock")
    with pytest.raises(motor.MotorFehler) as e:
        ECHTE_PRUEFSUMMEN({**q, "lock": str(kaputt)}, umgebung=motor.saubere_umgebung(),
                                   ordner=tmp_path / "lock2")
    assert e.value.code == "installation"


def test_pruefsummen_aufteilen():
    from taleward_worker import motor

    h = " \\\n    --hash=sha256:" + "c" * 64
    text = (f"six==1.17.0{h}\ntorch==2.8.0{h}\ntorchaudio==2.8.0{h}\ntorchvision==0.23.0 ; sys_platform == 'win32'{h}\n"
            f"torchcodec==0.7.0{h}\nnvidia-cudnn-cu12==9.10.2.21 ; sys_platform == 'linux'{h}\n"
            f"triton==3.4.0 ; sys_platform == 'linux'{h}\n")
    geprueft, pytorch = motor.pruefsummen_aufteilen(text)
    assert pytorch.splitlines() == ["torch==2.8.0", "torchaudio==2.8.0", "torchvision==0.23.0 ; sys_platform == 'win32'"]
    assert [n for n, _ in motor.eintraege(geprueft)] == ["six", "torchcodec", "nvidia-cudnn-cu12", "triton"]
    assert geprueft.count("--hash=") == 4
    # nur Prozessor: ohne die CUDA-Bibliotheken
    geprueft, _ = motor.pruefsummen_aufteilen(text, mit_cuda=False)
    assert [n for n, _ in motor.eintraege(geprueft)] == ["six", "torchcodec"]
    with pytest.raises(motor.MotorFehler):
        motor.pruefsummen_aufteilen("six==1.17.0\n")  # ohne Prüfsumme nie installieren
    assert [n for n, _ in motor.eintraege(motor.IMAGEIO_FFMPEG)] == ["imageio-ffmpeg"]


def _grosse_installation(monkeypatch, testmodus=False, backend="cuda"):
    from taleward_worker import motor

    monkeypatch.setattr(motor, "quelle", lambda f: {"ref": f"v{f}", "genau": True, "paket": "paket.zip"})
    monkeypatch.setattr(motor, "installiert", lambda: None)
    monkeypatch.setattr(motor, "ffmpeg_einrichten", lambda py: None)
    monkeypatch.setattr(motor, "home_festlegen", lambda venv, py: None)
    monkeypatch.setattr(motor, "torch_pruefen", lambda py: {"cuda": "12.8"})
    monkeypatch.setattr(motor, "_python_pruefen", lambda *a, **k: subprocess.CompletedProcess(a, 0, "ok\n", ""))
    monkeypatch.setattr(motor.Installation, "_python_einrichten", lambda self: Path("python"))
    aufrufe = []

    def _uv(self, *a, **k):
        listen = Path(a[a.index("-r") + 1]).read_text() if "-r" in a else ""
        aufrufe.append((a, listen))
        if a[0] == "venv":
            Path(a[1]).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(motor.Installation, "_uv", _uv)
    inst = motor.Installation("0.4.100", testmodus=testmodus, backend=backend)
    inst._lauf()
    assert inst.phase == "fertig", inst.fehlertext
    return [x for x in aufrufe if x[0][0] == "pip"]


def test_ki_paket_mit_pruefsummen(monkeypatch):
    pip = _grosse_installation(monkeypatch)
    (a1, l1), (a2, l2), (a3, _) = pip
    assert "--require-hashes" in a1 and "--torch-backend" not in a1 and "--no-deps" in a1
    assert "six==1.17.0" in l1 and "torch" not in l1
    assert "imageio-ffmpeg==0.6.0" in l1 and l1.count("--hash=") == 8  # fehlte in uv.lock → feste Fassung
    assert a2[a2.index("--torch-backend") + 1] == "cu128" and l2.strip() == "torch==2.8.0" and "--no-deps" in a2
    assert a3[-1] == "paket.zip" and "--no-deps" in a3


def test_testmodus_mit_pruefsummen(monkeypatch):
    from taleward_worker import motor

    pip = _grosse_installation(monkeypatch, testmodus=True)
    (a1, l1), (a2, _) = pip
    assert "--require-hashes" in a1 and "torch" not in l1 and "imageio-ffmpeg" in l1
    assert a2[-1] == "paket.zip" and "--no-deps" in a2  # keine ungeprüften Abhängigkeiten mehr
    # steht imageio-ffmpeg in uv.lock, gilt dessen Eintrag
    eigen = "imageio-ffmpeg==0.7.0 \\\n    --hash=sha256:" + "d" * 64 + "\n"
    monkeypatch.setattr(motor, "pruefsummen", lambda q, umgebung, ordner: (
        "six==1.17.0 \\\n    --hash=sha256:" + "a" * 64 + "\n", eigen))
    (a1, l1), _ = _grosse_installation(monkeypatch, testmodus=True)
    assert "imageio-ffmpeg==0.7.0" in l1 and "0.6.0" not in l1


def test_quelle_nennt_projekt_und_lock():
    from taleward_worker import motor

    q = motor._adressen("v0.4.100", "a" * 40)
    assert q["lock"].endswith(f"/{'a' * 40}/uv.lock") and q["projekt"].endswith(f"/{'a' * 40}/pyproject.toml")
