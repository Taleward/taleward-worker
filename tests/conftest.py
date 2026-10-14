import pytest


@pytest.fixture(autouse=True)
def eigener_ordner(tmp_path, monkeypatch):
    monkeypatch.setenv("TALEWARD_WORKER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("TALEWARD_MOTOR", raising=False)
    monkeypatch.delenv("TALEWARD_MOTOR_QUELLE", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _freigabe_testschluessel(monkeypatch):
    """Freigaben in Tests mit einem eigenen Schlüssel (tests/freigabe_hilfe.py) statt dem echten."""
    from taleward_worker import freigabe
    from tests.freigabe_hilfe import OEFFENTLICH

    monkeypatch.setattr(freigabe, "OEFFENTLICHER_SCHLUESSEL", OEFFENTLICH)


@pytest.fixture(autouse=True)
def _pruefsummen(monkeypatch):
    """Paketlisten mit Prüfsummen ohne Netz und ohne uv: kleine feste Listen. Die echte Erzeugung prüft
    test_pruefsummen.py."""
    from taleward_worker import motor

    basis = "six==1.17.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
    ki = basis + "torch==2.8.0 \\\n    --hash=sha256:" + "b" * 64 + "\n"
    monkeypatch.setattr(motor, "pruefsummen", lambda q, umgebung, ordner: (basis, ki))
