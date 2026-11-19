"""Worker 0.3.26: Mindestfassung für das KI-Paket und Obergrenze beim Laden des eigenen Updates."""
import pytest


def test_altes_ki_paket_wird_nicht_installiert_auch_wenn_es_freigegeben_ist(monkeypatch):
    from taleward_worker import freigabe as fg, motor
    from tests.freigabe_hilfe import freigabe

    gefragt = []

    def von_github(repo, tag, **k):
        gefragt.append(tag)
        text, sig = freigabe(repo, tag, commit="c" * 40)
        return fg.lesen(text, sig, repo, tag)

    monkeypatch.setattr(fg, "von_github", von_github)
    for alt in ("0.4.169", "0.4.38", "0.3.999"):
        with pytest.raises(motor.MotorFehler) as e:
            motor.quelle(alt)  # gültig unterschrieben, aber unter der Mindestfassung
        assert e.value.code == "server_zu_alt"
    assert gefragt == []  # dafür wird nicht einmal nachgefragt
    assert motor.quelle(motor.MINDEST_MOTOR)["paket"].endswith("c" * 40 + ".zip")
    assert motor.quelle("0.5.0")["ref"] == "v0.5.0"
    # nur der Rückweg nach einem gescheiterten Update darf den Stand von vorher holen
    assert motor.quelle("0.4.169", rueckweg=True)["ref"] == "v0.4.169"
    assert motor.zu_alt("0.4.169") and not motor.zu_alt("0.4.170") and not motor.zu_alt(None)


def test_vorhandenes_altes_ki_paket_gilt_als_unpassend(monkeypatch):
    from taleward_worker import einzelplatz, motor, oberflaeche

    monkeypatch.delenv("TALEWARD_MOTOR", raising=False)
    app = oberflaeche.WorkerApp.__new__(oberflaeche.WorkerApp)
    app.e = type("E", (), {"testmodus": False, "geraet": "cpu"})()
    app.server_version = "0.4.169"  # ein Server, der die alte Fassung verlangt
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": "0.4.169", "backend": "cpu"})
    assert not app.motor_passt()
    app.server_version = motor.MINDEST_MOTOR
    monkeypatch.setattr(motor, "installiert", lambda: {"fassung": motor.MINDEST_MOTOR, "backend": "cpu"})
    assert app.motor_passt()
    assert einzelplatz.MINDEST_SERVER == motor.MINDEST_MOTOR


def test_texte_fuer_zu_alten_server():
    from pathlib import Path

    import taleward_worker

    texte = (Path(taleward_worker.__file__).parent / "ui" / "texte.js").read_text(encoding="utf-8")
    assert texte.count("f_server_zu_alt:") == 2  # Deutsch und Englisch


def test_update_download_hat_eine_obergrenze(monkeypatch, tmp_path):
    import httpx

    from taleward_worker import pfade, selbstupdate

    monkeypatch.setattr(pfade, "basis", lambda: tmp_path)
    monkeypatch.setattr(selbstupdate, "MAX_INSTALLER", 1000)
    u = selbstupdate.Aktualisierung.__new__(selbstupdate.Aktualisierung)
    u.angebot = {"version": "9.9.9", "url": "/downloads/worker-windows/9.9.9/TalewardWorker-Setup.exe"}
    u.server, u.token, u.phase, u.anteil = "http://server.example", "t", "", 0.0
    monkeypatch.setattr(u, "_freigabe", lambda: type("F", (), {"dateien": {"TalewardWorker-Setup.exe": "0" * 64}})())

    class Antwort:
        headers = {}

        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            for _ in range(50):
                yield b"x" * 100

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Antwort())
    with pytest.raises(RuntimeError, match="größer als ein Installer"):
        u._laden()
    assert not list((tmp_path / "updates").glob("*.exe"))
