"""KI-Paket („Motor“) installieren und aktualisieren.

Der Motor ist der Taleward-Server-Code in genau der Fassung des verbundenen Servers, mit den KI-Bibliotheken
(WhisperX, PyTorch mit CUDA). Die App installiert ihn mit uv in eine eigene Python-Umgebung im Benutzerordner:

1. Python 3.11 (von uv verwaltet, unabhängig von einem installierten Python)
2. Pakete in genau den Versionen aus uv.lock der passenden Fassung – mit Prüfsummen: uv lädt nur Dateien, deren
   Prüfsumme in uv.lock dieses (freigegebenen) Commits steht (--require-hashes). torch, torchaudio und torchvision
   kommen in der Variante für die Grafikkarte vom PyTorch-Verzeichnis (uv --torch-backend); ihre Prüfsummen stehen
   in `pytorch-pruefsummen.txt` am Release, die Freigabe nennt deren SHA-256. Ältere Fassungen ohne diese Liste:
   PyTorch nur auf die Version festgelegt (steht dann im Protokoll).
3. der Taleward-Code selbst (ohne Abhängigkeiten, die kamen aus Schritt 2) und ffmpeg (imageio-ffmpeg). Gebaut wird
   er mit dem Bauwerkzeug aus BAUWERKZEUG (feste Prüfsummen, vorher in die Umgebung installiert) – ohne Zugriff auf
   ein Paketverzeichnis (--no-build-isolation --no-index).

Im Testmodus reicht der Taleward-Code mit den Grundpaketen (ein paar MB statt einiger GB), ebenfalls mit Prüfsummen.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from taleward_worker import REPO, VERSION, freigabe, pfade, prozesse

# Ungefähre Downloadgröße (für die Fortschrittsanzeige; gemessen an der Größe des Zwischenspeichers)
# Größe des installierten KI-Pakets in MB (gemessen: Windows mit CUDA 7,7 GB, Stand 0.4.4). Der Fortschritt zählt,
# wie weit der Zwischenspeicher von uv gewachsen ist – dort liegen die Pakete entpackt, also in dieser Größe.
# CUDA-Fassung von PyTorch. Fest statt „auto“: uv würde nach der Treiber-Fassung wählen und bei Treibern vor 560 auf
# einem Index landen, auf dem die festgeschriebene torch-Fassung gar nicht liegt (Installation scheitert oder wird
# stillschweigend Prozessor). cu128 läuft dank CUDA-Minor-Kompatibilität mit jedem Treiber ab 528; genau diese
# Fassung prüft auch der Bau (packaging/pruefe-ki-paket.sh).
TORCH_BACKEND = "cu128"

ERWARTET_MB = {"windows": 7900, "linux": 8500, "cpu": 1800, "test": 60}


class MotorFehler(Exception):
    def __init__(self, code: str, text: str = ""):
        super().__init__(text or code)
        self.code = code
        self.text = text


def _ohne_fenster() -> dict:
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def uv_programm() -> str:
    """uv liegt dem Windows-Paket bei (ressourcen/uv.exe); unter Linux installiert es das Installationsskript."""
    name = "uv.exe" if sys.platform == "win32" else "uv"
    mitgeliefert = pfade.ressourcen() / "bin" / name
    if mitgeliefert.exists():
        return str(mitgeliefert)
    gefunden = shutil.which("uv") or shutil.which(str(Path.home() / ".local" / "bin" / "uv"))
    if gefunden:
        return gefunden
    raise MotorFehler("uv_fehlt")


def _netzcode(e: httpx.HTTPError) -> str:
    from taleward_worker.server import zertifikatsfehler

    return "zertifikat" if zertifikatsfehler(e) else "github_nicht_erreichbar"


def quelle(fassung: str) -> dict:
    """Download-Adressen für eine Serverfassung – nur, wenn sie freigegeben (unterschrieben) ist.

    Die Fassung nennt der Server; geladen wird genau der Commit aus der Freigabe des Tags v<Fassung>
    (taleward_worker/freigabe.py). Ohne Freigabe kein Rückfall auf main, sondern ein Fehler."""
    eigen = os.environ.get("TALEWARD_MOTOR_QUELLE")  # Entwicklung: lokaler Ordner des Server-Codes
    if eigen:
        return {"ref": "lokal", "genau": True, "paket": eigen,
                "anforderungen": str(Path(eigen) / "engine-requirements.txt"),
                "projekt": str(Path(eigen) / "pyproject.toml"), "lock": str(Path(eigen) / "uv.lock")}
    if not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", fassung or ""):
        raise MotorFehler("nicht_freigegeben", f"Unbekannte Serverfassung: {fassung!r}"[:200])
    tag = f"v{fassung}"
    try:
        f = freigabe.von_github(REPO, tag, user_agent=f"TalewardWorker/{VERSION}")
    except httpx.HTTPError as e:
        raise MotorFehler(_netzcode(e)) from None
    except freigabe.FreigabeFehler as e:
        raise MotorFehler("nicht_freigegeben", str(e)) from None
    return _adressen(tag, f.commit, f.dateien)


def neueste_fassung(klient: httpx.Client | None = None, mindestens: str | None = None) -> str:
    """„Alles auf diesem PC“: Es gibt keinen Server, der die Fassung nennt – also die neueste freigegebene
    Server-Fassung bei GitHub. Zählt nur, wofür eine gültige Freigabe vorliegt (ohne Freigabe weiter suchen, nie ein
    Rückfall unter `mindestens`, die laufende Fassung)."""
    from taleward_worker.selbstupdate import neuer

    eigen = os.environ.get("TALEWARD_MOTOR_FASSUNG")  # Entwicklung
    if eigen:
        return eigen
    eigener_klient = klient is None
    klient = klient or httpx.Client(timeout=20, follow_redirects=True,
                                    headers={"User-Agent": f"TalewardWorker/{VERSION}",
                                             "Accept": "application/vnd.github+json"})
    try:
        r = klient.get(f"https://api.github.com/repos/{REPO}/releases", params={"per_page": 30})
        r.raise_for_status()
        eintraege = r.json()
    except httpx.HTTPError as e:
        raise MotorFehler(_netzcode(e)) from None
    except ValueError:
        raise MotorFehler("github_nicht_erreichbar") from None
    finally:
        if eigener_klient:
            klient.close()
    fassungen = []
    for e in eintraege if isinstance(eintraege, list) else []:
        tag = str(e.get("tag_name") or "") if isinstance(e, dict) else ""
        m = re.fullmatch(r"v(\d{1,4}\.\d{1,4}\.\d{1,4})", tag)
        if m and not e.get("draft") and not e.get("prerelease"):
            fassungen.append(m.group(1))
    fassungen.sort(key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
    for fassung in fassungen[:6]:
        if mindestens and neuer(mindestens, fassung):
            break  # nie zurück unter die laufende Fassung
        try:
            freigabe.von_github(REPO, f"v{fassung}", user_agent=f"TalewardWorker/{VERSION}")
        except freigabe.FreigabeFehler:
            continue  # noch nicht freigegeben – die nächstältere prüfen
        except httpx.HTTPError as e:
            raise MotorFehler(_netzcode(e)) from None
        return fassung
    if mindestens:
        return mindestens
    raise MotorFehler("nicht_freigegeben", "Keine freigegebene Fassung gefunden.")


def _adressen(tag: str, commit: str, dateien: dict[str, str] | None = None) -> dict:
    roh = f"https://raw.githubusercontent.com/{REPO}/{commit}"
    return {"ref": tag, "genau": True, "commit": commit, "anhaenge": dict(dateien or {}),
            "release": f"https://github.com/{REPO}/releases/download/{tag}",
            "paket": f"https://github.com/{REPO}/archive/{commit}.zip",
            "anforderungen": f"{roh}/engine-requirements.txt",
            "projekt": f"{roh}/pyproject.toml", "lock": f"{roh}/uv.lock"}


@dataclass
class Installation:
    """Läuft in einem eigenen Faden; die Oberfläche fragt den Stand ab."""
    fassung: str
    testmodus: bool = False
    backend: str = "cuda"         # "cuda" (NVIDIA) oder "cpu" (nur Prozessor, deutlich kleiner)
    neueste: bool = False         # Alles auf diesem PC: Fassung = neueste freigegebene bei GitHub
    phase: str = "start"          # start, python, pakete, taleward, ffmpeg, fertig, fehler, abgebrochen
    anteil: float = 0.0
    geladen_mb: int = 0
    erwartet_mb: int = 0
    fehler: str = ""
    fehlertext: str = ""
    zeilen: list[str] = field(default_factory=list)
    vor_tausch: object = None     # Aufruf vor dem Ersetzen des alten Motors (laufenden Worker anhalten)
    nach_tausch: object = None
    alt_behalten: bool = False    # Update „Alles auf diesem PC“: alten Motor bis zur Prüfung als motor-alt behalten
    weg: str = ""                 # gross (neuer Ordner), gleich/klein (im vorhandenen Motor) – für den Rückweg
    pytorch_geprueft: bool = False  # PyTorch mit Prüfsummen aus der Liste am Release
    _prozess: subprocess.Popen | None = None
    _abbrechen: threading.Event = field(default_factory=threading.Event)

    def stand(self) -> dict:
        return {"phase": self.phase, "anteil": round(self.anteil, 3), "geladenMb": self.geladen_mb,
                "erwartetMb": self.erwartet_mb, "fehler": self.fehler, "fehlertext": self.fehlertext,
                "fassung": self.fassung, "letzteZeile": self.zeilen[-1] if self.zeilen else ""}

    def abbrechen(self) -> None:
        self._abbrechen.set()
        prozesse.beenden(self._prozess, sanft=False)

    # ------------------------------------------------------------------ Ablauf
    def starten(self) -> threading.Thread:
        t = threading.Thread(target=self._lauf, daemon=True, name="motor-installation")
        t.start()
        return t

    def _lauf(self) -> None:
        try:
            self._installieren()
            self.phase, self.anteil = "fertig", 1.0
        except MotorFehler as e:
            self.phase = "abgebrochen" if self._abbrechen.is_set() else "fehler"
            self.fehler, self.fehlertext = e.code, e.text
        except Exception as e:  # unerwartet – Text für das Protokoll
            self.phase, self.fehler, self.fehlertext = "fehler", "unerwartet", f"{type(e).__name__}: {e}"
        finally:
            shutil.rmtree(pfade.basis() / "cache", ignore_errors=True)  # spart einige GB

    def _umgebung(self) -> dict:
        env = saubere_umgebung()
        env.update({"UV_NO_CONFIG": "1", "UV_NATIVE_TLS": "1",  # Zertifikate aus dem Windows-Speicher (Virenscanner)
                    "UV_CACHE_DIR": str(pfade.basis() / "cache"),
                    "UV_PYTHON_INSTALL_DIR": str(pfade.basis() / "python"),
                    "UV_PYTHON_PREFERENCE": "only-managed",
                    "UV_NO_PROGRESS": "1", "NO_COLOR": "1", "UV_LINK_MODE": "hardlink"})  # kein zweites Exemplar: spart während der Installation ~8 GB
        if getattr(self, "_ohne_verknuepfung", False):
            # Python liegt dann „außerhalb“ von uv: uv soll den Python-Ordner mit der blockierten Verknüpfung
            # nicht mehr ansehen (sonst os error 448 bei jeder Abfrage des Interpreters)
            env["UV_PYTHON_INSTALL_DIR"] = str(pfade.basis() / "python-ohne-uv")
            env["UV_PYTHON_PREFERENCE"] = "managed"
        return env

    def _uv(self, *argumente: str, anteil_von: float, anteil_bis: float, erwartet_mb: int = 0,
            messordner: Path | None = None) -> None:
        if self._abbrechen.is_set():
            raise MotorFehler("abgebrochen")
        befehl = [uv_programm(), *argumente]
        self.zeilen.append("$ uv " + " ".join(argumente))
        cache = messordner or pfade.basis() / "cache"   # dessen Wachstum zeigt den Fortschritt
        start_mb = pfade.ordnergroesse(cache) // 2 ** 20 if cache.exists() else 0
        self._prozess = subprocess.Popen(befehl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", env=self._umgebung(),
                                         cwd=pfade.basis(), **_ohne_fenster())
        prozesse.zuordnen(self._prozess)
        lesen = threading.Thread(target=self._mitlesen, args=(self._prozess,), daemon=True)
        lesen.start()
        while self._prozess.poll() is None:
            if erwartet_mb:
                self.geladen_mb = max(0, pfade.ordnergroesse(cache) // 2 ** 20 - start_mb) if cache.exists() else 0
                self.anteil = anteil_von + (anteil_bis - anteil_von) * min(0.97, self.geladen_mb / erwartet_mb)
            time.sleep(1)
        lesen.join(2)
        if self._prozess.returncode != 0:
            if self._abbrechen.is_set():
                raise MotorFehler("abgebrochen")
            letzte = "\n".join(self.zeilen[-12:])
            raise MotorFehler(fehlercode(letzte), letzte)
        self.anteil = anteil_bis

    def _mitlesen(self, p: subprocess.Popen) -> None:
        for zeile in p.stdout:
            zeile = zeile.rstrip()
            if zeile:
                self.zeilen.append(zeile)
                del self.zeilen[:-400]

    def _installieren(self) -> None:
        if not self.fassung and self.neueste:
            from taleward_worker.einzelplatz import MINDEST_SERVER
            from taleward_worker.selbstupdate import neuer

            self.phase = "suchen"
            fassung = neueste_fassung()
            if neuer(MINDEST_SERVER, fassung):
                raise MotorFehler("einzelplatz_zu_alt", f"Neueste freigegebene Fassung: {fassung}")
            self.fassung = fassung
        q = quelle(self.fassung)
        self.zeilen.append(f"Fassung {self.fassung} → {q['ref']}" + (f" ({q['commit'][:12]}, freigegeben)"
                                                                    if q.get("commit") else ""))
        if not self.testmodus:
            art, pakete = self._aenderung(q)
            if art == "gleich":
                self.weg = "gleich"
                return self._nur_taleward(q)
            if art == "klein":  # nur kleine Bibliotheken neu – in den vorhandenen Motor, statt 8 GB neu zu laden
                self.weg = "klein"
                return self._nur_taleward(q, zusatz=pakete)
        self.weg = "gross"
        system = "test" if self.testmodus else "cpu" if self.backend == "cpu" else (
            "windows" if sys.platform == "win32" else "linux")
        self.erwartet_mb = ERWARTET_MB[system]
        neu = pfade.basis() / "motor-neu"
        shutil.rmtree(neu, ignore_errors=True)

        self.phase = "python"
        basis_python = self._python_einrichten()
        self._uv("venv", str(neu), "--python", str(basis_python), anteil_von=0.05, anteil_bis=0.06)
        home_festlegen(neu, basis_python)
        py = str(neu / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))

        self.phase = "pakete"
        geprueft, pytorch = self._listen(q)
        # Alle Versionen stehen fest (aus uv.lock) → ohne erneutes Auflösen installieren, nur Dateien mit
        # bekannter Prüfsumme
        self._uv("pip", "install", "--python", py, "--no-deps", "--require-hashes", "-r", str(geprueft),
                 anteil_von=0.06, anteil_bis=0.5 if pytorch else 0.9,
                 erwartet_mb=self.erwartet_mb // 2 if pytorch else self.erwartet_mb)
        if pytorch:
            torch = "cpu" if self.backend == "cpu" else TORCH_BACKEND
            self._uv("pip", "install", "--python", py, "--no-deps", "--torch-backend", torch,
                     *(["--require-hashes"] if self.pytorch_geprueft else []), "-r", str(pytorch),
                     anteil_von=0.5, anteil_bis=0.9, erwartet_mb=self.erwartet_mb // 2)
        self.phase = "taleward"
        self._bauwerkzeug(py)
        self._taleward_einspielen(py, q, anteil_von=0.9, anteil_bis=0.95)

        self.phase = "ffmpeg"
        pruefung = _python_pruefen(py, "import app, sys; print('ok')")
        if "ok" not in pruefung.stdout:
            raise MotorFehler("installation", pruefung.stderr[-2000:])
        cuda = self.backend != "cpu" and not self.testmodus
        ist = torch_pruefen(py) if cuda else {}
        if cuda and ist.get("cuda") is None:  # keine CUDA-Fassung gelandet – nicht als Grafikkarten-Motor eintragen
            raise MotorFehler("installation", ist.get("fehler") or "PyTorch wurde ohne CUDA installiert.")

        # ffmpeg noch aus dem neuen Ordner heraus einrichten – scheitert das, ist der alte Motor unberührt
        ffmpeg_einrichten(Path(py))
        kennzeichnung = {"fassung": self.fassung, "ref": q["ref"], "testmodus": self.testmodus,
                         "backend": self.backend, "torch": ist, "installiert": time.strftime("%Y-%m-%d %H:%M")}
        (neu / "taleward-motor.json").write_text(json.dumps(kennzeichnung), encoding="utf-8")

        # Erst jetzt den alten Motor ersetzen – bei einem Fehler bleibt der bisherige nutzbar
        alt = pfade.motor()
        weg = pfade.basis() / "motor-alt"
        ordner_entfernen(weg)
        if callable(self.vor_tausch):
            self.vor_tausch()
        self.phase = "tauschen"
        try:
            if alt.exists():
                umbenennen(alt, weg)
            umbenennen(neu, alt)
        except BaseException:
            self.phase = "fehler"  # damit nach_tausch die alte Fassung stehen lässt
            raise
        finally:
            if callable(self.nach_tausch):
                self.nach_tausch()
        if not self.alt_behalten:
            ordner_entfernen(weg)
        # Hinweis: Skripte im Motor (chronik.exe) kennen noch den alten Ordnernamen – gestartet wird daher immer
        # mit „python -m app.cli“, das funktioniert nach dem Umbenennen weiter.


    def _listen(self, q: dict, fassung: str | None = None, mit_pytorch: bool = True) -> tuple[Path, Path | None]:
        """Paketlisten aus uv.lock der Fassung: (mit Prüfsummen, PyTorch-Familie – mit Prüfsummen aus der Liste am
        Release). Fassungen ab PYTORCH_LISTE_PFLICHT_AB ohne Liste werden nicht installiert; ältere nur auf die Version
        festgelegt (steht im Protokoll). Im Testmodus nur die Grundpakete und ffmpeg, ohne PyTorch."""
        ordner = pfade.basis() / "cache"
        ordner.mkdir(parents=True, exist_ok=True)
        basis, ki = pruefsummen(q, umgebung=self._umgebung(), ordner=ordner / "lock")
        if self.testmodus:
            geprueft, pytorch = pruefsummen_aufteilen(basis)[0], ""
            ffmpeg = [b for n, b in eintraege(ki) if n == "imageio-ffmpeg"]
            if not any(n == "imageio-ffmpeg" for n, _ in eintraege(geprueft)):
                geprueft += (ffmpeg[0] + "\n") if ffmpeg else IMAGEIO_FFMPEG
        else:
            geprueft, pytorch = pruefsummen_aufteilen(ki, mit_cuda=self.backend != "cpu")
            if not any(n == "imageio-ffmpeg" for n, _ in eintraege(geprueft)):
                geprueft += IMAGEIO_FFMPEG
        (ordner / "geprueft.txt").write_text(geprueft, encoding="utf-8")
        if not pytorch.strip() or not mit_pytorch:
            return ordner / "geprueft.txt", None
        liste = pytorch_pruefsummen(q)
        if liste is None and liste_pflicht(fassung or self.fassung, q):
            raise MotorFehler("installation", f"Für die Fassung {fassung or self.fassung} fehlt die unterschriebene "
                                              "Liste der PyTorch-Prüfsummen – sie wird nicht installiert.")
        if liste is None:
            self.pytorch_geprueft = False
            self.zeilen.append("PyTorch ohne eigene Prüfsummen (diese Fassung hat noch keine Liste) – nur die Version "
                               "ist festgelegt.")
        else:
            pytorch = pytorch_mit_pruefsummen(pytorch, liste, "cpu" if self.backend == "cpu" else TORCH_BACKEND)
            self.pytorch_geprueft = True
        (ordner / "pytorch.txt").write_text(pytorch, encoding="utf-8")
        return ordner / "geprueft.txt", ordner / "pytorch.txt"

    # ------------------------------------------------------------------ Taleward-Code
    def _bauwerkzeug(self, py: str) -> None:
        """Bauwerkzeug mit festen Prüfsummen in die Umgebung – nur, was dort noch fehlt (vorhandene Pakete kommen aus
        uv.lock und bleiben in deren Fassung)."""
        vorhanden = installierte_pakete(py, self._umgebung())
        fehlt = [block for name, block in eintraege(BAUWERKZEUG) if name not in vorhanden]
        if not fehlt:
            return
        datei = pfade.basis() / "cache" / "bauwerkzeug.txt"
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text("\n".join(fehlt) + "\n", encoding="utf-8")
        self._uv("pip", "install", "--python", py, "--no-deps", "--require-hashes", "-r", str(datei),
                 anteil_von=self.anteil, anteil_bis=self.anteil)

    def _taleward_einspielen(self, py: str, q: dict, anteil_von: float, anteil_bis: float,
                             neu: bool = False) -> None:
        """Den Taleward-Code (Commit der Freigabe) bauen und installieren: ohne Abhängigkeiten, ohne Bau-Umgebung
        und ohne Paketverzeichnis – es läuft nur das Bauwerkzeug mit bekannter Prüfsumme."""
        self._uv("pip", "install", "--python", py, "--no-deps", "--no-build-isolation", "--no-index",
                 *(["--reinstall"] if neu else []), q["paket"], anteil_von=anteil_von, anteil_bis=anteil_bis)

    # ------------------------------------------------------------------ Schneller Weg bei neuer Serverfassung
    def _nur_taleward_noetig(self, q: dict) -> bool:
        return self._aenderung(q)[0] == "gleich"

    def _aenderung(self, q: dict) -> tuple[str, list[str]]:
        """Was hat sich an den KI-Bibliotheken gegenüber dem installierten Motor geändert? Probelauf von uv:
        „gleich“ (nur Taleward-Code tauschen, Sekunden), „klein“ (ein paar kleine Pakete – in den vorhandenen Motor)
        oder „gross“ (torch/CUDA anders – vollständige Installation, 8 GB)."""
        inst = installiert()
        if not inst or inst.get("testmodus") or inst.get("backend", "cuda") != self.backend:
            return "gross", []
        try:
            basis, extra = anforderungen_aufteilen(anforderungen_lesen(q["anforderungen"]))
            ordner = pfade.basis() / "cache"
            ordner.mkdir(parents=True, exist_ok=True)
            (ordner / "pruefen.txt").write_text(basis + "\n" + extra, encoding="utf-8")
            torch = "cpu" if self.backend == "cpu" else TORCH_BACKEND
            res = subprocess.run([uv_programm(), "pip", "install", "--dry-run", "--python", str(pfade.motor_python()),
                                  "--no-deps", "--torch-backend", torch, "-r", str(ordner / "pruefen.txt")],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                                 env=self._umgebung(), cwd=pfade.basis(), **_ohne_fenster())
        except (MotorFehler, OSError, subprocess.SubprocessError):
            return "gross", []
        ausgabe = res.stdout + res.stderr
        if res.returncode != 0:
            self.zeilen.append("Probelauf gescheitert – vollständige Installation.")
            return "gross", []
        if "Would make no changes" in ausgabe:
            self.zeilen.append("KI-Bibliotheken unverändert – nur der Taleward-Code wird getauscht.")
            return "gleich", []
        pakete = geaenderte_pakete(ausgabe)
        if pakete and not any(schwer(p) for p in pakete):
            self.zeilen.append("Kleine Änderung an den KI-Bibliotheken (" + ", ".join(pakete) +
                               ") – wird in das vorhandene KI-Paket eingespielt.")
            return "klein", pakete
        self.zeilen.append("KI-Bibliotheken haben sich geändert – vollständige Installation.")
        return "gross", pakete

    def _nur_taleward(self, q: dict, zusatz: list[str] | None = None) -> None:
        self.phase = "taleward"
        py = str(pfade.motor_python())
        # vorher: braucht das Netz, der Worker läuft noch. PyTorch tauscht ein kleines Update nie.
        geprueft = self._listen(q, mit_pytorch=False)[0] if zusatz else None
        self._bauwerkzeug(py)  # stört den laufenden Worker nicht (wird nur beim Bauen gebraucht)
        if callable(self.vor_tausch):
            self.vor_tausch()  # laufenden Worker anhalten (nur für wenige Sekunden)
        try:
            if geprueft:
                self.phase = "pakete"  # nur kleine Pakete (nie PyTorch, siehe schwer) – mit Prüfsummen
                self._uv("pip", "install", "--python", py, "--no-deps", "--require-hashes", "-r", str(geprueft),
                         anteil_von=0.1, anteil_bis=0.6)
                self.phase = "taleward"
            self._taleward_einspielen(py, q, anteil_von=0.6 if zusatz else 0.1, anteil_bis=0.9, neu=True)
            pruefung = _python_pruefen(py, "import app, sys; print('ok')")
            if "ok" not in pruefung.stdout:
                raise MotorFehler("installation", pruefung.stderr[-2000:])
            daten = installiert() or {}
            daten.update({"fassung": self.fassung, "ref": q["ref"], "installiert": time.strftime("%Y-%m-%d %H:%M")})
            (pfade.motor() / "taleward-motor.json").write_text(json.dumps(daten), encoding="utf-8")
        except BaseException:
            self.phase = "fehler"  # damit nach_tausch die alte Fassung stehen lässt
            raise
        finally:
            if callable(self.nach_tausch):
                self.nach_tausch()

    # ------------------------------------------------------------------ Rückweg (Update „Alles auf diesem PC“)
    def stand_festhalten(self, ziel: Path) -> None:
        """Vor dem Update: genaue Paketliste des laufenden Motors (mit Herkunft des Taleward-Codes) und seine
        Kennzeichnung sichern – daraus stellt `zurueck` den alten Stand wieder her."""
        ziel.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([uv_programm(), "pip", "freeze", "--python", str(pfade.motor_python())],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           env=self._umgebung(), cwd=pfade.basis(), **_ohne_fenster())
        if r.returncode != 0 or not r.stdout.strip():
            raise MotorFehler("installation", "Paketliste nicht lesbar: " + (r.stderr or r.stdout)[-500:])
        ziel.write_text(r.stdout, encoding="utf-8")
        kennung = pfade.motor() / "taleward-motor.json"
        if kennung.exists():
            shutil.copyfile(kennung, ziel.with_suffix(".json"))

    def zurueck(self, vorher: Path, alt_fassung: str | None = None) -> None:
        """Nach einem gescheiterten Update: den alten Motor wiederherstellen. Großes Update → der behaltene Ordner
        motor-alt kommt zurück (ohne Download). Sonst → die alte Fassung wie bei einer Installation: ihre Pakete mit den
        Prüfsummen aus ihrer uv.lock, das Bauwerkzeug mit festen Prüfsummen und ihr Taleward-Code ohne
        Paketverzeichnis. PyTorch bleibt, wie es ist – ein kleines Update tauscht es nie."""
        alt = pfade.basis() / "motor-alt"
        if self.weg == "gross" and (alt / "taleward-motor.json").exists():
            kaputt = pfade.basis() / "motor-kaputt"
            ordner_entfernen(kaputt)
            if pfade.motor().exists():
                umbenennen(pfade.motor(), kaputt)
            umbenennen(alt, pfade.motor())
            ordner_entfernen(kaputt)
            return
        kennung = vorher.with_suffix(".json")
        if not alt_fassung and kennung.exists():
            try:
                alt_fassung = str(json.loads(kennung.read_text(encoding="utf-8")).get("fassung") or "")
            except (OSError, ValueError):
                alt_fassung = ""
        if not alt_fassung:
            raise MotorFehler("installation", "Fassung von vorher unbekannt.")
        q = quelle(alt_fassung)
        py = str(pfade.motor_python())
        self._abbrechen.clear()
        geprueft, _ = self._listen(q, alt_fassung, mit_pytorch=False)
        self._uv("pip", "install", "--python", py, "--no-deps", "--require-hashes", "-r", str(geprueft),
                 anteil_von=0.0, anteil_bis=0.6)
        self._bauwerkzeug(py)
        self._taleward_einspielen(py, q, anteil_von=0.6, anteil_bis=1.0, neu=True)
        if kennung.exists():
            shutil.copyfile(kennung, pfade.motor() / "taleward-motor.json")

    def alt_entfernen(self) -> None:
        ordner_entfernen(pfade.basis() / "motor-alt")

    def _python_einrichten(self) -> Path:
        """Python 3.11 über uv holen und den Pfad zur python(.exe) liefern.

        uv legt zusätzlich eine Verknüpfung „cpython-3.11-…“ an (unter Windows eine Junction). Manche Windows-PCs
        verbieten das (os error 448, „nicht vertrauenswürdiger Bereitstellungspunkt“, z. B. mit OneDrive „Dateien
        bei Bedarf“). Python selbst ist dann trotzdem vollständig da – wir nehmen es direkt, ohne die Verknüpfung.
        """
        ordner = pfade.basis() / "python"
        vorhanden = python_finden(ordner)
        if vorhanden is not None and python_laeuft(vorhanden):
            # Nicht neu installieren: uv würde dabei die Verknüpfung „cpython-3.11-…“ anfassen, an der ein älteres,
            # noch laufendes KI-Paket hängen kann
            self.zeilen.append(f"Python 3.11 vorhanden: {vorhanden.parent.name}")
            self._ohne_verknuepfung = True  # uv soll den Python-Ordner (samt Verknüpfung) gar nicht erst durchsuchen
            return vorhanden
        try:
            self._uv("python", "install", "3.11", "--no-bin", "--no-registry", anteil_von=0.0, anteil_bis=0.05, erwartet_mb=110,
                     messordner=ordner)
        except MotorFehler as e:
            if e.code == "abgebrochen" or not verknuepfung_verboten(e.text):
                raise
            self.zeilen.append("Hinweis: Windows erlaubt die Python-Verknüpfung nicht – nutze Python direkt.")
            verknuepfungen_entfernen(ordner)
            self._ohne_verknuepfung = True
        gefunden = python_finden(ordner)
        if gefunden is None:
            raise MotorFehler("installation", "\n".join(self.zeilen[-12:]) + "\nPython 3.11 nicht gefunden.")
        return gefunden


def verknuepfung_verboten(text: str) -> bool:
    return "os error 448" in text or "minor version link" in text


def verknuepfungen_entfernen(ordner: Path) -> None:
    """Die (blockierte) Verknüpfung „cpython-3.11-…“ entfernen – nur die Verknüpfung, nie das Ziel."""
    for d in ordner.glob("cpython-3.11-*"):
        try:
            if d.is_symlink():
                d.unlink()
            elif getattr(d, "is_junction", lambda: False)() or (sys.platform == "win32" and _ist_umleitung(d)):
                os.rmdir(d)   # entfernt bei einer Junction nur die Verknüpfung selbst
        except OSError:
            pass


def _ist_umleitung(d: Path) -> bool:
    try:
        return bool(os.lstat(d).st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False


def python_laeuft(python: Path) -> bool:
    try:
        return subprocess.run([str(python), "-c", "print(1)"], capture_output=True, text=True, timeout=60,
                              **_ohne_fenster()).stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def home_festlegen(venv: Path, python: Path) -> None:
    """pyvenv.cfg: „home“ auf den echten Python-Ordner statt auf uvs Verknüpfung „cpython-3.11-…“. Die Verknüpfung
    blockiert Windows manchmal (os error 448) oder legt sie bei einer Neuinstallation neu an – das KI-Paket fände sein
    Python dann nicht mehr („uv trampoline failed to spawn Python child process“)."""
    cfg = venv / "pyvenv.cfg"
    try:
        zeilen = cfg.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    neu = [f"home = {python.parent}" if z.strip().lower().startswith("home") and "=" in z else z for z in zeilen]
    if neu != zeilen:
        cfg.write_text("\n".join(neu) + "\n", encoding="utf-8")


def reparieren() -> bool:
    """Vor jedem Start: Zeigt das KI-Paket auf ein Python, das es nicht (mehr) gibt, auf das vorhandene umstellen.
    True, wenn etwas geändert wurde."""
    cfg = pfade.motor() / "pyvenv.cfg"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return False
    home = next((z.split("=", 1)[1].strip() for z in text.splitlines() if z.strip().lower().startswith("home")), "")
    exe = "python.exe" if sys.platform == "win32" else "bin/python3.11"
    echt = python_finden(pfade.basis() / "python") or python_finden(pfade.basis() / "python-ohne-uv")
    if echt is None or (home and Path(home) == echt.parent and (Path(home) / exe).is_file()):
        return False
    if home and (Path(home) / exe).is_file() and "cpython-3.11-" not in Path(home).name:
        return False  # zeigt schon auf einen echten, vorhandenen Ordner
    home_festlegen(pfade.motor(), echt)
    return True


def diagnose() -> list[str]:
    """Wenn das KI-Paket sein Python nicht findet („uv trampoline failed“): was steht in pyvenv.cfg, gibt es den
    Ordner, welche Pythons liegen da? Nur Pfade und Ja/Nein – fürs Protokoll, damit die Ursache beim nächsten Mal
    nicht wieder im Dunkeln bleibt."""
    exe = "python.exe" if sys.platform == "win32" else "bin/python3.11"
    zeilen = []
    cfg = pfade.motor() / "pyvenv.cfg"
    try:
        text = cfg.read_text(encoding="utf-8")
        home = next((z.split("=", 1)[1].strip() for z in text.splitlines() if z.strip().lower().startswith("home")), "")
        uv = next((z.split("=", 1)[1].strip() for z in text.splitlines() if z.strip().lower().startswith("uv")), "?")
        zeilen.append(f"pyvenv.cfg: home = {home or '(fehlt)'} (uv {uv})")
        if home:
            zeilen.append(f"  Ordner vorhanden: {'ja' if Path(home).is_dir() else 'nein'}, "
                          f"{exe}: {'ja' if (Path(home) / exe).is_file() else 'nein'}")
    except OSError as e:
        zeilen.append(f"pyvenv.cfg nicht lesbar: {type(e).__name__}")
    for name in ("python", "python-ohne-uv"):
        ordner = pfade.basis() / name
        if not ordner.is_dir():
            zeilen.append(f"{name}\\: fehlt")
            continue
        eintraege = []
        for d in sorted(ordner.iterdir()):
            art = "Verknüpfung" if (d.is_symlink() or _ist_umleitung(d)) else "Ordner" if d.is_dir() else "Datei"
            hat = (d / exe).is_file() if d.is_dir() else False
            eintraege.append(f"{d.name} ({art}{', python' if hat else ''})")
        zeilen.append(f"{name}\\: " + (", ".join(eintraege) or "leer"))
    return zeilen


def python_finden(ordner: Path) -> Path | None:
    """Neueste von uv installierte Python-3.11-Fassung (nicht die Verknüpfung „cpython-3.11-…“)."""
    kandidaten = []
    for d in ordner.glob("cpython-3.11.*"):
        exe = d / "python.exe" if sys.platform == "win32" else d / "bin" / "python3.11"
        teile = d.name.split("-")[1].split(".")
        if exe.is_file() and len(teile) == 3 and teile[2].isdigit():
            kandidaten.append((int(teile[2]), exe))
    return max(kandidaten)[1] if kandidaten else None


# Pakete, die es im PyTorch-Verzeichnis nicht für jedes System gibt (torchcodec cu128: nur Linux). Sie kommen von
# PyPI in der gewöhnlichen Fassung – Taleward übergibt Audio im Speicher, torchcodec dekodiert nichts.
OHNE_TORCH_VERZEICHNIS = ("torchcodec",)


def anforderungen_lesen(quelle: str) -> str:
    if quelle.startswith(("http://", "https://")):
        try:
            r = httpx.get(quelle, timeout=30, follow_redirects=True, headers={"User-Agent": f"TalewardWorker/{VERSION}"})
        except httpx.HTTPError as e:
            raise MotorFehler(_netzcode(e)) from None
        if r.status_code != 200:
            raise MotorFehler("installation", f"engine-requirements.txt: HTTP {r.status_code}")
        return r.text
    return Path(quelle).read_text(encoding="utf-8")


def anforderungen_aufteilen(text: str) -> tuple[str, str]:
    """engine-requirements.txt → (über das PyTorch-Verzeichnis, direkt von PyPI)."""
    basis, extra = [], []
    for zeile in text.splitlines():
        name = zeile.split("==")[0].split(";")[0].strip().lower()
        (extra if name in OHNE_TORCH_VERZEICHNIS else basis).append(zeile)
    return "\n".join(basis) + "\n", "\n".join(extra) + ("\n" if extra else "")


# Kommen in der Variante für die Grafikkarte vom PyTorch-Verzeichnis – dafür stehen keine Prüfsummen in uv.lock
PYTORCH_VERZEICHNIS = ("torch", "torchaudio", "torchvision")
# Nur für die Grafikkarte unter Linux nötig (bringt PyTorch von PyPI mit), bei „nur Prozessor“ weglassen
NUR_CUDA = ("nvidia-", "triton")
# Ältere Server-Fassungen hatten imageio-ffmpeg nicht in uv.lock (wurde ohne feste Fassung nachinstalliert)
IMAGEIO_FFMPEG = """imageio-ffmpeg==0.6.0 \\
    --hash=sha256:02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a \\
    --hash=sha256:196faa79366b4a82f95c0f4053191d2013f4714a715780f0ad2a68ff37483cc2 \\
    --hash=sha256:1d47bebd83d2c5fc770720d211855f208af8a596c82d17730aa51e815cdee6dc \\
    --hash=sha256:9d2baaf867088508d4a3458e61eeb30e945c4ad8016025545f66c4b5aaef0a61 \\
    --hash=sha256:b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742 \\
    --hash=sha256:c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282 \\
    --hash=sha256:e2556bed8e005564a9f925bb7afa4002d82770d6b08825078b7697ab88ba1755
"""


# Bauwerkzeug für den Taleward-Code ([build-system] im Server: hatchling) mit seinen Abhängigkeiten, Python 3.11.
# Fest mit Prüfsummen, damit beim Bauen nichts Ungeprüftes von PyPI läuft. Neu erzeugen mit:
#   echo hatchling | uv pip compile - --generate-hashes --universal --python-version 3.11 --no-header --no-annotate
BAUWERKZEUG = """hatchling==1.32.4 \\
    --hash=sha256:08ecf7548fb48205e7f213d70c71e67b8271b7242093dc3f1da578b42c734a2c \\
    --hash=sha256:c4468f73144c054d2aab4ef0f0378c43b9878bf07f8ffd6b79690e970d375f07
packaging==26.3 \\
    --hash=sha256:94edc256424af38762eb31306eed28beb9f0efc50a8837492c9d6fd6004aed79 \\
    --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c
pathspec==1.1.1 \\
    --hash=sha256:17db5ecd524104a120e173814c90367a96a98d07c45b2e10c2f3919fff91bf5a \\
    --hash=sha256:a00ce642f577bf7f473932318056212bc4f8bfdf53128c78bbd5af0b9b20b189
pluggy==1.6.0 \\
    --hash=sha256:7dcc130b76258d33b90f61b658791dede3486c3e6bfb003ee5c9bfb396dd22f3 \\
    --hash=sha256:e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746
tomlkit==0.15.1 \\
    --hash=sha256:177a05aece5a8ca5266fd3c448abb47b8d352f09d477d3ca8332db4d89b24304 \\
    --hash=sha256:e25bbf38843005246210a12982776f27f99cb9be67160e14434d0c0d21ee1e97
trove-classifiers==2026.9.21.13 \\
    --hash=sha256:0a9ebc8d4e2f3e8a22848c5258033035bec17a3012ac3fea16dbaa764489eb71 \\
    --hash=sha256:8b1ff4f9c191b1040b71c37f1e445ab99732911e3cd91de52838453a854d7a17
"""

PYTORCH_LISTE = "pytorch-pruefsummen.txt"
# Ab dieser Server-Fassung trägt jede Freigabe die Liste – ohne sie wird nichts installiert. Ältere Fassungen (alle mit
# torch 2.8.0) gehen noch ohne; sobald die erste Liste vorliegt, kommt sie für diese fest in die Worker-App.
PYTORCH_LISTE_PFLICHT_AB = "0.4.103"
MAX_PYTORCH_LISTE = 256 * 1024
_PYTORCH_ZEILE = re.compile(r"^([a-z0-9]+) ([a-z0-9_.-]+)==([A-Za-z0-9.+!]+)((?: --hash=sha256:[0-9a-f]{64})+)$")


def liste_pflicht(fassung: str, q: dict) -> bool:
    """Braucht diese Fassung die PyTorch-Liste? Entwicklung (lokaler Ordner) nie."""
    from taleward_worker.selbstupdate import neuer

    if q.get("ref") == "lokal" or not fassung:
        return False
    return not neuer(PYTORCH_LISTE_PFLICHT_AB, fassung)


def installierte_pakete(py: str, umgebung: dict) -> set[str]:
    """Namen der Pakete in einer Umgebung (klein geschrieben, „_“ als „-“)."""
    try:
        r = subprocess.run([uv_programm(), "pip", "list", "--python", py, "--format", "json"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120,
                           env={**umgebung, "PYTHONUTF8": "1"}, cwd=pfade.basis(), **_ohne_fenster())
        liste = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        liste = []
    return {str(p.get("name", "")).lower().replace("_", "-") for p in liste if isinstance(p, dict)}


def pytorch_pruefsummen(q: dict) -> dict[tuple[str, str, str], str] | None:
    """Liste `pytorch-pruefsummen.txt` am Release laden – nur, wenn die (unterschriebene) Freigabe sie mit SHA-256
    nennt, und nur, wenn die Datei genau dazu passt. → {(Variante, Paket, volle Fassung): „--hash=… …“}.
    None: Die Freigabe nennt keine Liste (ältere Fassung oder Entwicklung)."""
    import hashlib

    erwartet = (q.get("anhaenge") or {}).get(PYTORCH_LISTE)
    if not erwartet or not q.get("release"):
        return None
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": f"TalewardWorker/{VERSION}"}) as k:
            r = k.get(f"{q['release']}/{PYTORCH_LISTE}")
    except httpx.HTTPError as e:
        raise MotorFehler(_netzcode(e)) from None
    if r.status_code != 200 or len(r.content) > MAX_PYTORCH_LISTE:
        raise MotorFehler("installation", f"{PYTORCH_LISTE}: HTTP {r.status_code}")
    if hashlib.sha256(r.content).hexdigest() != erwartet:
        raise MotorFehler("installation", f"{PYTORCH_LISTE} passt nicht zur Freigabe.")
    return pytorch_liste_lesen(r.content.decode("utf-8"))


def pytorch_liste_lesen(text: str) -> dict[tuple[str, str, str], str]:
    liste: dict[tuple[str, str, str], str] = {}
    for zeile in text.splitlines():
        if not zeile.strip() or zeile.startswith("#"):
            continue
        m = _PYTORCH_ZEILE.match(zeile.strip())
        if not m:
            raise MotorFehler("installation", f"{PYTORCH_LISTE} ist nicht lesbar.")
        liste[(m.group(1), m.group(2), m.group(3))] = m.group(4).strip()
    return liste


def pytorch_mit_pruefsummen(pytorch: str, liste: dict[tuple[str, str, str], str], variante: str) -> str:
    """„torch==2.8.0 ; Bedingung“ → „torch==2.8.0+cu128 ; Bedingung --hash=…“. uv prüft Prüfsummen nur gegen die volle
    Fassung (mit „+cu128“), deshalb steht sie in der Anforderung. Fehlt ein Eintrag: keine Installation."""
    zeilen = []
    for zeile in pytorch.splitlines():
        if not zeile.strip():
            continue
        anforderung, _, bedingung = zeile.partition(";")
        name, _, fassung = anforderung.strip().partition("==")
        voll = f"{fassung}+{variante}"
        hashes = liste.get((variante, name.lower(), voll))
        if not hashes:
            raise MotorFehler("installation", f"Keine Prüfsumme für {name} {voll} in {PYTORCH_LISTE}.")
        zeilen.append(f"{name}=={voll}" + (f" ; {bedingung.strip()}" if bedingung.strip() else "") + f" {hashes}")
    return "\n".join(zeilen) + "\n"


def pruefsummen(q: dict, umgebung: dict, ordner: Path) -> tuple[str, str]:
    """pyproject.toml und uv.lock genau des freigegebenen Commits laden und daraus mit uv die Paketlisten mit
    Prüfsummen erzeugen (ohne Netz, ohne neu aufzulösen): (Grundpakete, mit KI-Bibliotheken)."""
    shutil.rmtree(ordner, ignore_errors=True)
    ordner.mkdir(parents=True)
    for name, schluessel in (("pyproject.toml", "projekt"), ("uv.lock", "lock")):
        (ordner / name).write_text(anforderungen_lesen(q[schluessel]), encoding="utf-8")
    listen = []
    for name, zusatz in (("basis.txt", []), ("ki.txt", ["--extra", "ki"])):
        befehl = [uv_programm(), "export", "--frozen", "--format", "requirements-txt", "--no-dev",
                  "--no-emit-project", "--no-header", "--no-annotate", "-q", "-o", str(ordner / name), *zusatz]
        try:
            r = subprocess.run(befehl, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=120, env=umgebung, cwd=ordner, **_ohne_fenster())
        except (OSError, subprocess.SubprocessError) as e:
            raise MotorFehler("installation", f"Paketliste mit Prüfsummen nicht erzeugbar: {e}") from None
        if r.returncode != 0 or not (ordner / name).exists():
            raise MotorFehler("installation",
                              "Paketliste mit Prüfsummen nicht erzeugbar: " + (r.stderr or r.stdout)[-800:])
        listen.append((ordner / name).read_text(encoding="utf-8"))
    return listen[0], listen[1]


def eintraege(text: str) -> list[tuple[str, str]]:
    """requirements.txt mit Fortsetzungszeilen → [(Paketname, ganzer Eintrag)]."""
    liste: list[tuple[str, str]] = []
    for zeile in text.splitlines():
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        if zeile[:1].isspace() and liste:
            name, block = liste[-1]
            liste[-1] = (name, block + "\n" + zeile)
        else:
            name = re.split(r"[=<>!~ ;\[]", zeile.strip(), maxsplit=1)[0].lower().replace("_", "-")
            liste.append((name, zeile))
    return liste


def pruefsummen_aufteilen(text: str, mit_cuda: bool = True) -> tuple[str, str]:
    """→ (Einträge mit Prüfsummen, PyTorch-Familie als „name==version ; Bedingung“ ohne Prüfsummen)."""
    geprueft, pytorch = [], []
    for name, block in eintraege(text):
        if name in PYTORCH_VERZEICHNIS:
            pytorch.append(block.split("\\")[0].strip())
        elif not mit_cuda and name.startswith(NUR_CUDA):
            continue
        else:
            if "--hash=" not in block:
                raise MotorFehler("installation", f"Für {name} fehlt die Prüfsumme in uv.lock.")
            geprueft.append(block)
    return "\n".join(geprueft) + "\n", "\n".join(pytorch) + ("\n" if pytorch else "")


def ffmpeg_einrichten(python: Path) -> Path:
    """ffmpeg aus imageio-ffmpeg als „ffmpeg(.exe)“ in den Werkzeug-Ordner legen (WhisperX ruft „ffmpeg“ auf)."""
    aus = _python_pruefen(str(python), "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())", timeout=60)
    quelle_pfad = Path(aus.stdout.strip().splitlines()[-1]) if aus.stdout.strip() else Path("fehlt")
    if aus.returncode != 0 or not quelle_pfad.exists():
        raise MotorFehler("ffmpeg", aus.stderr[-1000:])
    ziel = pfade.werkzeuge() / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    ziel.unlink(missing_ok=True)
    shutil.copy2(quelle_pfad, ziel)
    if sys.platform != "win32":
        ziel.chmod(0o755)
    return ziel


# Umgebungsvariablen, die fremde Python- oder uv-Installationen hinterlassen und den Motor verbiegen würden:
# PYTHONHOME (Anaconda-Altlast → „Fatal Python error: init_fs_encoding“), PYTHONPATH (fremdes numpy/torch),
# UV_*/PIP_* (andere Indexe, Konfigurationsdateien, Offline-Modus).
_FREMDE_VARIABLEN = ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONSAFEPATH",
                     "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "PIP_REQUIRE_VIRTUALENV")


def saubere_umgebung() -> dict:
    """Kopie der Umgebung ohne alles, was Python oder uv von außen umstellt."""
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in _FREMDE_VARIABLEN and not k.upper().startswith(("UV_", "PIP_"))}
    return env


def _python_pruefen(py: str, code: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Kurzer Probelauf im Motor – immer UTF-8, unabhängig von der Windows-Codepage und von Umgebungsvariablen des
    Benutzers (PYTHONHOME, PYTHONPATH …), die den Motor sonst aus dem Tritt bringen."""
    env = saubere_umgebung()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return subprocess.run([py, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, **_ohne_fenster())


def torch_pruefen(py: str) -> dict:
    """Was ist wirklich drin? {"cuda": "12.8" | None, "sichtbar": bool, "karte": str | None} – oder {"fehler": …}."""
    code = ("import json, torch; v = torch.version.cuda; s = bool(v) and torch.cuda.is_available(); "
            "print(json.dumps({'cuda': v, 'sichtbar': s, 'karte': torch.cuda.get_device_name(0) if s else None}))")
    try:
        r = _python_pruefen(py, code, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return {"fehler": f"{type(e).__name__}: {e}"}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"fehler": (r.stderr or r.stdout)[-1000:].strip() or "PyTorch antwortet nicht."}


SCHWERE_PAKETE = ("torch", "torchaudio", "torchvision", "nvidia-", "triton", "ctranslate2", "onnxruntime",
                  "cudnn", "cublas", "cuda-")


def schwer(paket: str) -> bool:
    name = paket.lower().split("==")[0].strip()
    return any(name.startswith(s) or name == s.rstrip("-") for s in SCHWERE_PAKETE)


def geaenderte_pakete(ausgabe: str) -> list[str]:
    """Aus dem uv-Probelauf („ + paket==1.0“, „ - paket==0.9“) die betroffenen Paketnamen."""
    namen: list[str] = []
    for zeile in ausgabe.splitlines():
        z = zeile.strip()
        if z[:2] in ("+ ", "- ") and len(z) > 2:
            name = z[2:].split("==")[0].split(" ")[0].strip()
            if name and name not in namen:
                namen.append(name)
    return namen


PLATZ_RESERVE_GB = 3  # Luft für Windows und die Aufnahme, die gerade bearbeitet wird


def platz_fehlt_gb(testmodus: bool, backend: str) -> float:
    """Wie viele GB fehlen für die Installation? 0, wenn genug frei ist. Beim Aktualisieren liegt der alte Motor
    noch da, und uv hält die entpackten Pakete im Zwischenspeicher – Spitze etwa das Doppelte des Pakets."""
    system = "test" if testmodus else "cpu" if backend == "cpu" else ("windows" if sys.platform == "win32" else "linux")
    noetig_gb = ERWARTET_MB[system] / 1024 * 2 + PLATZ_RESERVE_GB
    try:
        frei_gb = shutil.disk_usage(pfade.basis()).free / 1024 ** 3
    except OSError:
        return 0.0
    return max(0.0, noetig_gb - frei_gb)


def fehlercode(ausgabe: str) -> str:
    """Aus der letzten uv-Ausgabe einen Fehlercode für die Oberfläche machen (Texte in ui/texte.js, f_…)."""
    klein = ausgabe.lower()
    if any(s in klein for s in ("no space left", "speicherplatz", "not enough space", "os error 112")):
        return "kein_platz"
    if "os error 206" in klein or "filename or extension is too long" in klein or "zu lang" in klein:
        return "pfad_zu_lang"
    if any(s in klein for s in ("os error 5", "os error 32", "access is denied", "zugriff verweigert",
                                "being used by another process")):
        return "dateien_in_benutzung"
    if any(s in klein for s in ("dns error", "timed out", "failed to fetch", "connection reset",
                                "certificate", "tls handshake")):
        return "netz"
    return "installation"


def umbenennen(von: Path, nach: Path, versuche: int = 20, pause: float = 0.5) -> None:
    """Ordner umbenennen, unter Windows mit Geduld: direkt nach dem Beenden des Motors hält Windows Handles noch
    kurz offen, und der Virenscanner liest gerade die frisch geschriebenen Dateien – jede offene Datei lässt die
    Umbenennung mit „Zugriff verweigert“ scheitern. Nach den Versuchen ein sprechender Fehler statt „unerwartet“."""
    letzter: OSError | None = None
    for _ in range(max(1, versuche)):
        try:
            von.rename(nach)
            return
        except FileExistsError:
            ordner_entfernen(nach)
            letzter = FileExistsError(str(nach))
        except PermissionError as e:
            letzter = e
        except OSError as e:
            if getattr(e, "winerror", None) not in (5, 32, 145):  # Zugriff verweigert, in Benutzung, nicht leer
                raise
            letzter = e
        time.sleep(pause)
    raise MotorFehler("dateien_in_benutzung", f"{von.name} → {nach.name}: {letzter}")


def ordner_entfernen(p: Path) -> None:
    """rmtree, das auch schreibgeschützte Dateien (Windows) wegräumt und bei gesperrten nicht aufgibt."""
    if not p.exists():
        return

    def nachhelfen(funktion, pfad, _info):
        try:
            os.chmod(pfad, 0o700)
            funktion(pfad)
        except OSError:
            pass

    for _ in range(3):
        shutil.rmtree(p, onerror=nachhelfen)
        if not p.exists():
            return
        time.sleep(0.5)


def installiert() -> dict | None:
    try:
        d = json.loads((pfade.motor() / "taleward-motor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if pfade.motor_python().exists() else None


def entfernen(modelle_auch: bool = False) -> None:
    for p in [pfade.motor(), pfade.basis() / "motor-neu", pfade.basis() / "motor-alt", pfade.basis() / "python",
              pfade.basis() / "python-ohne-uv", pfade.basis() / "cache", pfade.werkzeuge()]:
        ordner_entfernen(p)
    if modelle_auch:
        shutil.rmtree(pfade.modelle(), ignore_errors=True)
