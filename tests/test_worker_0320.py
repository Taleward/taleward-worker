"""Worker 0.3.20: „Passwort vergessen“ bei „Alles auf diesem PC“ – erst Windows Hello, dann das neue Passwort über die
Standardeingabe (`passwort-neu`, Server ab 0.4.118); ohne Bestätigung wird nichts gesetzt."""
import re
import subprocess
from pathlib import Path

from taleward_worker import passwort

UI = Path(__file__).resolve().parent.parent / "taleward_worker" / "ui"


class Cli:
    def __init__(self, verwalter="benjamin\n", fehlt=False, rc_neu=0):
        self.aufrufe, self.verwalter, self.fehlt, self.rc_neu = [], verwalter, fehlt, rc_neu

    def __call__(self, *args, eingabe=None):
        self.aufrufe.append((args, eingabe))
        if args[0] == "verwalter":
            if self.fehlt:
                return subprocess.CompletedProcess(args, 2, "", "Error: No such command 'verwalter'.")
            return subprocess.CompletedProcess(args, 0, self.verwalter, "")
        return subprocess.CompletedProcess(args, self.rc_neu, "Passwort geändert.\n", "")


def test_erst_hello_dann_passwort():
    cli, gefragt = Cli(), []
    r = passwort.neu_setzen(cli, "benjamin", "ganz-neu-123", "de", bestaetigen=lambda t: gefragt.append(t) or "ok")
    assert r == {"ok": True} and "benjamin" in gefragt[0]
    args, eingabe = cli.aufrufe[-1]
    assert args == ("passwort-neu", "-u", "benjamin") and eingabe == "ganz-neu-123\n"  # nie in der Befehlszeile
    assert all("ganz-neu-123" not in " ".join(a) for a, _ in cli.aufrufe)


def test_ohne_hello_nichts():
    for ergebnis in ("abgebrochen", "abgelehnt", "nicht_eingerichtet", "fehler"):
        cli = Cli()
        r = passwort.neu_setzen(cli, "benjamin", "ganz-neu-123", bestaetigen=lambda t, e=ergebnis: e)
        assert r == {"ok": False, "fehler": "hello_" + ergebnis}
        assert [a[0][0] for a in cli.aufrufe] == ["verwalter"]


def test_nur_verwalter_und_alter_server():
    cli = Cli()
    assert passwort.neu_setzen(cli, "spielerin", "ganz-neu-123", bestaetigen=lambda t: "ok")["fehler"] == "konto"
    assert passwort.konten(Cli(fehlt=True)) == {"ok": False, "fehler": "zu_alt"}
    assert passwort.konten(Cli(verwalter="a\nb\n")) == {"ok": True, "konten": ["a", "b"]}


def test_ausserhalb_windows_nicht_eingerichtet():
    import sys

    if sys.platform != "win32":
        assert passwort.windows_bestaetigen("x") == "nicht_eingerichtet"


def test_oberflaeche_vollstaendig():
    html = (UI / "desktop.html").read_text(encoding="utf-8")
    assert 'id="es-passwort"' in html and 'id="verwaltung-passwort"' in html and 'data-dialog="passwort"' in html
    texte = (UI / "desktop-texte.js").read_text(encoding="utf-8")
    de, en = texte.split("\n  en: {")
    for schluessel in set(re.findall(r"\b((?:d_pw|pw_f)_\w+):", texte)):
        assert schluessel + ":" in de and schluessel + ":" in en, schluessel
    js = (UI / "desktop.js").read_text(encoding="utf-8")
    assert 'riskant("passwort_neu"' in js  # nur mit Prüfwert
