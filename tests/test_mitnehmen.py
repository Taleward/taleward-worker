"""Sicherung mit Passwort und Hilfe-Datei (ab Worker 0.3.4)."""
import subprocess
import time
from pathlib import Path

from tests.test_betriebsart import _app


def _warten(bedingung, frist=5.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.02)
    return bedingung()


class _Cli:
    def __init__(self, code=0, ausgabe=""):
        self.code, self.ausgabe, self.aufrufe = code, ausgabe, []

    def __call__(self, *argumente, eingabe=None):
        self.aufrufe.append((argumente, eingabe))
        return subprocess.CompletedProcess(argumente, self.code, self.ausgabe, "")


def test_sicherung_speichern(monkeypatch, tmp_path):
    app, api = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=True)
    assert api.sicherung_speichern("kurz", "kurz")["fehler"] == "password_too_short"
    assert api.sicherung_speichern("Lindwurm-Kessel-7", "anders-1234")["fehler"] == "passwoerter_ungleich"
    monkeypatch.setattr(app, "datei_waehlen", lambda **k: None)
    assert api.sicherung_speichern("Lindwurm-Kessel-7", "Lindwurm-Kessel-7")["fehler"] == "abgebrochen"
    monkeypatch.setattr(app, "datei_waehlen", lambda **k: tmp_path / "stick" / "meine-runden")
    assert api.sicherung_speichern("Lindwurm-Kessel-7", "Lindwurm-Kessel-7")["fehler"] == "startet_noch"
    app.server.bereit.set()
    cli = _Cli(0, f"Sicherung gespeichert: {tmp_path / 'stick' / 'meine-runden.taleward'}\n")
    monkeypatch.setattr(app.ep_update, "cli", cli)
    assert api.sicherung_speichern("Lindwurm-Kessel-7", "Lindwurm-Kessel-7") == {"ok": True}
    assert _warten(lambda: app.sicherung.phase == "fertig")
    (argumente, eingabe), = cli.aufrufe
    assert argumente == ("sicherung-export", str(tmp_path / "stick" / "meine-runden.taleward"))
    assert eingabe == "Lindwurm-Kessel-7\n"  # nur über die Standardeingabe
    assert api.zustand()["taleward"]["sicherung"]["datei"] == "meine-runden.taleward"
    assert "Lindwurm" not in "\n".join(app.dienst.protokoll.zeilen)


def test_sicherung_zurueckspielen(monkeypatch, tmp_path):
    app, api = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=False)
    datei = tmp_path / "alt.taleward"
    datei.write_bytes(b"TWSICH01")
    monkeypatch.setattr(app, "datei_waehlen", lambda **k: datei)
    assert api.sicherung_zurueckspielen("Lindwurm-Kessel-7")["fehler"] == "keine_datei"
    assert api.sicherung_waehlen() == {"ok": True, "datei": "alt.taleward"}
    ablauf = []
    monkeypatch.setattr(app.dienst, "stoppen", lambda *a, **k: ablauf.append("worker aus"))
    monkeypatch.setattr(app.server, "stoppen", lambda *a, **k: ablauf.append("taleward aus"))
    monkeypatch.setattr(app, "taleward_starten", lambda: ablauf.append("taleward an"))
    cli = _Cli(2)
    monkeypatch.setattr(app.ep_update, "cli", cli)
    assert api.sicherung_zurueckspielen("falsch-123") == {"ok": True}
    assert _warten(lambda: app.sicherung.phase == "fehler")
    assert app.sicherung.fehler == "passwort" and ablauf == ["worker aus", "taleward aus", "taleward an"]
    assert cli.aufrufe == [(("sicherung-import", str(datei), "--ja"), "falsch-123\n")]
    assert not app.e.konto_angelegt
    cli.code = 0
    ablauf.clear()
    assert api.sicherung_zurueckspielen("Lindwurm-Kessel-7") == {"ok": True}
    assert _warten(lambda: app.sicherung.phase == "fertig")
    assert ablauf[-1] == "taleward an" and app.e.konto_angelegt  # neuer PC: das Konto kam mit der Sicherung
    assert app.sicherung.gewaehlt is None


def test_sicherung_nicht_waehrend_eines_updates(monkeypatch):
    app, api = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=True)
    app.ep_update.phase = "tauschen"
    app.sicherung.gewaehlt = Path(__file__)
    assert api.sicherung_zurueckspielen("Lindwurm-Kessel-7")["fehler"] == "beschaeftigt"
    assert api.betrieb_wechseln("verbunden")["fehler"] == "beschaeftigt"


def test_sicherung_nur_bei_alles_auf_diesem_pc(monkeypatch):
    app, api = _app(monkeypatch, betrieb="verbunden", server="https://verein.invalid", token="wk.1.x")
    assert api.sicherung_speichern("Lindwurm-Kessel-7", "Lindwurm-Kessel-7")["ok"] is False
    assert api.sicherung_waehlen()["ok"] is False


# ---------------------------------------------------------------- Hilfe-Datei
def test_schwaerzen(monkeypatch):
    from taleward_worker import mitnehmen

    heim = str(Path.home())
    text = mitnehmen.schwaerzen(
        f"Bearer wk.12.abcDEF-ghi und token=geheim123 sowie \"api_key\": \"sk-abcdefghijk\"\n"
        f"hf_ABCDEFGHIJKLMNOP · Code ABCD-EFGH · anna@beispiel.invalid · {heim}\\AppData\\Local\n"
        f"https://x.invalid/a?token=xyz&b=1 · dpapi:QUJDREVG · Passwort: Lindwurm-Kessel-7")
    for geheim in ("wk.12", "geheim123", "sk-abcdefghijk", "hf_ABCDEFGHIJKLMNOP", "ABCD-EFGH", "anna@",
                   heim, "xyz", "QUJDREVG", "Lindwurm"):
        assert geheim not in text, geheim
    assert "%USERPROFILE%" in text and "b=1" in text


def test_hilfe_datei_vorschau_und_speichern(monkeypatch, tmp_path):
    from taleward_worker import pfade

    app, api = _app(monkeypatch, betrieb="verbunden", server="https://verein.invalid", token="wk.5.sehrgeheim")
    app.dienst.protokoll.schreiben("Verbunden mit Bearer wk.5.sehrgeheim")
    (pfade.protokolle() / "server.log").write_text("nicht im Verbund-Betrieb\n")
    assert api.hilfe_speichern()["fehler"] == "keine_vorschau"
    text = api.hilfe_vorschau()["text"]
    assert "sehrgeheim" not in text and "Programm:" in text and "Verbunden mit" in text
    assert "nicht im Verbund-Betrieb" not in text
    ziel = tmp_path / "hilfe.txt"
    monkeypatch.setattr(app, "datei_waehlen", lambda **k: ziel)
    assert api.hilfe_speichern() == {"ok": True, "datei": "hilfe.txt"}
    assert ziel.read_text(encoding="utf-8") == text  # genau das, was gezeigt wurde
