"""Worker 0.3.19: Paketlisten werden außerhalb des uv-Zwischenspeichers erzeugt (neuere uv lehnen ein Projekt im
Zwischenspeicher ab) – sonst scheitert jedes Update des KI-Pakets nach wenigen Sekunden. Der Grund steht jetzt mit im
Protokoll."""
import types

from taleward_worker import motor, pfade, serverupdate


def test_listen_ausserhalb_des_zwischenspeichers(tmp_path, monkeypatch):
    monkeypatch.setenv("TALEWARD_WORKER_HOME", str(tmp_path))
    gesehen = {}

    def pruefsummen(q, umgebung, ordner):
        gesehen["ordner"], gesehen["cache"] = ordner, umgebung["UV_CACHE_DIR"]
        return "", ""

    monkeypatch.setattr(motor, "pruefsummen", pruefsummen)
    inst = motor.Installation("0.4.116", testmodus=True, backend="cpu")
    inst._listen({"ref": "v0.4.116"})
    cache = pfade.basis() / "cache"
    assert str(cache) == gesehen["cache"]
    assert cache not in gesehen["ordner"].parents and gesehen["ordner"] != cache


def test_grund_steht_im_protokoll():
    inst = types.SimpleNamespace(fehlertext="$ uv export\nerror: The project directory `.` is inside the cache directory")
    assert serverupdate.grund_kurz(inst) == " (error: The project directory `.` is inside the cache directory)"
    assert serverupdate.grund_kurz(types.SimpleNamespace(fehlertext="")) == ""
