"""Das eine Programmfenster (Taleward Desktop und Taleward Worker), abgedichtet.

Das Fenster zeigt die eigene Oberfläche des Programms (`ui/`, ausgeliefert von `huelle.py` unter
`http://localhost:<Anschluss>`) mit Python-Brücke. Bei „Alles auf diesem PC“ zeigt die Oberfläche die Taleward-App
(`http://localhost:8000/app/`) und die Verwaltung in Rahmen. Die App kommt aus dem Netz des eigenen Servers und ist
aus Sicht des Programms fremder Code. Eine Lücke in der App darf nie zu Programmen auf dem PC werden. Darum:
- Die Brücke nimmt nur Nachrichten der Oberfläche an (genaue Herkunft der Hülle) und nur Namen von Funktionen der `Api`;
  alles andere wird verworfen, mit einer Protokollzeile ohne Inhalt. Für Rahmen gibt es keine eigenen
  Nachrichten-Ereignisse und keine Host-Objekte.
- Die Hauptseite darf nur Seiten der Hülle laden, Rahmen nur `http://localhost:8000` (beide Schreibweisen). Alles andere
  öffnet im normalen Browser bzw. wird blockiert.
- Neue Fenster (`window.open`, Links mit target) landen nie im Programm: immer im Standardbrowser, nur http(s).
- Mikrofon nur für die App, alle anderen Berechtigungen abgelehnt.
- Keine Entwicklerwerkzeuge, kein Kontextmenü (nur mit TALEWARD_DEBUG, das der Nutzer selbst setzt).
- Fremde Schemata (`mailto:`, `ms-settings:` …) öffnet WebView2 nicht, auch nicht nach Rückfrage.
Die Regeln für WebView2 greifen nur unter Windows; anderswo bleibt es bei den Vorgaben von pywebview. Das Fenster
startet mit einer leeren eigenen Seite; die Oberfläche wird erst geladen, wenn die Regeln gesetzt sind.
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
    except (OSError, ImportError):
        return False


def papier() -> str:
    return PAPIER_DUNKEL if dunkel() else PAPIER_HELL


def leer() -> str:
    return ('<!doctype html><meta charset="utf-8"><title>Taleward</title>'
            f'<body style="background:{papier()}"></body>')




# ---------------------------------------------------------------------- Herkunft der Oberfläche
def von_huelle(uri, herkunft: str) -> bool:
    """Stammt die Adresse genau von der Oberfläche des Programms (`http://localhost:<Anschluss>`)? Nur dieser Name,
    nur dieser Anschluss, ohne Zugangsdaten."""
    try:
        h, t = urlsplit(herkunft or ""), urlsplit(str(uri or ""))
        return (bool(h.port) and t.scheme == "http" and (t.hostname or "") == "localhost" and t.port == h.port
                and h.scheme == "http" and h.hostname == "localhost" and t.username is None and t.password is None
                and "\\" not in str(uri))
    except ValueError:
        return False


def mikrofon_erlaubt(uri) -> bool:
    """Mikrofon nur für die App (Herkunft `http://localhost:8000`, Pfad unter /app, falls WebView2 einen nennt) – nie für
    die Oberfläche selbst oder die Verwaltung."""
    if not eigene_seite(str(uri or "")):
        return False
    pfad = urlsplit(str(uri)).path
    return pfad in ("", "/") or pfad == "/app" or pfad.startswith("/app/")


# Name des Rahmens mit der App in Taleward Desktop (`<iframe name=…>` in ui/desktop.html)
APP_RAHMEN = "taleward-app"


def mikrofon_im_app_rahmen(uri, herkunft: str) -> bool:
    """Mikrofon-Anfrage aus dem App-Rahmen: WebView2 nennt dabei je nach Fassung die Herkunft der App oder – weil der
    Rahmen das Recht über `allow="microphone"` von der Oberfläche geliehen bekommt – die Herkunft der Oberfläche."""
    return mikrofon_erlaubt(uri) or von_huelle(uri, herkunft)


INTERNE_NACHRICHTEN = frozenset()  # pywebview-eigene Namen (Alarm, Fenster ziehen, Zustand …) braucht die Oberfläche nicht


def nachricht_erlaubt(nachricht: str, quelle, herkunft: str, erlaubt) -> bool:
    """Prüfung einer Nachricht an die Brücke, bevor pywebview sie ausführt: Absender genau die Oberfläche, Inhalt
    `[name, parameter, kennung]` und `name` eine Funktion der `Api` (ohne Punkt, ohne Unterstrich – pywebview liefe
    sonst über Attribute bis ins Programm hinein)."""
    import json

    if not von_huelle(quelle, herkunft):
        return False
    try:
        daten = json.loads(nachricht)
    except (TypeError, ValueError):
        return False
    if not (isinstance(daten, list) and len(daten) == 3 and all(isinstance(x, str) for x in daten)):
        return False
    name = daten[0]
    return (name in erlaubt or name in INTERNE_NACHRICHTEN) and "." not in name and not name.startswith("_")


def api_namen(api_klasse) -> frozenset[str]:
    """Die Namen, die die Oberfläche aufrufen darf: öffentliche Methoden, die die Klasse selbst festlegt."""
    import inspect

    return frozenset(n for n, f in vars(api_klasse).items() if not n.startswith("_") and inspect.isfunction(f))


def bruecke_absichern(herkunft: str, erlaubt) -> bool:
    """Vor `webview.start` (bevor WebView2 entsteht): pywebviews Nachrichten-Empfang und neue Fenster ersetzen.
    pywebview hängt `on_script_notify` beim Anlegen des Fensters an `WebMessageReceived` – darum hier an der Klasse.
    Liefert False, wenn es diese Teile von pywebview nicht gibt (dann darf die Oberfläche nicht starten)."""
    if sys.platform != "win32":
        return True
    try:
        from webview.platforms import edgechromium  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 – kein WebView2 (dann startet ohnehin nichts)
        log.exception("Programmfenster: pywebview für WebView2 nicht ladbar")
        return False
    klasse = getattr(edgechromium, "EdgeChrome", None)
    original = getattr(klasse, "on_script_notify", None)
    if klasse is None or original is None or not hasattr(klasse, "on_new_window_request"):
        log.error("Programmfenster: pywebview hat einen unbekannten Aufbau – Oberfläche startet nicht")
        return False
    if getattr(original, "_taleward", False):
        return True

    def gefiltert(self, sender, args):
        try:
            quelle, nachricht = str(args.Source), str(args.get_WebMessageAsJson())
        except Exception:  # noqa: BLE001
            quelle, nachricht = "", ""
        if not nachricht_erlaubt(nachricht, quelle, herkunft, erlaubt):
            log.warning("Programmfenster: Nachricht aus fremder Herkunft oder unbekannter Aufruf verworfen")
            return None
        return original(self, sender, args)

    def neues_fenster(self, sender, args):
        args.set_Handled(True)  # nie im Programm – auch nicht, wenn die Einstellung von pywebview fehlt
        extern_oeffnen(str(args.get_Uri()))

    gefiltert._taleward = True
    klasse.on_script_notify = gefiltert
    klasse.on_new_window_request = neues_fenster
    return True


# ---------------------------------------------------------------------- das Fenster
def hauptfenster(titel: str, adresse: str, herkunft: str, api, **groesse):
    """Erst eine leere eigene Seite – die Oberfläche lädt `abdichten`, nachdem die Regeln gesetzt sind."""
    import webview

    if not von_huelle(adresse, herkunft):
        raise ValueError("nur die eigene Oberfläche")
    w = webview.create_window(titel, html=leer(), js_api=api, background_color=papier(), **groesse)
    w.events.loaded += lambda: abdichten(w, herkunft, adresse)
    return w


def abdichten(w, herkunft: str, adresse: str) -> None:
    """Beim ersten Laden (der leeren Seite): WebView2-Regeln setzen, dann die Oberfläche laden. Läuft auf dem Faden der
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
        form.Invoke(Action(lambda: _abdichten(w, form, herkunft, adresse)))
    except Exception:  # noqa: BLE001 – lieber eine Hinweisseite als eine Oberfläche ohne Regeln
        log.exception("Programmfenster: Regeln ließen sich nicht setzen – Oberfläche wird nicht geladen")
        w._taleward_dicht = "fehler"
        try:
            w.load_html(FEHLERSEITE.format(papier=papier(), schrift="#E9ECE6" if dunkel() else "#2A2118"))
        except Exception:  # noqa: BLE001
            pass


FEHLERSEITE = ('<!doctype html><meta charset="utf-8"><title>Taleward</title>'
               '<body style="background:{papier};font:16px/24px Segoe UI,sans-serif;padding:32px;color:{schrift}">'
               '<h1 style="font-size:22px">Taleward konnte sein Fenster nicht sicher öffnen</h1>'
               '<p>Beende Taleward über das Symbol im Infobereich und starte es neu. Klappt es dann immer noch nicht, '
               'installiere WebView2 neu (Microsoft-Download „WebView2 Runtime“).</p>'
               '<h1 style="font-size:22px">Taleward could not open its window safely</h1>'
               '<p>Quit Taleward from the icon in the notification area and start it again. If that does not help, '
               'reinstall the Microsoft WebView2 Runtime.</p></body>')


def _abdichten(w, form, herkunft: str, adresse: str) -> None:
    from Microsoft.Web.WebView2.Core import (  # type: ignore[import-not-found]
        CoreWebView2PermissionKind, CoreWebView2PermissionState)

    core = form.browser.webview.CoreWebView2

    def navigation(_sender, args):
        uri = str(args.Uri)
        if not von_huelle(uri, herkunft):
            args.Cancel = True
            extern_oeffnen(uri)

    def rahmen(_sender, args):
        if not eigene_seite(str(args.Uri)):
            args.Cancel = True

    def berechtigung(_sender, args):
        erlaubt = mikrofon_erlaubt(str(args.Uri)) and args.PermissionKind == CoreWebView2PermissionKind.Microphone
        args.State = CoreWebView2PermissionState.Allow if erlaubt else CoreWebView2PermissionState.Deny

    def rahmen_erzeugt(_sender, args):
        """Nur der App-Rahmen darf das Mikrofon nutzen. Anfragen aus Rahmen meldet WebView2 mit der Herkunft der
        Oberfläche – darum hier am Rahmen selbst entscheiden (der Name gilt so, wie er beim Anlegen war)."""
        frame = args.Frame
        if str(frame.Name or "") != APP_RAHMEN:
            return

        def app_berechtigung(_s, a):
            erlaubt = (a.PermissionKind == CoreWebView2PermissionKind.Microphone
                       and mikrofon_im_app_rahmen(str(a.Uri), herkunft))
            a.State = CoreWebView2PermissionState.Allow if erlaubt else CoreWebView2PermissionState.Deny
            a.Handled = True

        try:
            frame.PermissionRequested += app_berechtigung
        except AttributeError:  # sehr alte WebView2 – dann bleibt das Mikrofon im Rahmen gesperrt
            log.warning("Programmfenster: diese WebView2 kennt keine Berechtigungen je Rahmen")

    def fremdes_schema(_sender, args):
        args.Cancel = True  # mailto:, ms-settings:, … – nie aus der App heraus starten
        log.info("Programmfenster: fremdes Schema nicht geöffnet (%.20s…)", str(args.Uri))

    core.NavigationStarting += navigation
    core.FrameNavigationStarting += rahmen
    core.PermissionRequested += berechtigung
    try:
        core.FrameCreated += rahmen_erzeugt
    except AttributeError:
        log.warning("Programmfenster: diese WebView2 kennt FrameCreated nicht")
    try:
        core.LaunchingExternalUriScheme += fremdes_schema
    except AttributeError:  # ältere WebView2: fragt dann selbst nach, bevor sie etwas startet
        log.info("Programmfenster: diese WebView2 kennt LaunchingExternalUriScheme nicht")
    einstellungen = core.Settings
    # Entwickleransicht nur, wenn der Nutzer Taleward selbst mit TALEWARD_DEBUG gestartet hat (Fehlersuche)
    einstellungen.AreDevToolsEnabled = bool(os.environ.get("TALEWARD_DEBUG"))
    einstellungen.AreDefaultContextMenusEnabled = bool(os.environ.get("TALEWARD_DEBUG"))
    einstellungen.IsWebMessageEnabled = True  # die Brücke (nur von der Oberfläche, siehe bruecke_absichern)
    w._taleward_dicht = True
    core.Navigate(adresse)  # erst jetzt die Oberfläche


def mindestgroesse(w, breite: int, hoehe: int) -> None:
    """Mindestgröße nach dem Wechsel der Betriebsart (pywebview kann sie nur beim Anlegen setzen)."""
    if sys.platform != "win32":
        return
    try:
        from System import Action  # type: ignore[import-not-found]
        from System.Drawing import Size  # type: ignore[import-not-found]

        form = w.native
        form.Invoke(Action(lambda: setattr(form, "MinimumSize", Size(breite, hoehe))))
    except Exception:  # noqa: BLE001 – Beiwerk
        log.debug("Mindestgröße nicht gesetzt", exc_info=True)
