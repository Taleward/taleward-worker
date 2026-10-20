/* Brücke zum Programm. Ruft Funktionen der Api über pywebview auf, ohne die Stellvertreter, die pywebview mit
   „new Function“ erzeugt (das verbietet die Sicherheitsrichtlinie der Oberfläche). Das Programm nimmt nur Nachrichten
   dieser Seite und nur Namen seiner Api an. */
(function () {
  "use strict";
  let warten = null;
  const da = () => !!(window.pywebview && typeof window.pywebview._jsApiCallback === "function"
    && typeof window.pywebview._checkValue === "function" && window.pywebview._returnValuesCallbacks);
  function bereit() {
    if (!warten) {
      warten = new Promise((fertig) => {
        if (da()) return fertig();
        const z = setInterval(() => { if (da()) { clearInterval(z); fertig(); } }, 25);
      });
    }
    return warten;
  }
  function rufen(name, args) {
    return bereit().then(() => new Promise((fertig, fehler) => {
      const p = window.pywebview;
      const id = String(Math.random()).slice(2) + String(Date.now());
      p._returnValuesCallbacks[name] = p._returnValuesCallbacks[name] || {};
      p._checkValue(name, fertig, fehler, id);
      p._jsApiCallback(name, args, id);
    }));
  }
  const api = new Proxy({}, {
    get: (_, name) => (typeof name === "string" && name !== "then" ? (...args) => rufen(name, args) : undefined),
  });
  window.TalewardBruecke = Object.freeze({ bereit, api });
})();
