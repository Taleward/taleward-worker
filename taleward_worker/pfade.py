"""Wo die App ihre Dateien ablegt – alles in einem Ordner je Benutzer, nichts im Programmordner."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

WINDOWS = sys.platform == "win32"


def basis() -> Path:
    """Windows: %LOCALAPPDATA%\\Taleward Worker, Linux: ~/.local/share/taleward-worker.
    TALEWARD_WORKER_HOME überschreibt das (Tests, mehrere Worker auf einem PC)."""
    eigen = os.environ.get("TALEWARD_WORKER_HOME")
    if eigen:
        p = Path(eigen)
    elif WINDOWS:
        p = Path(user_data_dir("Taleward Worker", appauthor=False, roaming=False))
    else:
        p = Path(user_data_dir("taleward-worker", appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def motor() -> Path:
    """Python-Umgebung mit dem KI-Paket."""
    return basis() / "motor"


def motor_python() -> Path:
    return motor() / ("Scripts/python.exe" if WINDOWS else "bin/python")


def motor_chronik() -> Path:
    return motor() / ("Scripts/chronik.exe" if WINDOWS else "bin/chronik")


def werkzeuge() -> Path:
    """Eigene Werkzeuge der App (ffmpeg als Verknüpfung/Kopie), vorn im PATH des Motors."""
    p = basis() / "werkzeuge"
    p.mkdir(parents=True, exist_ok=True)
    return p


def modelle() -> Path:
    return basis() / "modelle"


def arbeit() -> Path:
    return basis() / "arbeit"


def protokolle() -> Path:
    p = basis() / "protokolle"
    p.mkdir(parents=True, exist_ok=True)
    return p


def einstellungen() -> Path:
    return basis() / "einstellungen.json"


def einzelplatz_daten() -> Path:
    """„Alles auf diesem PC“: Daten von Taleward (Kampagnen, Aufnahmen, Konten) – getrennt vom Programm, damit sie
    ein Neuinstallieren überstehen. Windows: %LOCALAPPDATA%\\Taleward\\Daten (wie der Server es selbst vorsieht)."""
    if os.environ.get("TALEWARD_WORKER_HOME"):
        p = basis() / "daten"
    elif WINDOWS:
        p = Path(user_data_dir("Taleward", appauthor=False, roaming=False)) / "Daten"
    else:
        p = Path(user_data_dir("taleward", appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def startschluessel() -> Path:
    """Einmaliger Schlüssel für das erste Konto – der Server liest und löscht die Datei beim Start."""
    return basis() / "start.key"


def worker_schluessel() -> Path:
    """Schlüssel des Workers – der Server schreibt ihn bei jedem Start neu."""
    return basis() / "worker.key"


def server_stopp() -> Path:
    """Liegt diese Datei, beendet sich der Server geordnet."""
    return basis() / "server.stopp"


def fenster_speicher() -> Path:
    """Speicher des Programmfensters (Anmeldung der App, Aufnahmen bis zum Hochladen)."""
    p = basis() / "fenster"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ressourcen() -> Path:
    """Mitgelieferte Dateien (Oberfläche, Symbole, uv). Im PyInstaller-Paket unter sys._MEIPASS."""
    wurzel = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return wurzel / "taleward_worker" if (wurzel / "taleward_worker").is_dir() else Path(__file__).resolve().parent


def ordnergroesse(p: Path) -> int:
    gesamt = 0
    for wurzel, _, dateien in os.walk(p):
        for d in dateien:
            try:
                gesamt += os.lstat(os.path.join(wurzel, d)).st_size
            except OSError:
                pass
    return gesamt


def dokumente() -> Path:
    """Der Ordner „Dokumente“, wie Windows ihn führt (auch wenn OneDrive ihn verlegt hat); sonst ~/Documents bzw. ~."""
    if WINDOWS:
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            kennung = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")  # FOLDERID_Documents
            guid = (ctypes.c_ubyte * 16).from_buffer_copy(kennung.bytes_le)
            zeiger = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
            shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                     ctypes.POINTER(ctypes.c_wchar_p)]
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(zeiger)) == 0 and zeiger.value:
                ordner = Path(zeiger.value)
                ctypes.windll.ole32.CoTaskMemFree(zeiger)  # type: ignore[attr-defined]
                if ordner.is_dir():
                    return ordner
        except Exception:  # noqa: BLE001 – dann der übliche Ordner
            pass
    for ordner in (Path.home() / "Documents", Path.home()):
        if ordner.is_dir():
            return ordner
    return Path.home()
