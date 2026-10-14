"""Windows-Firewall für „Alles auf diesem PC“: Handys im WLAN sollen Taleward erreichen – und sonst niemand.

Eine Regel „Taleward“: eingehend, nur das Python des KI-Pakets (das Programm, das tatsächlich lauscht), nur TCP 8000,
nur im Netzprofil „Privat“, nur aus dem lokalen Subnetz. Anlegen braucht einmal Administratorrechte (Windows fragt);
Prüfen geht ohne. Ist das WLAN als „Öffentlich“ eingestuft, stellt das Programm nichts still um, sondern sagt, wo man
es ändert. Beim Deinstallieren entfernt der Installer die Regel (Merkdatei `firewall.txt`).

Alle PowerShell-Befehle gehen als -EncodedCommand (UTF-16, Base64) – Pfade mit Leerzeichen, Umlauten oder Anführungs-
zeichen können so nichts umdeuten.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
from pathlib import Path

from taleward_worker import motor, pfade

log = logging.getLogger("taleward_worker")
REGEL = "Taleward"
PORT = 8000
WINDOWS = sys.platform == "win32"


def _ps(text: str) -> str:
    """PowerShell-Zeichenkette in einfachen Anführungszeichen (dort zählt nur ' – verdoppelt)."""
    return "'" + text.replace("'", "''") + "'"


def _kodiert(skript: str) -> list[str]:
    return ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
            base64.b64encode(skript.encode("utf-16-le")).decode("ascii")]


def _powershell(skript: str, zeit: float = 30) -> str:
    vorspann = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
    r = subprocess.run(["powershell.exe", *_kodiert(vorspann + skript)], capture_output=True, timeout=zeit,
                       creationflags=0x08000000)  # ohne Konsolenfenster
    return r.stdout.decode("utf-8", errors="replace").strip()


def lauschendes_programm() -> Path | None:
    """Der Python-Interpreter, der wirklich lauscht. Das python.exe im KI-Paket ist nur ein Starter, der das Python
    aus dem Ordner „python“ der App aufruft – die Regel muss auf dieses zeigen."""
    py = pfade.motor_python()
    if not py.exists():
        return None
    try:
        r = subprocess.run([str(py), "-c", "import sys; print(getattr(sys, '_base_executable', sys.executable))"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           env=motor.saubere_umgebung(), **({"creationflags": 0x08000000} if WINDOWS else {}))
    except (OSError, subprocess.SubprocessError):
        return None
    pfad = r.stdout.strip()
    return Path(pfad) if pfad else None


def regel_lesen() -> dict | None:
    if not WINDOWS:
        return None
    skript = f"""
$r = Get-NetFirewallRule -DisplayName {_ps(REGEL)} -ErrorAction SilentlyContinue | Select-Object -First 1
if ($r) {{
  $a = $r | Get-NetFirewallApplicationFilter
  $p = $r | Get-NetFirewallPortFilter
  $d = $r | Get-NetFirewallAddressFilter
  [pscustomobject]@{{ Program = [string]$a.Program; Port = [string]$p.LocalPort; Protocol = [string]$p.Protocol;
    Profile = [string]$r.Profile; Enabled = [string]$r.Enabled; Action = [string]$r.Action;
    Remote = (@($d.RemoteAddress) -join ',') }} | ConvertTo-Json -Compress
}}"""
    try:
        text = _powershell(skript)
        return json.loads(text) if text else None
    except (OSError, subprocess.SubprocessError, ValueError):
        log.debug("Firewall-Regel nicht lesbar", exc_info=True)
        return None


def regel_passt(regel: dict | None, programm: Path | None) -> bool:
    if not regel or programm is None:
        return False
    return (str(regel.get("Program", "")).lower() == str(programm).lower() and regel.get("Port") == str(PORT)
            and regel.get("Protocol", "").upper() == "TCP" and regel.get("Enabled") == "True"
            and regel.get("Action") == "Allow" and regel.get("Profile") == "Private"
            and regel.get("Remote") == "LocalSubnet")


def stand() -> dict:
    """Für die Oberfläche: Ist die Freigabe da, wie ist das Netz eingestuft? Unter Linux nichts zu tun."""
    if not WINDOWS:
        return {"noetig": False}
    return {"noetig": True, "regel": regel_passt(regel_lesen(), lauschendes_programm()), "profile": netzprofile()}


def netzprofile() -> list[str]:
    """Einstufung der verbundenen Netze: Private, Public oder DomainAuthenticated."""
    if not WINDOWS:
        return []
    try:
        text = _powershell("Get-NetConnectionProfile | ForEach-Object { [string]$_.NetworkCategory }")
    except (OSError, subprocess.SubprocessError):
        return []
    return [z.strip() for z in text.splitlines() if z.strip()]


def anlegen_skript(programm: Path) -> str:
    """Alte Regel ersetzen; Sperr-Regeln, die Windows beim Abbrechen seiner eigenen Rückfrage für genau dieses
    Python angelegt hat, entfernen (sie gingen der Erlaubnis vor). Andere Regeln bleiben unberührt."""
    p = _ps(str(programm))
    return f"""$ErrorActionPreference = 'Stop'
Remove-NetFirewallRule -DisplayName {_ps(REGEL)} -ErrorAction SilentlyContinue
Get-NetFirewallApplicationFilter -Program {p} -ErrorAction SilentlyContinue | Get-NetFirewallRule |
  Where-Object {{ $_.Action -eq 'Block' -and $_.Direction -eq 'Inbound' }} | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName {_ps(REGEL)} -Description 'Taleward: Handys im eigenen WLAN' -Direction Inbound `
  -Action Allow -Program {p} -Protocol TCP -LocalPort {PORT} -Profile Private -RemoteAddress LocalSubnet | Out-Null
"""


def anlegen() -> bool:
    """Regel mit Administratorrechten anlegen (Windows fragt einmal). False, wenn abgelehnt oder gescheitert."""
    if not WINDOWS:
        return True
    programm = lauschendes_programm()
    if programm is None:
        return False
    ok = _als_admin("powershell.exe", " ".join(_kodiert(anlegen_skript(programm))))
    if ok:
        try:
            (pfade.basis() / "firewall.txt").write_text(REGEL, encoding="utf-8")  # für den Deinstaller
        except OSError:
            pass
    return ok and regel_passt(regel_lesen(), programm)


def _als_admin(programm: str, parameter: str, warten_ms: int = 120_000) -> bool:
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", programm, parameter, 0  # SW_HIDE
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False  # u. a. „Nein“ bei der Rückfrage von Windows
    try:
        kernel32.WaitForSingleObject(info.hProcess, warten_ms)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value == 0
    finally:
        kernel32.CloseHandle(info.hProcess)
