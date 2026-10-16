"""Programmfenster für die Taleward-App („Alles auf diesem PC“), abgedichtet.

Die App kommt aus dem Netz des eigenen Servers (http://localhost:8000/app/) und ist damit fremder Code aus Sicht des
Programms. Darum:
- keine Python-Brücke: Die Seite kann nichts im Programm aufrufen (anders als die eigene Oberfläche des Programms);
- Navigation nur innerhalb von http://localhost:8000 – alles andere öffnet im normalen Browser, Rahmen von anderswo
  werden blockiert;
- Mikrofon nur für diese Herkunft, alle anderen Berechtigungen abgelehnt;
- keine Entwicklerwerkzeuge, kein Kontextmenü (nur mit TALEWARD_DEBUG, das der Nutzer selbst setzt);
- neue Fenster (`window.open`, Links mit target) öffnet pywebview über `webbrowser.open` – das geht unter Windows an
  `os.startfile` und damit auch an `file:`, UNC-Pfade oder fremde Schemata. `absichern()` lässt darum vor dem Start
  des ersten Fensters nur noch http(s)-Adressen ohne Zugangsdaten durch (für beide Fenster des Programms).
Die Regeln für WebView2 greifen nur unter Windows; anderswo bleibt es bei den Vorgaben von pywebview. Das Fenster
startet mit einer leeren eigenen Seite; die App wird erst geladen, wenn die Regeln gesetzt sind. Fremde Schemata
(`mailto:`, `ms-settings:` …) öffnet WebView2 nicht mehr, auch nicht nach Rückfrage.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("taleward_worker")
HOSTS = ("localhost", "127.0.0.1")
PORT = 8000
_ORIGINAL_OEFFNEN = webbrowser.open


def freier_anschluss() -> int:
    """Ein gerade freier Anschluss auf 127.0.0.1 – bei jedem Start ein anderer."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


ZWISCHENSPEICHER = ("Cache", "Code Cache", "GPUCache")


def zwischenspeicher_leeren(speicher: Path, fassung: str) -> bool:
    """Nach einem Fassungswechsel nur den Zwischenspeicher für Seiten im Fensterspeicher leeren. Anmeldung und
    Aufnahmen der App (Local Storage, IndexedDB) bleiben. Liefert True, wenn geleert wurde."""
    merker = speicher / "fassung.txt"
    try:
        if merker.read_text(encoding="utf-8").strip() == fassung:
            return False
    except OSError:
        pass
    for ordner in list(speicher.rglob("*")):
        if ordner.name in ZWISCHENSPEICHER and ordner.is_dir() and not ordner.is_symlink():
            shutil.rmtree(ordner, ignore_errors=True)
    try:
        merker.write_text(fassung, encoding="utf-8")
    except OSError:
        pass
    return True


def eigene_seite(uri: str) -> bool:
    """Gehört die Adresse zur App auf diesem PC? Nur http, localhost bzw. 127.0.0.1, Port 8000."""
    try:
        t = urlsplit(uri or "")
        return t.scheme == "http" and (t.hostname or "") in HOSTS and t.port == PORT and not t.username
    except ValueError:
        return False


def web_adresse(uri) -> bool:
    """Darf diese Adresse an den Browser von Windows gehen? Nur http(s) mit Host, ohne Zugangsdaten, ohne
    Steuerzeichen und Backslashes (Windows deutet sonst Pfade hinein), nur ASCII (Hostnamen in Punycode)."""
    if not isinstance(uri, str) or len(uri) > 4000 or "\\" in uri or any(not 33 <= ord(z) < 127 for z in uri):
        return False
    try:
        t = urlsplit(uri)
        return t.scheme in ("http", "https") and bool(t.hostname) and t.username is None and t.password is None
    except ValueError:
        return False


def nur_web_oeffnen(url, new=0, autoraise=True) -> bool:
    if not web_adresse(url):
        log.warning("Adresse nicht geöffnet (kein http/https): %.80r", url)
        return False
    return _ORIGINAL_OEFFNEN(url, new, autoraise)


def absichern() -> None:
    """Vor dem ersten Fenster aufrufen: Was immer pywebview oder das Programm an den Browser gibt, muss http(s) sein."""
    webbrowser.open = nur_web_oeffnen
    webbrowser.open_new = lambda url: nur_web_oeffnen(url, 1)
    webbrowser.open_new_tab = lambda url: nur_web_oeffnen(url, 2)


def extern_oeffnen(uri: str) -> None:
    """Links aus der App (Hilfe, Datenschutz anderer Anbieter …) im normalen Browser – nur http(s)."""
    if web_adresse(uri):
        threading.Thread(target=nur_web_oeffnen, args=(uri,), daemon=True).start()


class KeineBruecke:
    """Statt js_api=None: pywebview sucht aufgerufene Namen per getattr in diesem Objekt – hier gibt es nichts, auch
    keine eingebauten Attribute wie __class__."""

    def __getattribute__(self, _name):
        return None


# Seitenfarbe aus den Marken-Tokens: Pergament hell, dunkles Thema „--paper“ – sonst blitzt das Fenster im dunklen
# Windows beim Start hell auf
PAPIER_HELL, PAPIER_DUNKEL = "#F2E8D5", "#121D22"


def dunkel() -> bool:
    """Windows im dunklen Thema für Apps? (Andere Systeme und Fehler: hell.)"""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def papier() -> str:
    return PAPIER_DUNKEL if dunkel() else PAPIER_HELL


def leer() -> str:
    return ('<!doctype html><meta charset="utf-8"><title>Taleward</title>'
            f'<body style="background:{papier()}"></body>')


def app_fenster(adresse: str, geschlossen=None):
    """Erst eine leere eigene Seite – die App lädt `abdichten`, nachdem die Regeln gesetzt sind."""
    import webview

    if not eigene_seite(adresse):
        raise ValueError("nur die App auf diesem PC")
    w = webview.create_window("Taleward", html=leer(), js_api=KeineBruecke(), width=1180, height=820,
                              min_size=(420, 560), background_color=papier(), text_select=True)
    w.events.loaded += lambda: abdichten(w, adresse)
    if geschlossen:
        w.events.closed += geschlossen
    return w


def abdichten(w, adresse: str) -> None:
    """Beim ersten Laden (der leeren Seite): WebView2-Regeln setzen, dann die App laden. Läuft auf dem Faden der
    Oberfläche, wie WebView2 es verlangt. Später geladene Seiten ändern nichts mehr."""
    if getattr(w, "_taleward_dicht", False):
        return
    if sys.platform != "win32":
        w._taleward_dicht = True
        w.load_url(adresse)
        return
    try:
        from System import Action  # type: ignore[import-not-found]

        form = w.native
        form.Invoke(Action(lambda: _abdichten(w, form, adresse)))
    except Exception:  # noqa: BLE001 – lieber protokollieren als das Fenster stehen lassen
        log.exception("Programmfenster: Regeln ließen sich nicht setzen – Fenster wird geschlossen")
        try:
            w.destroy()
        finally:
            extern_oeffnen(adresse.replace("://localhost:", "://127.0.0.1:"))


def _abdichten(w, form, adresse: str) -> None:
    from Microsoft.Web.WebView2.Core import (  # type: ignore[import-not-found]
        CoreWebView2PermissionKind, CoreWebView2PermissionState)

    core = form.browser.webview.CoreWebView2

    def navigation(_sender, args):
        uri = str(args.Uri)
        if not eigene_seite(uri) and uri != "about:blank":
            args.Cancel = True
            extern_oeffnen(uri)

    def rahmen(_sender, args):
        if not eigene_seite(str(args.Uri)):
            args.Cancel = True

    def berechtigung(_sender, args):
        erlaubt = eigene_seite(str(args.Uri)) and args.PermissionKind == CoreWebView2PermissionKind.Microphone
        args.State = CoreWebView2PermissionState.Allow if erlaubt else CoreWebView2PermissionState.Deny

    def fremdes_schema(_sender, args):
        args.Cancel = True  # mailto:, ms-settings:, … – nie aus der App heraus starten
        log.info("Programmfenster: fremdes Schema nicht geöffnet (%.20s…)", str(args.Uri))

    core.NavigationStarting += navigation
    core.FrameNavigationStarting += rahmen
    core.PermissionRequested += berechtigung
    try:
        core.LaunchingExternalUriScheme += fremdes_schema
    except AttributeError:  # ältere WebView2: fragt dann selbst nach, bevor sie etwas startet
        log.info("Programmfenster: diese WebView2 kennt LaunchingExternalUriScheme nicht")
    einstellungen = core.Settings
    # Entwickleransicht nur, wenn der Nutzer Taleward selbst mit TALEWARD_DEBUG gestartet hat (Fehlersuche)
    einstellungen.AreDevToolsEnabled = bool(os.environ.get("TALEWARD_DEBUG"))
    einstellungen.IsWebMessageEnabled = True  # pywebview braucht es für Fenstergröße und Ereignisse
    w._taleward_dicht = True
    core.Navigate(adresse)  # erst jetzt die App
