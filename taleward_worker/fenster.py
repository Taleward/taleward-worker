"""Programmfenster für die Taleward-App („Alles auf diesem PC“), abgedichtet.

Die App kommt aus dem Netz des eigenen Servers (http://localhost:8000/app/) und ist damit fremder Code aus Sicht des
Programms. Darum:
- keine Python-Brücke: Die Seite kann nichts im Programm aufrufen (anders als die eigene Oberfläche des Programms);
- Navigation nur innerhalb von http://localhost:8000 – alles andere öffnet im normalen Browser, Rahmen von anderswo
  werden blockiert;
- Mikrofon nur für diese Herkunft, alle anderen Berechtigungen abgelehnt;
- keine Entwicklerwerkzeuge, kein Kontextmenü (nur mit TALEWARD_DEBUG);
- neue Fenster (`window.open`, Links mit target) öffnet pywebview über `webbrowser.open` – das geht unter Windows an
  `os.startfile` und damit auch an `file:`, UNC-Pfade oder fremde Schemata. `absichern()` lässt darum vor dem Start
  des ersten Fensters nur noch http(s)-Adressen ohne Zugangsdaten durch (für beide Fenster des Programms).
Die Regeln für WebView2 greifen nur unter Windows; anderswo bleibt es bei den Vorgaben von pywebview.
"""
from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from urllib.parse import urlsplit

log = logging.getLogger("taleward_worker")
HOSTS = ("localhost", "127.0.0.1")
PORT = 8000
_ORIGINAL_OEFFNEN = webbrowser.open


def eigene_seite(uri: str) -> bool:
    """Gehört die Adresse zur App auf diesem PC? Nur http, localhost bzw. 127.0.0.1, Port 8000."""
    try:
        t = urlsplit(uri or "")
        return t.scheme == "http" and (t.hostname or "") in HOSTS and t.port == PORT and not t.username
    except ValueError:
        return False


def web_adresse(uri) -> bool:
    """Darf diese Adresse an den Browser von Windows gehen? Nur http(s) mit Host, ohne Zugangsdaten, ohne
    Steuerzeichen und Backslashes (Windows deutet sonst Pfade hinein)."""
    if not isinstance(uri, str) or len(uri) > 4000 or "\\" in uri or any(ord(z) < 33 or z == "\x7f" for z in uri):
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


def app_fenster(adresse: str, geschlossen=None):
    import webview

    w = webview.create_window("Taleward", adresse, js_api=KeineBruecke(), width=1180, height=820,
                              min_size=(420, 560), background_color="#F2E8D5", text_select=True)
    w.events.loaded += lambda: abdichten(w)
    if geschlossen:
        w.events.closed += geschlossen
    return w


def abdichten(w) -> None:
    """WebView2-Regeln setzen (einmal je Fenster). Läuft auf dem Faden der Oberfläche, wie WebView2 es verlangt."""
    if sys.platform != "win32" or getattr(w, "_taleward_dicht", False):
        return
    try:
        from System import Action  # type: ignore[import-not-found]

        form = w.native
        form.Invoke(Action(lambda: _abdichten(w, form)))
    except Exception:  # noqa: BLE001 – lieber protokollieren als das Fenster stehen lassen
        log.exception("Programmfenster: Regeln ließen sich nicht setzen – Fenster wird geschlossen")
        try:
            w.destroy()
        finally:
            extern_oeffnen(str(getattr(w, "real_url", "") or ""))


def _abdichten(w, form) -> None:
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

    core.NavigationStarting += navigation
    core.FrameNavigationStarting += rahmen
    core.PermissionRequested += berechtigung
    einstellungen = core.Settings
    einstellungen.AreDevToolsEnabled = False
    einstellungen.IsWebMessageEnabled = True  # pywebview braucht es für Fenstergröße und Ereignisse
    w._taleward_dicht = True
    # Was vor dem Setzen der Regeln geladen war, nochmals prüfen
    if not eigene_seite(str(core.Source)):
        core.Navigate(f"http://localhost:{PORT}/app/")
