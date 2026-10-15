"""KI-Paket mit Prüfsummen aus uv.lock der freigegebenen Fassung (ab Worker 0.3.4)."""
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from taleward_worker.motor import pruefsummen as ECHTE_PRUEFSUMMEN  # vor dem Ersatz aus conftest.py

DATEN = Path(__file__).parent / "daten" / "kleines-projekt"
_ECHTER_KLIENT = httpx.Client


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


def _grosse_installation(monkeypatch, testmodus=False, backend="cuda", q=None, vorhanden=(), fehler=False):
    from taleward_worker import motor

    monkeypatch.setattr(motor, "quelle", lambda f: q or {"ref": f"v{f}", "genau": True, "paket": "paket.zip"})
    monkeypatch.setattr(motor, "installierte_pakete", lambda py, umgebung: set(vorhanden))
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
    if fehler:
        assert inst.phase == "fehler"
        return inst
    assert inst.phase == "fertig", inst.fehlertext
    return [x for x in aufrufe if x[0][0] == "pip"]


def test_ki_paket_mit_pruefsummen(monkeypatch):
    pip = _grosse_installation(monkeypatch)
    (a1, l1), (a2, l2), (a3, l3), (a4, _) = pip
    assert "--require-hashes" in a1 and "--torch-backend" not in a1 and "--no-deps" in a1
    assert "six==1.17.0" in l1 and "torch" not in l1
    assert "imageio-ffmpeg==0.6.0" in l1 and l1.count("--hash=") == 8  # fehlte in uv.lock → feste Fassung
    assert a2[a2.index("--torch-backend") + 1] == "cu128" and l2.strip() == "torch==2.8.0" and "--no-deps" in a2
    assert "--require-hashes" not in a2  # Fassung ohne Liste am Release: wie bisher
    _bauwerkzeug_und_taleward(a3, l3, a4)


def _bauwerkzeug_und_taleward(a3, l3, a4, ohne=()):
    from taleward_worker import motor

    assert "--require-hashes" in a3 and "--no-deps" in a3
    namen = [n for n, _ in motor.eintraege(l3)]
    assert "hatchling" in namen and not set(ohne) & set(namen)
    assert l3.count("--hash=") == sum(b.count("--hash=") for n, b in motor.eintraege(motor.BAUWERKZEUG) if n in namen)
    # der Taleward-Code: gebaut nur mit dem geprüften Bauwerkzeug, nichts aus einem Paketverzeichnis
    assert a4[-1] == "paket.zip" and {"--no-deps", "--no-build-isolation", "--no-index"} <= set(a4)


def test_bauwerkzeug_nur_was_fehlt(monkeypatch):
    pip = _grosse_installation(monkeypatch, vorhanden=("packaging", "pluggy"))
    (_, _), (_, _), (a3, l3), (a4, _) = pip
    _bauwerkzeug_und_taleward(a3, l3, a4, ohne=("packaging", "pluggy"))
    from taleward_worker import motor

    alles = {n for n, _ in motor.eintraege(motor.BAUWERKZEUG)}
    pip = _grosse_installation(monkeypatch, vorhanden=alles)
    assert len(pip) == 3 and "--no-build-isolation" in pip[-1][0]  # alles da: kein zusätzlicher Aufruf
    for _, block in motor.eintraege(motor.BAUWERKZEUG):
        assert "==" in block.splitlines()[0] and block.count("--hash=sha256:") >= 1


def test_testmodus_mit_pruefsummen(monkeypatch):
    from taleward_worker import motor

    pip = _grosse_installation(monkeypatch, testmodus=True)
    (a1, l1), (a2, l2), (a3, _) = pip
    assert "--require-hashes" in a1 and "torch" not in l1 and "imageio-ffmpeg" in l1
    _bauwerkzeug_und_taleward(a2, l2, a3)  # keine ungeprüften Abhängigkeiten, auch beim Bauen nicht
    # steht imageio-ffmpeg in uv.lock, gilt dessen Eintrag
    eigen = "imageio-ffmpeg==0.7.0 \\\n    --hash=sha256:" + "d" * 64 + "\n"
    monkeypatch.setattr(motor, "pruefsummen", lambda q, umgebung, ordner: (
        "six==1.17.0 \\\n    --hash=sha256:" + "a" * 64 + "\n", eigen))
    (a1, l1), *_ = _grosse_installation(monkeypatch, testmodus=True)
    assert "imageio-ffmpeg==0.7.0" in l1 and "0.6.0" not in l1


def test_quelle_nennt_projekt_und_lock():
    from taleward_worker import motor

    q = motor._adressen("v0.4.100", "a" * 40)
    assert q["lock"].endswith(f"/{'a' * 40}/uv.lock") and q["projekt"].endswith(f"/{'a' * 40}/pyproject.toml")


# ---------------------------------------------------------------- PyTorch mit Prüfsummen aus der Liste am Release
_LISTE = ("# Prüfsummen\n"
          f"cu128 torch==2.8.0+cu128 --hash=sha256:{'a' * 64} --hash=sha256:{'b' * 64}\n"
          f"cpu torch==2.8.0+cpu --hash=sha256:{'c' * 64}\n"
          f"cpu torch==2.8.0 --hash=sha256:{'d' * 64}\n")


def _release(monkeypatch, inhalt: bytes, sha: str | None = None):
    import hashlib

    from taleward_worker import motor

    geholt = []

    def antwort(anfrage):
        geholt.append(str(anfrage.url))
        return httpx.Response(200, content=inhalt)
    monkeypatch.setattr(motor.httpx, "Client",
                        lambda **k: _ECHTER_KLIENT(transport=httpx.MockTransport(antwort), **k))
    return {"ref": "v0.4.103", "genau": True, "paket": "paket.zip",
            "release": "https://github.com/Taleward/taleward-server/releases/download/v0.4.103",
            "anhaenge": {"pytorch-pruefsummen.txt": sha or hashlib.sha256(inhalt).hexdigest()}}, geholt


def test_pytorch_mit_pruefsummen_aus_der_freigabe(monkeypatch):
    q, geholt = _release(monkeypatch, _LISTE.encode())
    pip = _grosse_installation(monkeypatch, q=q)
    a2, l2 = pip[1]
    assert "--require-hashes" in a2 and a2[a2.index("--torch-backend") + 1] == "cu128"
    assert l2.strip() == f"torch==2.8.0+cu128 --hash=sha256:{'a' * 64} --hash=sha256:{'b' * 64}"
    assert geholt == ["https://github.com/Taleward/taleward-server/releases/download/v0.4.103/pytorch-pruefsummen.txt"]
    q, _ = _release(monkeypatch, _LISTE.encode())
    a2, l2 = _grosse_installation(monkeypatch, q=q, backend="cpu")[1]
    assert l2.strip() == f"torch==2.8.0+cpu --hash=sha256:{'c' * 64}" and "--require-hashes" in a2


def test_pytorch_liste_muss_zur_freigabe_passen(monkeypatch):
    q, _ = _release(monkeypatch, _LISTE.encode(), sha="e" * 64)  # ausgetauschte Liste
    inst = _grosse_installation(monkeypatch, q=q, fehler=True)
    assert "passt nicht zur Freigabe" in inst.fehlertext
    q, _ = _release(monkeypatch, _LISTE.replace("cu128 torch", "cu126 torch").encode())  # Variante fehlt
    inst = _grosse_installation(monkeypatch, q=q, fehler=True)
    assert "Keine Prüfsumme für torch 2.8.0+cu128" in inst.fehlertext
    q, _ = _release(monkeypatch, b"cu128 torch==2.8.0+cu128 --hash=md5:abc\n")  # unlesbar
    assert "nicht lesbar" in _grosse_installation(monkeypatch, q=q, fehler=True).fehlertext


def test_pytorch_mit_bedingung():
    from taleward_worker import motor

    liste = motor.pytorch_liste_lesen(f"cu128 torchvision==0.23.0+cu128 --hash=sha256:{'f' * 64}\n")
    zeile = motor.pytorch_mit_pruefsummen("torchvision==0.23.0 ; sys_platform == 'win32'\n", liste, "cu128")
    assert zeile == f"torchvision==0.23.0+cu128 ; sys_platform == 'win32' --hash=sha256:{'f' * 64}\n"


def test_quelle_reicht_die_anhaenge_der_freigabe_weiter():
    from taleward_worker import motor

    q = motor._adressen("v0.4.103", "a" * 40, {"pytorch-pruefsummen.txt": "b" * 64})
    assert q["anhaenge"] == {"pytorch-pruefsummen.txt": "b" * 64}
    assert q["release"].endswith("/releases/download/v0.4.103")
