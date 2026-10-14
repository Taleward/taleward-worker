"""Windows-Firewall für „Alles auf diesem PC“: Handys im WLAN sollen Taleward erreichen – und sonst niemand.

Eine Regel (Name `Taleward-Einzelplatz`, angezeigt als „Taleward“): eingehend, nur das Python des KI-Pakets (das
Programm, das tatsächlich lauscht), nur TCP 8000, nur im Netzprofil „Privat“, nur aus dem lokalen Subnetz. Anlegen
braucht einmal Administratorrechte (Windows fragt); Prüfen geht ohne. Andere eingehende Regeln für genau dieses Python
(z. B. die, die Windows bei seiner eigenen Rückfrage anlegt, auch für „Öffentlich“) ersetzt das Anlegen; solange es sie
gibt, gilt die Freigabe als nicht in Ordnung. Ist das WLAN als „Öffentlich“ eingestuft, stellt das Programm nichts still
um, sondern sagt, wo man es ändert. Beim Deinstallieren entfernt der Installer die Regel (Merkdatei `firewall.txt`, wird
vor dem Anlegen geschrieben).

Grenze: Die Regel gilt für das Python im Benutzerordner. Jedes Programm, das dieser Windows-Nutzer mit genau diesem
Python startet, dürfte auf TCP 8000 aus dem Heimnetz lauschen – solange Taleward läuft, ist der Anschluss belegt.

Alle PowerShell-Befehle gehen als -EncodedCommand (UTF-16, Base64); Pfade und Namen stehen darin nochmals als Base64
(UTF-8) – Leerzeichen, Umlaute oder Anführungszeichen jeder Art können so nichts umdeuten.
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
REGEL = "Taleward"                    # Anzeigename
REGEL_NAME = "Taleward-Einzelplatz"   # eindeutiger Name – entfernt wird nur diese Regel
BESCHREIBUNG = "Taleward: Handys im eigenen WLAN"
PORT = 8000
WINDOWS = sys.platform == "win32"


def _ps(text: str) -> str:
    """Zeichenkette für PowerShell, ohne dass irgendein Zeichen darin etwas bedeutet: Base64 (UTF-8), erst in
    PowerShell zurückverwandelt. (PowerShell wertet auch typografische Anführungszeichen ‘ ’ ‚ ‛ als Ende einer
    Zeichenkette – ein bloßes Verdoppeln von ' reicht nicht.)"""
    roh = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{roh}')))"


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


def regel_lesen(programm: Path | None = None) -> dict | None:
    if not WINDOWS:
        return None
    fremd = "0"
    if programm is not None:  # andere eingehende, eingeschaltete Regeln für genau dieses Programm
        fremd = (f"@(Get-NetFirewallApplicationFilter -Program {_ps(str(programm))} -ErrorAction SilentlyContinue | "
                 f"Get-NetFirewallRule | Where-Object {{ $_.Name -ne {_ps(REGEL_NAME)} -and $_.Direction -eq 'Inbound' "
                 f"-and [string]$_.Enabled -eq 'True' }}).Count")
    skript = f"""
$r = Get-NetFirewallRule -Name {_ps(REGEL_NAME)} -ErrorAction SilentlyContinue | Select-Object -First 1
if ($r) {{
  $a = $r | Get-NetFirewallApplicationFilter
  $p = $r | Get-NetFirewallPortFilter
  $d = $r | Get-NetFirewallAddressFilter
  [pscustomobject]@{{ Program = [string]$a.Program; Port = [string]$p.LocalPort; Protocol = [string]$p.Protocol;
    Profile = [string]$r.Profile; Enabled = [string]$r.Enabled; Action = [string]$r.Action;
    Remote = (@($d.RemoteAddress) -join ','); Fremd = [int]({fremd}) }} | ConvertTo-Json -Compress
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
            and regel.get("Remote") == "LocalSubnet" and not regel.get("Fremd"))


def stand() -> dict:
    """Für die Oberfläche: Ist die Freigabe da, wie ist das Netz eingestuft? Unter Linux nichts zu tun."""
    if not WINDOWS:
        return {"noetig": False}
    programm = lauschendes_programm()
    return {"noetig": True, "regel": regel_passt(regel_lesen(programm), programm), "profile": netzprofile()}


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
    """Eigene Regel ersetzen; alle anderen eingehenden Regeln für genau dieses Python entfernen – Sperr-Regeln, die
    Windows beim Abbrechen seiner eigenen Rückfrage anlegt (gingen der Erlaubnis vor), und Erlaubnis-Regeln aus dieser
    Rückfrage (oft auch für „Öffentlich“). Regeln für andere Programme bleiben unberührt; ebenso fremde Regeln, die nur
    zufällig „Taleward“ heißen. Ältere Fassungen (bis 0.3.2) legten die Regel nur mit Anzeigenamen an."""
    p = _ps(str(programm))
    return f"""$ErrorActionPreference = 'Stop'
Remove-NetFirewallRule -Name {_ps(REGEL_NAME)} -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName {_ps(REGEL)} -ErrorAction SilentlyContinue |
  Where-Object {{ $_.Description -eq {_ps(BESCHREIBUNG)} }} | Remove-NetFirewallRule
Get-NetFirewallApplicationFilter -Program {p} -ErrorAction SilentlyContinue | Get-NetFirewallRule |
  Where-Object {{ $_.Direction -eq 'Inbound' }} | Remove-NetFirewallRule
New-NetFirewallRule -Name {_ps(REGEL_NAME)} -DisplayName {_ps(REGEL)} -Description {_ps(BESCHREIBUNG)} `
  -Direction Inbound -Action Allow -Program {p} -Protocol TCP -LocalPort {PORT} -Profile Private `
  -RemoteAddress LocalSubnet | Out-Null
"""


def anlegen() -> bool:
    """Regel mit Administratorrechten anlegen (Windows fragt einmal). False, wenn abgelehnt oder gescheitert."""
    if not WINDOWS:
        return True
    programm = lauschendes_programm()
    if programm is None:
        return False
    try:  # vorher: auch wenn das Skript nach dem Anlegen scheitert, findet der Deinstaller die Regel
        (pfade.basis() / "firewall.txt").write_text(REGEL_NAME, encoding="utf-8")
    except OSError:
        pass
    ok = _als_admin("powershell.exe", " ".join(_kodiert(anlegen_skript(programm))))
    return ok and regel_passt(regel_lesen(programm), programm)


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
