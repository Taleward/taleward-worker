"""Worker 0.3.22: „Einstellungen öffnen“ bei gesperrtem Mikrofon – Absprache mit der App im Rahmen per postMessage."""
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "taleward_worker" / "ui"


def test_mikrofon_einstellungen_nur_feste_seite(monkeypatch):
    """Worker 0.3.22: Die App im Rahmen kann bitten, die Windows-Seite fürs Mikrofon zu öffnen – nur diese, gedrosselt,
    nur auf Nachrichten genau vom App-Rahmen."""
    import os
    import sys

    from taleward_worker import oberflaeche

    geoeffnet = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", lambda z: geoeffnet.append(z), raising=False)
    api = oberflaeche.Api.__new__(oberflaeche.Api)
    api.mikrofon_einstellungen()
    api.mikrofon_einstellungen()  # gleich danach: gedrosselt
    assert geoeffnet == ["ms-settings:privacy-microphone"]
    js = (UI / "desktop.js").read_text(encoding="utf-8")
    block = js[js.index('window.addEventListener("message"'):]
    block = block[:block.index("});") + 3]
    assert 'e.origin !== APP_HERKUNFT' in block and "e.source !== f.contentWindow" in block
    assert 'api.mikrofon_einstellungen()' in block and "d.url" not in block
    assert 'postMessage({ taleward: "desktop", can: ["mic-settings"] }, APP_HERKUNFT)' in js
    assert 'const APP_HERKUNFT = "http://localhost:8000";' in js
