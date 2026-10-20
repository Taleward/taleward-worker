"""Worker 0.3.10: Kopplungscode nimmt Strich und eingefügten Text an; bei „nur Prozessor“ keine CUDA-Hilfspakete."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent
UI = WURZEL / "taleward_worker" / "ui"


def _code_formen(*eingaben: str) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node fehlt")
    js = (UI / "app.js").read_text(encoding="utf-8")
    m = re.search(r"^  function codeFormen\(text\) \{.*?^  \}$", js, re.S | re.M)
    assert m, "codeFormen fehlt in app.js"
    prog = m.group(0) + f"\nconsole.log(JSON.stringify({json.dumps(list(eingaben))}.map(codeFormen)));"
    r = subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_kopplungscode_beim_tippen_mit_und_ohne_strich():
    assert _code_formen("a", "abcd", "abcd-", "abcde", "ABCD-E", "abcd-efgh", "abcdefgh", "abcdefghij") == [
        "A", "ABCD", "ABCD-", "ABCD-E", "ABCD-E", "ABCD-EFGH", "ABCD-EFGH", "ABCD-EFGH"]
    # Strich an falscher Stelle oder doppelt: wird still gerade gerückt
    assert _code_formen("ab-cd", "abcd--e", " abcd - efgh ") == ["ABCD", "ABCD-E", "ABCD-EFGH"]


def test_kopplungscode_eingefuegt_mit_text_drumherum():
    assert _code_formen("Kopplungscode: K7QM-2XPA (gilt 10 Minuten)", "code k7qm-2xpa.", "»K7QM-2XPA«") == [
        "K7QM-2XPA", "K7QM-2XPA", "K7QM-2XPA"]


def test_kopplungsfeld_ohne_laengengrenze():
    html = (UI / "index.html").read_text(encoding="utf-8")
    feld = re.search(r'<input id="code"[^>]*>', html).group(0)
    assert "maxlength" not in feld
    assert "codeFormen(e.target.value)" in (UI / "app.js").read_text(encoding="utf-8")


def test_server_koppeln_findet_den_code_im_text(monkeypatch):
    from taleward_worker import server

    gesendet = {}

    class Antwort:
        status_code = 201

        def json(self):
            return {"token": "t"}

    class Klient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json):
            gesendet.update(json)
            return Antwort()

    monkeypatch.setattr(server, "_klient", lambda: Klient())
    monkeypatch.setattr(server, "_json", lambda r: r.json())
    for eingabe in ("Kopplungscode: k7qm-2xpa (gilt 10 Minuten)", "k7qm2xpa", " K7QM - 2XPA "):
        server.koppeln("https://server.example", eingabe, "PC")
        assert gesendet["code"] == "K7QM-2XPA", eingabe


def test_nur_prozessor_ohne_cuda_hilfspakete():
    from taleward_worker import motor

    h = " \\\n    --hash=sha256:" + "d" * 64
    text = (f"six==1.17.0{h}\ncuda-bindings==12.9.4 ; sys_platform == 'linux'{h}\n"
            f"cuda-pathfinder==1.3.3 ; sys_platform == 'linux'{h}\nnvidia-nccl-cu12==2.27.5 ; sys_platform == 'linux'{h}\n")
    geprueft, _ = motor.pruefsummen_aufteilen(text, mit_cuda=False)
    assert [n for n, _ in motor.eintraege(geprueft)] == ["six"]
    geprueft, _ = motor.pruefsummen_aufteilen(text)
    assert [n for n, _ in motor.eintraege(geprueft)] == ["six", "cuda-bindings", "cuda-pathfinder", "nvidia-nccl-cu12"]
