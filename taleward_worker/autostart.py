"""Mit dem Computer starten – Windows: Eintrag unter HKCU\\...\\Run, Linux: ~/.config/autostart.
Der Windows-Installer setzt denselben Eintrag, wenn der Haken bei der Installation gesetzt ist."""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

NAME = "Taleward Worker"
SCHLUESSEL = r"Software\Microsoft\Windows\CurrentVersion\Run"


def befehl() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--hintergrund"]
    starter = os.environ.get("TALEWARD_WORKER_STARTER")  # Linux-Installation: Startskript
    if starter:
        return [starter, "--hintergrund"]
    return [sys.executable, "-m", "taleward_worker", "--hintergrund"]


def _linux_datei() -> Path:
    basis = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return basis / "autostart" / "taleward-worker.desktop"


def aktiv() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCHLUESSEL) as k:
                winreg.QueryValueEx(k, NAME)
                return True
        except OSError:
            return False
    return _linux_datei().exists()


def eintrag() -> str | None:
    """Der gespeicherte Startbefehl (nur Windows), None ohne Eintrag."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCHLUESSEL) as k:
            return str(winreg.QueryValueEx(k, NAME)[0])
    except OSError:
        return None


ALTER_NAME = "talewardworker.exe"  # Programmdatei bis Worker 0.3.9


def programm_im_eintrag(text: str) -> str:
    """Programmpfad aus einem Run-Eintrag („"C:\\…\\X.exe" --hintergrund“ oder ohne Anführungszeichen)."""
    text = text.strip()
    if text.startswith('"'):
        return text[1:].split('"', 1)[0]
    return text.split(" ", 1)[0]


def alter_eintrag_hier(alt: str, programm: str) -> bool:
    """Zeigt der Eintrag auf die alte Programmdatei (TalewardWorker.exe) im selben Ordner wie das laufende Programm?
    Nur dann ist es dieselbe Installation – eine zweite Kopie (etwa aus „Downloads“) übernimmt den Eintrag nicht."""
    pfad = programm_im_eintrag(alt).replace("/", "\\")
    ordner, _, name = pfad.rpartition("\\")
    hier = programm.replace("/", "\\").rpartition("\\")[0]
    return name.lower() == ALTER_NAME and bool(ordner) and ordner.lower() == hier.lower()


def nachfuehren() -> bool:
    """Zeigt ein vorhandener Eintrag auf TalewardWorker.exe (vor der Umbenennung in Taleward.exe) im selben Ordner,
    auf das laufende Programm umschreiben. Ohne Eintrag bleibt es aus, andere Einträge bleiben, wie sie sind.
    True, wenn umgeschrieben."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    import subprocess

    alt = eintrag()
    if alt is None or alt == subprocess.list2cmdline(befehl()) or not alter_eintrag_hier(alt, sys.executable):
        return False
    setzen(True)
    return True


def setzen(an: bool) -> None:
    if sys.platform == "win32":
        import subprocess
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCHLUESSEL, 0, winreg.KEY_SET_VALUE) as k:
            if an:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(befehl()))
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except OSError:
                    pass
        return
    datei = _linux_datei()
    if not an:
        datei.unlink(missing_ok=True)
        return
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("\n".join([
        "[Desktop Entry]", "Type=Application", f"Name={NAME}",
        "Comment=Verarbeitet Aufnahmen für Taleward im Hintergrund",
        "Exec=" + " ".join(shlex.quote(t) for t in befehl()),
        "Icon=taleward-worker", "X-GNOME-Autostart-enabled=true", "Terminal=false", ""]), encoding="utf-8")
