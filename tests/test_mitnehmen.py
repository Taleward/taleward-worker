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
    assert api.sicherung_zurueckspielen(api._pruefwert, "Lindwurm-Kessel-7")["fehler"] == "keine_datei"
    assert api.sicherung_waehlen() == {"ok": True, "datei": "alt.taleward"}
    ablauf = []
    monkeypatch.setattr(app.dienst, "stoppen", lambda *a, **k: ablauf.append("worker aus"))
    monkeypatch.setattr(app.server, "stoppen", lambda *a, **k: ablauf.append("taleward aus"))
    monkeypatch.setattr(app, "taleward_starten", lambda: ablauf.append("taleward an"))
    cli = _Cli(2)
    monkeypatch.setattr(app.ep_update, "cli", cli)
    assert api.sicherung_zurueckspielen(api._pruefwert, "falsch-123") == {"ok": True}
    assert _warten(lambda: app.sicherung.phase == "fehler")
    assert app.sicherung.fehler == "passwort" and ablauf == ["worker aus", "taleward aus", "taleward an"]
    assert cli.aufrufe == [(("sicherung-import", str(datei), "--ja"), "falsch-123\n")]
    assert not app.e.konto_angelegt
    cli.code = 0
    ablauf.clear()
    assert api.sicherung_zurueckspielen(api._pruefwert, "Lindwurm-Kessel-7") == {"ok": True}
    assert _warten(lambda: app.sicherung.phase == "fertig")
    assert ablauf[-1] == "taleward an" and app.e.konto_angelegt  # neuer PC: das Konto kam mit der Sicherung
    assert app.sicherung.gewaehlt is None


def test_sicherung_nicht_waehrend_eines_updates(monkeypatch):
    app, api = _app(monkeypatch, betrieb="einzelplatz", konto_angelegt=True)
    app.ep_update.phase = "tauschen"
    app.sicherung.gewaehlt = Path(__file__)
    assert api.sicherung_zurueckspielen(api._pruefwert, "Lindwurm-Kessel-7")["fehler"] == "beschaeftigt"
    assert api.betrieb_wechseln(api._pruefwert, "verbunden")["fehler"] == "beschaeftigt"


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


def test_schwaerzen_weitere_formen():
    """Weitere Formen: Namen mit _, Werte in Anführungszeichen, Basic, JWT, Zugang in Adressen, Fragment,
    Schlüssel ohne Vorsilbe."""
    from taleward_worker import mitnehmen

    proben = {
        "MISTRAL_API_KEY=abc123geheim": "abc123geheim",
        "client_secret=xyz-geheim": "xyz-geheim",
        "access_token: tok-geheim-1": "tok-geheim-1",
        "passwort='mit leer zeichen'": "leer zeichen",
        'secret: "noch mehr leer"': "noch mehr",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==": "dXNlcjpwYXNzd29yZA",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl": "eyJ",
        "http://u:pw-geheim@localhost:8000/app/": "pw-geheim",
        "https://x.invalid/a#token=fragment-geheim": "fragment-geheim",
        "https://x.invalid/#/login?access_token=frage-geheim": "frage-geheim",
        "Schlüssel 4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e": "4f9a8b7c6d5e",
        "X_hf_ABCDEFGHIJKLMNOP": "ABCDEFGHIJKLMNOP",
        "smtp_pass=Kessel7": "Kessel7",
    }
    for probe, geheim in proben.items():
        assert geheim not in mitnehmen.schwaerzen(probe), probe
    for bleibt in ("Version 0.4.102 läuft", "Tests passed: 3, keyboard: de", "TALEWARD_EINZELPLATZ_WORKER_GESCHUETZT=true",
                   "http://localhost:8000/app/"):
        assert mitnehmen.schwaerzen(bleibt) == bleibt, bleibt


def test_schwaerzen_cookies_sitzungen_und_vorsilben():
    from taleward_worker import mitnehmen

    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
    proben = {
        "set-cookie: session=keks-geheim; Path=/; HttpOnly": "keks-geheim",
        "Cookie: sid=sitzung-geheim; theme=dunkel": "sitzung-geheim",
        "Authorization: Token tok-geheim-9": "tok-geheim-9",
        "passphrase: Lindwurm im Kessel": "Lindwurm",
        "auth=kurz-geheim und weiter": "kurz-geheim",
        "session_id=abc-geheim": "abc-geheim",
        "ghp_abcdefghijklmnopqrstuvwxyzABCD": "ghp_abcdefghij",
        "github_pat_abcdefghijklmn": "abcdefghijklmn",
        "xoxb-abcdefghijk": "abcdefghijk",
        "AKIAABCDEFGHIJKLMNOP": "AKIAABCDEFGHIJKLMNOP",
        "https://nutzer:p@ss@wort@example.invalid/pfad": "wort",
        "private_key: abc123geheim": "abc123geheim",
        pem: "b3BlbnNzaC1rZXktdjE",
    }
    for probe, geheim in proben.items():
        assert geheim not in mitnehmen.schwaerzen(probe), probe
    assert "example.invalid/pfad" in mitnehmen.schwaerzen("https://nutzer:p@ss@wort@example.invalid/pfad")
    for bleibt in ("Autor: Rabenfels", "Sitzungsdauer 3 h", "Fassung 0.3.7", "https://taleward.org/hilfe"):
        assert mitnehmen.schwaerzen(bleibt) == bleibt, bleibt


def test_schwaerzen_token_ohne_kopfzeile():
    from taleward_worker import mitnehmen

    assert "abcdef1234567" not in mitnehmen.schwaerzen("Token abcdef1234567")
    assert "abcdef1234567" not in mitnehmen.schwaerzen("auth failed: token abcdef1234567 expired")
    for bleibt in ("Token wurde erneuert", "Token abgelaufen, neuer Versuch"):
        assert mitnehmen.schwaerzen(bleibt) == bleibt, bleibt
