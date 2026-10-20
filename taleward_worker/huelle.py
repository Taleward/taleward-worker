"""Die Oberfläche des Programms als feste Dateien unter `http://localhost:<Anschluss>` (die „Hülle“).

Warum nicht als Datei oder über pywebviews eigenen Server: Die Hülle zeigt die Taleward-App im Rahmen. Unter
`localhost` sind Hülle und App dieselbe Site, Chromium legt Anmeldung und Aufnahmen der App dann genau dort ab, wo sie
im bisherigen App-Fenster lagen. Die Herkunft (Anschluss) bleibt verschieden – die App kommt nicht an die Hülle heran.

- Nur GET und HEAD, nur Dateien aus `ui/` mit bekannten Endungen, keine Ordnerliste, keine Cookies, nichts, was etwas
  ändert. Alles, was die Oberfläche tut, geht über die Brücke (nur von dieser Herkunft, siehe fenster.py).
- Gebunden an 127.0.0.1 **und** [::1] auf demselben Anschluss, exklusiv: Sonst könnte ein anderes Programm die zweite
  Adresse belegen und sich als Hülle ausgeben – mit Brücke. Fehlt IPv6 auf dem PC ganz, kann auch niemand [::1] belegen.
- Host-Kopfzeile nur `localhost`, `127.0.0.1` oder `[::1]` mit diesem Anschluss (gegen Umleitung über fremde Namen).
"""
from __future__ import annotations

import errno
import logging
import socket
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

log = logging.getLogger("taleward_worker")

APP_HERKUNFT = "http://localhost:8000"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
       f"frame-src {APP_HERKUNFT}; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
ARTEN = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2",
         ".txt": "text/plain; charset=utf-8"}
VERSUCHE = 25
_NICHT_DA = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, getattr(errno, "WSAEADDRNOTAVAIL", 10049),
             getattr(errno, "WSAEAFNOSUPPORT", 10047), 10049, 10047}


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # SO_REUSEADDR hieße unter Windows: ein anderes Programm darf mitbinden
    daemon_threads = True

    def __init__(self, adresse, handler, familie):
        self.address_family = familie
        super().__init__(adresse, handler, bind_and_activate=False)

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        socketserver.TCPServer.server_bind(self)  # ohne HTTPServer.server_bind: kein Namensabruf (getfqdn)
        self.server_name, self.server_port = "localhost", self.server_address[1]


def _handler(ordner: Path, anschluss_fn):
    wurzel = ordner.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Taleward"
        sys_version = ""

        def log_message(self, *_a):  # nichts ins Protokoll (auch keine Cookies, die der Browser mitschickt)
            return

        def _senden(self, status: int, inhalt: bytes = b"", art: str = "text/plain; charset=utf-8", kopf=True):
            self.send_response(status)
            self.send_header("Content-Type", art)
            self.send_header("Content-Length", str(len(inhalt)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()
            if kopf and self.command != "HEAD":
                self.wfile.write(inhalt)

        def _host_passt(self) -> bool:
            anschluss = anschluss_fn()
            return (self.headers.get("Host") or "").lower() in (
                f"localhost:{anschluss}", f"127.0.0.1:{anschluss}", f"[::1]:{anschluss}")

        def _datei(self) -> Path | None:
            pfad = unquote(urlsplit(self.path).path)
            if pfad in ("", "/"):
                pfad = "/index.html"
            if "\\" in pfad or "\0" in pfad or "/." in pfad:
                return None
            ziel = (wurzel / pfad.lstrip("/")).resolve()
            if wurzel not in ziel.parents or ziel.suffix.lower() not in ARTEN or not ziel.is_file():
                return None
            return ziel

        def do_GET(self):  # noqa: N802
            if not self._host_passt():
                return self._senden(421)
            ziel = self._datei()
            if ziel is None:
                return self._senden(404)
            try:
                inhalt = ziel.read_bytes()
            except OSError:
                return self._senden(404)
            self._senden(200, inhalt, ARTEN[ziel.suffix.lower()])

        do_HEAD = do_GET

        def _verboten(self):
            self._senden(405)

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _verboten

    return Handler


class Huelle:
    def __init__(self, ordner: Path):
        self.ordner = ordner
        self.anschluss = 0
        self._server: list[_Server] = []

    @property
    def herkunft(self) -> str:
        return f"http://localhost:{self.anschluss}"

    def adresse(self, seite: str = "index.html") -> str:
        return f"{self.herkunft}/{seite}"

    def starten(self) -> int:
        handler = _handler(self.ordner, lambda: self.anschluss)
        for _ in range(VERSUCHE):
            v4 = _Server(("127.0.0.1", 0), handler, socket.AF_INET)
            try:
                v4.server_bind()
                v4.server_activate()
            except OSError:
                v4.server_close()
                continue
            anschluss = v4.server_address[1]
            v6 = None
            try:
                v6 = _Server(("::1", anschluss), handler, socket.AF_INET6)
                v6.server_bind()
                v6.server_activate()
            except OSError as e:
                if v6 is not None:
                    v6.server_close()
                if e.errno in _NICHT_DA or getattr(e, "winerror", None) in _NICHT_DA:
                    v6 = None  # kein IPv6 auf diesem PC – dann kann auch niemand anderes [::1] belegen
                else:
                    v4.server_close()  # [::1]:<Anschluss> gehört schon jemandem: nächster Anschluss
                    continue
            self.anschluss = anschluss
            self._server = [s for s in (v4, v6) if s is not None]
            for s in self._server:
                threading.Thread(target=s.serve_forever, daemon=True, name="huelle").start()
            return anschluss
        raise OSError("Kein freier Anschluss für die Oberfläche")

    def stoppen(self) -> None:
        for s in self._server:
            try:
                s.shutdown()
                s.server_close()
            except OSError:
                pass
        self._server = []
