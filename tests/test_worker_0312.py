"""Worker 0.3.12: Beim Einfügen zählt der Code direkt nach „Code“, sonst der letzte; ohne Code wird nichts gesendet."""
import pytest

from tests.test_worker_0310 import _code_formen

FAELLE = [
    # Adresse oder Schlüssel davor: nicht das erste Stück nehmen
    ("Server https://abcd-efgh.example Kopplungscode: K7QM-2XPA", "K7QM-2XPA"),
    ("wk.ABCD-EFGH.xyz und dann K7QM-2XPA", "K7QM-2XPA"),
    # direkt nach „Code“ gewinnt, auch wenn danach noch etwas kommt
    ("Code K7QM-2XPA, gültig bis 21:30, Ersatz WXYZ-RSTU", "K7QM-2XPA"),
    ("pairing code: k7qm-2xpa (expires soon)", "K7QM-2XPA"),
    # ein Datum ist nie ein Code (0 und 1 kommen in Codes nicht vor)
    ("Gültig ab 2026-10-20, Code K7QM-2XPA", "K7QM-2XPA"),
]


@pytest.mark.parametrize("eingabe, code", FAELLE)
def test_programmoberflaeche_nimmt_den_richtigen_code(eingabe, code):
    assert _code_formen(eingabe) == [code]


@pytest.mark.parametrize("eingabe, code", FAELLE)
def test_server_teil_nimmt_den_richtigen_code(eingabe, code):
    from taleward_worker import server

    assert server.code_finden(eingabe) == code


def test_laengerer_text_ohne_code_sendet_nichts(monkeypatch):
    from taleward_worker import server

    text = "Hier steht kein Kopplungscode, nur ein Datum 2026-10-20."
    assert _code_formen(text) == [""]
    assert server.code_finden(text) == ""
    gesendet = []
    monkeypatch.setattr(server, "_klient", lambda: gesendet.append(1))
    with pytest.raises(server.ServerFehler) as e:
        server.koppeln("https://server.example", text, "PC")
    assert e.value.code == "code_fehlt" and gesendet == []


def test_tippen_bleibt_wie_bisher():
    assert _code_formen("k7", "k7qm", "k7qm-", "k7qm2", "k7qm-2xpa") == ["K7", "K7QM", "K7QM-", "K7QM-2", "K7QM-2XPA"]
