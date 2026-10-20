/* Taleward Worker – Oberfläche. Fragt den Zustand bei der App ab (pywebview js_api) und zeigt ihn an. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  let api = null, S = null, sprache = "de", reiter = "status", schritt = null, fertigZeigen = false, pruefErgebnis = null;
  let letzterLog = "", beschaeftigt = false, testWahl = null, prozessorWahl = false;
  const testmodus = () => (testWahl !== null ? testWahl : !!(S && S.einstellungen.testmodus));

  // ------------------------------------------------------------ Texte
  function t(k, v) {
    const tab = window.TEXTE[sprache] || {};
    let s = tab[k] !== undefined ? tab[k] : (window.TEXTE.de[k] !== undefined ? window.TEXTE.de[k] : k);
    if (v) for (const [a, b] of Object.entries(v)) s = s.split("{" + a + "}").join(b);
    return s;
  }
  function uebersetzen() {
    document.documentElement.lang = sprache;
    $$("[data-t]").forEach((el) => { el.textContent = t(el.dataset.t); });
  }
  function spracheWaehlen() {
    const eigen = S && S.einstellungen.sprache;
    const neu = eigen || ((navigator.language || "de").toLowerCase().startsWith("de") ? "de" : "en");
    if (neu !== sprache) { sprache = neu; uebersetzen(); }
  }
  const zahl = (n, st = 0) => Number(n).toLocaleString(sprache === "de" ? "de-DE" : "en-GB", { maximumFractionDigits: st, minimumFractionDigits: st });
  const gb = (mb) => mb >= 1024 ? zahl(mb / 1024, 1) + " GB" : zahl(mb) + " MB";
  function wann(zeit) {
    if (!zeit) return t("gleich");
    const s = Math.round(zeit - Date.now() / 1000);
    return s > 1 ? t("neuer_versuch_in", { s }) : t("gleich");
  }
  function toast(text) {
    const el = $("#toast"); el.textContent = text; el.hidden = false;
    clearTimeout(toast.z); toast.z = setTimeout(() => { el.hidden = true; }, 3200);
  }

  // ------------------------------------------------------------ Ansicht wählen
  const einzelplatz = () => !!(S && S.einstellungen.art === "einzelplatz");
  function imAssistenten() {
    if (!S) return false;
    if (!S.einstellungen.art) return true;
    if (einzelplatz()) return (!S.motor && !S.entwicklung) || !S.einstellungen.konto_angelegt || fertigZeigen;
    if (!S.einstellungen.gekoppelt) return true;
    if (fertigZeigen) return true;
    if (!S.motor && !S.entwicklung) return true;
    return false;
  }
  function assistentSchritt() {
    if (!S.einstellungen.art) return 0;
    if (einzelplatz()) {
      const inst = S.installation;
      const offen = inst && inst.phase !== "fertig";
      if (offen && (schritt === 3 || !S.motor)) return 3;
      if (!S.motor && !S.entwicklung) return schritt === 3 ? 3 : 2;
      if (!S.einstellungen.konto_angelegt) return 5;
      return 4;
    }
    if (!S.einstellungen.gekoppelt) return 1;
    const inst = S.installation;
    const offen = inst && inst.phase !== "fertig";
    if (offen && (schritt === 3 || !S.motor)) return 3;
    if (fertigZeigen && S.motor) return 4;
    if (schritt === 3) return 3;
    return 2;
  }
  function zeigeReiter(name) {
    reiter = name;
    $$(".reiter button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.reiter === name)));
    ["status", "einstellungen", "protokoll"].forEach((r) => { $("#reiter-" + r).hidden = r !== name || imAssistenten(); });
    if (name === "einstellungen") speicherLaden();
    if (name === "protokoll") logLaden(true);
  }

  // ------------------------------------------------------------ Darstellung
  function darstellen() {
    spracheWaehlen();
    const assistent = imAssistenten();
    $("#kopf").classList.toggle("kopf--assistent", assistent && reiter !== "protokoll");
    $("#assistent").hidden = !assistent || reiter === "protokoll";
    ["status", "einstellungen", "protokoll"].forEach((r) => {
      $("#reiter-" + r).hidden = r !== reiter || (assistent && r !== "protokoll");
    });
    if (assistent) assistentZeigen(); else statusZeigen();
    einstellungenZeigen();
  }

  function assistentZeigen() {
    const nr = assistentSchritt();
    const folge = einzelplatz() ? [2, 3, 5, 4] : [1, 2, 3, 4];
    $("#schritte").hidden = nr === 0;
    $$(".schritte li").forEach((li) => {
      const n = Number(li.dataset.schritt), pos = folge.indexOf(n), aktuell = folge.indexOf(nr);
      li.hidden = pos < 0;
      li.dataset.stand = pos < aktuell ? "fertig" : pos === aktuell ? "aktiv" : "";
    });
    $$("[data-seite]").forEach((el) => { el.hidden = Number(el.dataset.seite) !== nr; });
    if (nr === 5) { kontoZeigen(); sicherungZeigen(); }
    if (nr === 4) {
      $("#fertig-text").textContent = einzelplatz() ? t("fertig_text_einzelplatz", { name: S.einstellungen.anmeldename || "–" })
        + (S.taleward && !S.taleward.app ? " " + t("tw_app_laedt") : "") : t("fertig_text");
      $("#los").textContent = t(einzelplatz() ? "Taleward öffnen" : "Worker starten");
      $("#los").disabled = einzelplatz() && !(S.taleward && S.taleward.app);
    }
    if (nr === 1) {
      const name = $("#name");
      if (!name.value && S.einstellungen.name) name.value = S.einstellungen.name;
      const adr = $("#adresse");
      if (!adr.value && S.einstellungen.server) adr.value = S.einstellungen.server;
    }
    if (nr === 2) pruefungZeigen();
    if (nr === 3) installationZeigen();
    if (nr === 4) { $("#hinweis-modelle").hidden = !!(S.motor && S.motor.testmodus); reglerZeigen(); }
  }

  function dauerText(d) {
    if (!d) return "";
    return d[1] >= 120 ? t("dauer_std", { a: zahl(d[0] / 60, d[0] % 60 ? 1 : 0), b: zahl(d[1] / 60, d[1] % 60 ? 1 : 0) })
      : t("dauer_min", { a: d[0], b: d[1] });
  }
  function profilText(p) {
    if (!p) return "–";
    if (p.geraet === "cpu") return t("pw_cpu", { modell: p.modell });
    let s = t(p.ausrichten === "cpu" ? "pw_gpu_teil" : "pw_gpu", { modell: p.modell });
    if (p.grenzeMb) s += " · " + t("pw_grenze", { gb: gb(p.grenzeMb) });
    return s;
  }

  function pruefungZeigen() {
    $("#verbunden-mit").textContent = einzelplatz() ? t("pruefen_einzelplatz")
      : t("verbunden_mit", { server: S.serverName || S.einstellungen.server, name: S.einstellungen.name });
    const h = S.hardware;
    if (!h) return;
    const k = h.grafikkarte, gpu = h.grafikkarteNutzbar;
    const zeilen = [];
    if (gpu) zeilen.push(["ja", t("pr_gpu"), `${k.name} · ${gb(k.vram_mb)}`]);
    else if (k && k.vram_mb < 2000) zeilen.push(["warn", t("pr_gpu"), t("pr_gpu_klein", { name: k.name, vram: gb(k.vram_mb) })]);
    else if (k && !h.treiberOk) zeilen.push(["warn", t("pr_treiber"), t("pr_treiber_alt", { v: k.treiber, min: h.minTreiber })]);
    else zeilen.push(["warn", t("pr_gpu"), t("pr_gpu_keine_cpu")]);
    if (gpu) zeilen.push(["ja", t("pr_treiber"), k.treiber]);
    if (h.ramGb) zeilen.push([h.ramOk ? "ja" : "warn", t("pr_ram"), h.ramOk ? t("pr_ram_text", { gb: zahl(h.ramGb) }) : t("pr_ram_wenig", { gb: zahl(h.ramGb), min: h.minRamGb })]);
    zeilen.push([h.platzOk ? "ja" : "warn", t("pr_platz"), t("pr_platz_text", { frei: zahl(h.platzGb), noetig: h.platzNoetigGb })]);
    const p = h.empfehlung;
    zeilen.push([p.geraet === "cpu" ? "warn" : "ja", t("pr_arbeitsweise"), profilText(p) + ". " + t("pw_dauer", { dauer: dauerText(p.dauerMin) })]);
    const liste = $("#pruefliste");
    const stand = JSON.stringify(zeilen);
    if (liste.dataset.stand !== stand) {
      liste.innerHTML = zeilen.map(([ok]) => `<li data-ok="${ok}"><span class="zeichen">${ok === "ja" ? "✓" : "!"}</span><span class="titel"></span><span class="wert"></span></li>`).join("");
      Array.from(liste.children).forEach((li, i) => {
        li.querySelector(".titel").textContent = zeilen[i][1];
        li.querySelector(".wert").textContent = zeilen[i][2];
      });
      liste.dataset.stand = stand;
    }
    $("#pruef-meldung").hidden = gpu;
    $("#pruef-meldung").className = "meldung meldung--hinweis";
    $("#pruef-meldung").textContent = t(einzelplatz() ? "pr_cpu_einzelplatz" : "pr_cpu_angebot");
    $("#testmodus-waehlen").hidden = gpu;
    $("#prozessor-waehlen").hidden = gpu;
    $("#weiter-installieren").hidden = !gpu;
  }

  // ------------------------------------------------------------ Schieberegler Grafikspeicher
  function reglerZeigen() {
    const k = S.hardware && S.hardware.grafikkarte;
    const sichtbar = !!(k && S.hardware.grafikkarteNutzbar && S.einstellungen.geraet !== "cpu" && !S.einstellungen.testmodus);
    const maxGb = k ? Math.floor(k.vram_mb / 512) / 2 : 8;
    $$("[data-regler]").forEach((r) => {
      r.hidden = !sichtbar;
      if (!sichtbar) return;
      const ein = r.querySelector("[data-regler-eingabe]");
      if (ein.max != maxGb) ein.max = maxGb;
      r.querySelector("[data-regler-max]").textContent = zahl(maxGb, maxGb % 1 ? 1 : 0) + " GB";
      if (document.activeElement !== ein && !ein.dataset.zieht) {
        const g = S.einstellungen.vram_grenze_mb;
        ein.value = g ? Math.min(maxGb, g / 1024) : maxGb;
        reglerText(r, S.profil);
      }
    });
  }
  function reglerWertMb(ein) {
    const v = Number(ein.value);
    return v >= Number(ein.max) ? 0 : Math.round(v * 1024);
  }
  function reglerText(r, p) {
    const ein = r.querySelector("[data-regler-eingabe]");
    const v = Number(ein.value), max = Number(ein.max);
    r.querySelector("[data-regler-wert]").textContent = v >= max ? t("regler_alles") + " · " + zahl(max, max % 1 ? 1 : 0) + " GB"
      : t("regler_von", { a: zahl(v, v % 1 ? 1 : 0) + " GB", b: zahl(max, max % 1 ? 1 : 0) + " GB" });
    if (!p) return;
    const folge = r.querySelector("[data-regler-folge]");
    folge.textContent = "";
    const st = document.createElement("strong"); st.textContent = profilText(p);
    folge.append(st, document.createElement("br"), document.createTextNode(t("pw_dauer", { dauer: dauerText(p.dauerMin) })));
    if (S.dienst.neustartOffen) folge.append(document.createElement("br"), document.createTextNode(t("neustart_offen")));
  }

  function installationZeigen() {
    const inst = S.installation;
    const test = testmodus();
    $("#installieren-text").textContent = test ? t("installieren_text_test")
      : prozessorWahl ? t("installieren_text_cpu", { mb: gb(1800) })
      : t("installieren_text_windows", { mb: gb(S.system === "windows" ? 7900 : 8500) });
    if (einzelplatz() && S.system === "windows") $("#installieren-text").textContent += " " + t("inst_netz_hinweis");
    const laeuft = inst && !["fertig", "fehler", "abgebrochen"].includes(inst.phase);
    const fehler = inst && ["fehler", "abgebrochen"].includes(inst.phase);
    $("#inst-start").hidden = laeuft || fehler;
    $("#inst-lauf").hidden = !laeuft;
    $("#inst-fehler").hidden = !fehler;
    if (laeuft) {
      $("#inst-phase").textContent = t("ph_" + inst.phase);
      const p = Math.round(inst.anteil * 100);
      $("#inst-balken span").style.width = p + "%";
      $("#inst-prozent").textContent = p + " %";
      $("#inst-mb").textContent = inst.phase === "pakete" && inst.erwartetMb ? t("mb_von", { a: gb(inst.geladenMb), b: gb(inst.erwartetMb) }) : "";
      $("#inst-zeile").textContent = inst.letzteZeile || "";
    }
    if (fehler) $("#inst-fehlertext").textContent = t("f_" + inst.fehler);
  }

  function kontoZeigen() {
    const tw = S.taleward || {};
    const bereit = !!tw.kontoMoeglich;
    const z = tw.zustand || {};
    $("#konto-wartet").hidden = bereit || z.art === "fehler";
    $("#konto-anlegen").disabled = !bereit || beschaeftigt;
    if (z.art === "fehler") {
      const f = $("#konto-fehler"); f.textContent = t("tw_f_" + (z.code || "startet_nicht")); f.hidden = false;
    } else if (z.art === "abgestuerzt") {
      $("#konto-wartet").textContent = t("tw_abgestuerzt_text", { wann: wann(z.neuerVersuch) });
    } else {
      $("#konto-wartet").textContent = t("taleward_startet_noch");
    }
    // Dauert der Start ungewöhnlich lange oder klappt er nicht: das Protokoll muss erreichbar sein (sonst Sackgasse)
    const lange = z.art === "startet" && z.seit && Date.now() / 1000 - z.seit > 45;
    $("#konto-protokoll-zeile").hidden = bereit || !(lange || ["fehler", "abgestuerzt"].includes(z.art));
  }

  function talewardZeigen() {
    const karte = $("#taleward-karte");
    karte.hidden = !einzelplatz();
    if (!einzelplatz()) return;
    const tw = S.taleward || {}, z = tw.zustand || {}, art = z.art || "gestoppt";
    const upd = tw.update || {}, imUpdate = ["vorbereiten", "sichern", "tauschen", "starten", "pruefen", "zurueck"].includes(upd.phase);
    const werte = { neu: upd.neu || upd.angebot || "", alt: upd.alt || "",
                    prozent: upd.anteil != null ? Math.round(upd.anteil * 100) + " %" : "…" };
    $("#taleward-titel").textContent = imUpdate && upd.phase !== "vorbereiten" ? t("tw_upd") : t("tw_" + art);
    $("#taleward-text").textContent = imUpdate && upd.phase !== "vorbereiten" ? t("tw_upd_" + upd.phase, werte)
      : art === "fehler" ? t("tw_f_" + (z.code || "startet_nicht"))
      : art === "laeuft" && tw.appFehlt ? t("tw_app_fehlt")
      : art === "laeuft" && !tw.app ? t("tw_app_laedt")
      : art === "laeuft" ? t("tw_laeuft_text", { name: S.einstellungen.anmeldename || "–" })
      : art === "abgestuerzt" ? t("tw_abgestuerzt_text", { wann: wann(z.neuerVersuch) }) : t("tw_" + art + "_text");
    $("#taleward-oeffnen").disabled = !tw.app;
    $("#taleward-browser").disabled = !tw.app;
    $("#taleward-neustart").hidden = art !== "fehler" || imUpdate;
    const um = $("#taleward-update");
    const hinweis_upd = upd.phase === "vorbereiten" ? t("tw_upd_vorbereiten", werte)
      : upd.phase === "zurueckgenommen" ? t("tw_upd_zurueckgenommen", werte)
      : upd.phase === "fehler" ? t("tw_upd_fehler", werte)
      : upd.phase === "verschoben" ? t("tw_upd_verschoben", werte)
      : upd.phase === "fertig" ? t("tw_upd_fertig", werte)
      : !imUpdate && upd.angebot ? t(S.einstellungen.auto_update && upd.angebot !== upd.gescheitert ? "tw_upd_angebot_auto" : "tw_upd_angebot", werte) : "";
    um.hidden = !hinweis_upd;
    um.textContent = hinweis_upd;
    um.className = "meldung" + (upd.phase === "zurueckgenommen" || upd.phase === "fehler" ? " meldung--hinweis" : "");
    $("#taleward-aktualisieren").hidden = imUpdate || !upd.angebot;
    const netz = tw.netz || {};
    const oeffentlich = (netz.profile || []).includes("Public") && !(netz.profile || []).includes("Private");
    const ohneRegel = netz.noetig && netz.regel === false;
    const hinweis = $("#netz-hinweis");
    hinweis.hidden = !(oeffentlich || ohneRegel);
    hinweis.textContent = oeffentlich ? t("netz_oeffentlich") : ohneRegel ? t("netz_ohne_regel") : "";
    $("#netz-freigeben").hidden = !ohneRegel;
  }

  function statusZeigen() {
    talewardZeigen();
    const d = S.dienst, z = d.zustand, art = z.art;
    const karte = $("#statuskarte");
    karte.dataset.art = art;
    const serverText = einzelplatz() ? t("taleward_hier") : (S.serverName || S.einstellungen.server);
    let titel = t("st_" + art), text = t("st_" + art + "_text", { server: serverText, wann: wann(z.neuerVersuch) });
    let fortschritt = null;
    if (art === "arbeitet") {
      text = t("st_" + (z.typ || "transcribe"));
      if (z.taetigkeit) text += " – " + z.taetigkeit.replace(/ …$/, "");
      fortschritt = z.p || 0;
    } else if (art === "startet" && z.modelle) {
      text = t("st_modelle_text", { mb: gb(z.modelleMb || 0) });
    } else if (art === "fehler" && z.code === "modell") {
      titel = t(einzelplatz() ? "st_modell_hier" : "st_modell");
      text = t(einzelplatz() ? "st_modell_hier_text" : "st_modell_text", { wann: wann(z.neuerVersuch) });
    } else if (art === "fehler") {
      text = z.text || t("f_" + (z.code || "einrichtung"));
    }
    if (d.info && d.info.testmodus && art === "warte") titel += " · " + t("testmodus_aktiv");
    $("#status-titel").textContent = titel;
    $("#status-text").textContent = text;
    $("#status-fortschritt").hidden = fortschritt === null;
    if (fortschritt !== null) {
      $("#status-fortschritt .balken span").style.width = Math.round(fortschritt * 100) + "%";
      $("#status-prozent").textContent = Math.round(fortschritt * 100) + " %";
      $("#status-dauer").textContent = t("laufzeit", { min: Math.max(1, Math.round((Date.now() / 1000 - z.seit) / 60)) });
    }
    knoepfe(art);
    $("#z-heute").textContent = zahl(d.statistik.heute);
    $("#z-gesamt").textContent = zahl(d.statistik.gesamt);
    $("#i-server").textContent = einzelplatz() ? t("taleward_hier") : (S.serverName ? S.serverName + " · " : "") + S.einstellungen.server;
    $("#i-name").textContent = S.einstellungen.name;
    const k = S.hardware && S.hardware.grafikkarte;
    $("#i-gpu").textContent = d.info.gpu || (k ? `${k.name} · ${gb(k.vram_mb)}` : "–");
    $("#i-modell").textContent = (d.info.testmodus || S.einstellungen.testmodus) ? t("testmodus_aktiv") : profilText(S.profil);
    const m = d.statistik.messung;
    let mt = t("noch_keine_messung");
    if (m && m.audio) {
      const dauer = (s) => s >= 3600 ? zahl(s / 3600, 1) + " h" : Math.max(1, Math.round(s / 60)) + " min";
      mt = t("messung_text", { audio: dauer(m.audio), rechen: dauer(m.rechen) });
      if (m.peakMb) mt += " · " + t("messung_peak", { gb: gb(m.peakMb) });
    }
    $("#i-messung").textContent = mt;
    const o = S.ollama || {};
    $("#i-recaps").textContent = o.an && o.sprachmodell ? t("recaps_lokal", { modell: o.sprachmodell }) : t("recaps_server");
    $("#i-fassung").textContent = t("fassung_text", { app: S.version, motor: S.motor ? S.motor.fassung || S.motor.ref : "–" });
    // Hinweise
    const inst = S.installation;
    const upd = $("#motor-update");
    const laeuft = inst && S.motor && !["fertig", "fehler", "abgebrochen"].includes(inst.phase);
    upd.hidden = !laeuft;
    if (laeuft) upd.textContent = t("st_update") + " – " + t("st_update_text", { fassung: inst.fassung, prozent: Math.round(inst.anteil * 100) + " %" });
    const au = $("#app-update"), u = S.appUpdate;
    au.hidden = !u;
    if (u) {
      const schluessel = [u.version, u.phase, Math.round(u.anteil * 20), u.fehler, S.einstellungen.auto_update, sprache].join("|");
      if (au.dataset.s !== schluessel) {
        au.dataset.s = schluessel;
        au.className = "meldung " + (u.phase === "fehler" ? "meldung--fehler" : "meldung--hinweis");
        au.innerHTML = "";
        const st = document.createElement("strong");
        st.textContent = t("app_update", { version: u.version });
        const text = document.createElement("span");
        text.textContent = u.phase === "laden" ? t("app_update_laden", { prozent: Math.round(u.anteil * 100) + " %" })
          : u.phase === "installieren" || u.phase === "fertig" ? t("app_update_installieren")
          : u.phase === "fehler" ? t("app_update_fehler", { fehler: u.fehler })
          : !u.moeglich ? t("app_update_hand")
          : S.einstellungen.auto_update ? t("app_update_auto") : "";
        au.append(st, text);
        if (u.moeglich && (u.phase === "bereit" || u.phase === "fehler")) {
          const b = document.createElement("button"); b.className = "tw-btn tw-btn--secondary";
          b.textContent = t("Jetzt aktualisieren");
          b.onclick = () => { riskant("app_aktualisieren"); setTimeout(neuLaden, 300); };
          au.append(b);
        }
        if (u.notizen) {
          const d = document.createElement("details"), s = document.createElement("summary"), p = document.createElement("p");
          s.textContent = t("Was ist neu?"); p.textContent = u.notizen; p.className = "notizen";
          d.append(s, p); au.append(d);
        }
      }
    }
  }

  function knoepfe(art) {
    const ziel = $("#status-knoepfe");
    const liste = [];
    if (["warte", "arbeitet", "getrennt", "startet", "server_pausiert"].includes(art)) liste.push(["Pausieren", "secondary", () => api.pausieren(true)]);
    if (art === "pausiert") liste.push(["Fortsetzen", "primary", () => api.pausieren(false)]);
    if (art === "gestoppt") liste.push(["Starten", "primary", () => api.starten()]);
    if (art === "fehler" && S.dienst.zustand.code === "modell" && einzelplatz()) {
      // Stimmen erkennen einrichten: genau die Stelle in der Verwaltung, mit Anleitung (Quelle der Wahrheit für den Weg)
      liste.push(["Stimmen erkennen einrichten", "primary", () => api.taleward_oeffnen()]);
      liste.push(["Protokoll ansehen", "quiet", () => zeigeReiter("protokoll")]);
    } else if (art === "fehler" || art === "abgestuerzt") {
      if (["motor_fehlt", "motor_kaputt"].includes(S.dienst.zustand.code || "")) liste.push(["Installieren", "primary", () => { schritt = 2; }]);
      else liste.push(["Erneut versuchen", "primary", () => api.starten()]);
      liste.push(["Protokoll ansehen", "quiet", () => zeigeReiter("protokoll")]);
    }
    if (art === "abgelehnt") liste.push(["Neu koppeln", "primary", () => riskant("entkoppeln").then(neuLaden)]);
    const schluessel = art + "|" + liste.map((x) => x[0]).join(",") + "|" + sprache;
    if (ziel.dataset.s === schluessel) return;
    ziel.dataset.s = schluessel;
    ziel.innerHTML = "";
    liste.forEach(([text, stil, fn]) => {
      const b = document.createElement("button");
      b.className = "tw-btn tw-btn--" + stil; b.textContent = t(text);
      b.onclick = () => { fn(); setTimeout(neuLaden, 150); };
      ziel.append(b);
    });
    ziel.hidden = liste.length === 0;
  }

  function einstellungenZeigen() {
    $$("[data-einstellung]").forEach((el) => {
      if (el === document.activeElement) return;
      const n = el.dataset.einstellung;
      const wert = n === "autostart" ? S.autostart : S.einstellungen[n];
      if (el.type === "checkbox") el.checked = !!wert; else el.value = wert || (n === "modell" ? "auto" : "");
    });
    $("#fuss-version").textContent = "Taleward " + S.version;
    $("#neu-koppeln").hidden = einzelplatz();
    $("#verwaltung").hidden = !einzelplatz();
    $("#betrieb-jetzt").textContent = einzelplatz() ? t("betrieb_jetzt_einzelplatz")
      : t("betrieb_jetzt_verbunden", { server: serverName() });
    $("#sicherung-karte").hidden = !einzelplatz();
    sicherungZeigen();
    const k = S.hardware && S.hardware.grafikkarte;
    $("#zeile-prozessor").hidden = !(k && S.hardware.grafikkarteNutzbar) || S.einstellungen.testmodus;
    $("#zeile-recaps").hidden = !!S.einstellungen.testmodus;
    ollamaZeigen();
    if (document.activeElement !== $("#nur-prozessor")) $("#nur-prozessor").checked = S.einstellungen.geraet === "cpu";
    reglerZeigen();
  }

  const serverName = () => (S && (S.serverName || S.einstellungen.server)) || t("dem Server");

  // ------------------------------------------------------------ Sicherung
  function sicherungText() {
    const s = S && S.taleward && S.taleward.sicherung;
    if (!s || !s.phase) return null;
    if (s.phase === "speichern") return ["", t("sich_laeuft_speichern")];
    if (s.phase === "zurueckspielen") return ["", t("sich_laeuft_zurueck")];
    if (s.phase === "fertig") return ["", t(s.art === "speichern" ? "sich_fertig_speichern" : "sich_fertig_zurueck", { datei: s.datei })];
    return ["meldung--fehler", t("sich_f_" + s.fehler) !== "sich_f_" + s.fehler ? t("sich_f_" + s.fehler) : t("sich_f_unbekannt")];
  }
  let sicherungZuletzt = "";
  function sicherungZeigen() {
    const x = sicherungText();
    const s = S.taleward && S.taleward.sicherung;
    const kennung = s ? s.phase + "|" + s.art + "|" + s.datei : "";
    if (kennung !== sicherungZuletzt && sicherungZuletzt && s && ["fertig", "fehler"].includes(s.phase)) toast(x[1]);
    sicherungZuletzt = kennung;
    ["#sicherung-stand", "#konto-sicherung-stand"].forEach((id) => {
      const el = $(id);
      el.hidden = !x;
      if (x) { el.className = "meldung " + x[0]; el.textContent = x[1]; }
    });
    const laeuft = !!(S.taleward && S.taleward.sicherung && ["speichern", "zurueckspielen"].includes(S.taleward.sicherung.phase));
    ["#sicherung-speichern", "#sicherung-zurueck", "#konto-sicherung"].forEach((id) => { $(id).disabled = laeuft; });
  }

  // ------------------------------------------------------------ Rückfragen
  let dialogArt = null;
  function dialogOeffnen(art, titel, okText, okStil) {
    dialogArt = art;
    $("#dialog-titel").textContent = titel;
    $$("[data-dialog]").forEach((el) => { el.hidden = el.dataset.dialog !== art; });
    $("#dialog-fehler").hidden = true;
    const ok = $("#dialog-ok");
    ok.textContent = okText; ok.className = "tw-btn tw-btn--" + (okStil || "primary"); ok.disabled = false;
    const d = $("#dialog");
    if (!d.open) d.showModal();
    const erstes = d.querySelector("[data-dialog=\"" + art + "\"] input, [data-dialog=\"" + art + "\"] textarea");
    (erstes || ok).focus();
  }
  function dialogFehler(text) { const f = $("#dialog-fehler"); f.textContent = text; f.hidden = false; }
  function dialogZu() { const d = $("#dialog"); if (d.open) d.close(); }
  function codeFormen(text) {
    // Eingefügt mit Text drumherum: nur den Code nehmen – den direkt nach „Code“, sonst den letzten (wie der Server-Teil
    // des Programms). Zeichen wie in den Codes: ohne 0/O und 1/I, darum trifft z. B. ein Datum nie.
    const roh = String(text || "");
    const z = "[A-HJ-NP-Za-hj-np-z2-9]";
    const suche = (muster) => [...roh.matchAll(new RegExp("(?<![A-Za-z0-9])(" + z + "{4})" + muster + "(" + z + "{4})(?![A-Za-z0-9])", "g"))];
    let treffer = suche("\\s*-\\s*");
    if (!treffer.length && /\s/.test(roh.trim())) treffer = suche("");
    if (treffer.length) {
      const m = treffer.find((t) => /code\W*$/i.test(roh.slice(0, t.index))) || treffer[treffer.length - 1];
      return (m[1] + "-" + m[2]).toUpperCase();
    }
    if (roh.trim().length > 12) return "";  // längerer Text ohne Code: nichts übernehmen
    // Beim Tippen: Buchstaben und Ziffern, Strich nach dem vierten Zeichen (auch selbst getippt)
    const v = roh.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);
    if (v.length > 4) return v.slice(0, 4) + "-" + v.slice(4);
    return v.length === 4 && /-\s*$/.test(roh) ? v + "-" : v;
  }

  function feldfehler(praefix, code) {
    return t(praefix + code) !== praefix + code ? t(praefix + code) : t(praefix + "unbekannt");
  }
  function betriebFragen() {
    const zu = einzelplatz() ? "verbunden" : "einzelplatz";
    if (zu === "einzelplatz") {
      $("#betrieb-frage").textContent = S.einstellungen.gekoppelt ? t("betrieb_zu_einzelplatz", { server: serverName() }) : t("betrieb_zu_einzelplatz_ohne");
      $("#betrieb-folge").textContent = S.einstellungen.gekoppelt ? t("betrieb_zu_einzelplatz_folge", { server: serverName() }) : "";
    } else {
      const e = S.einstellungen;
      $("#betrieb-frage").textContent = e.verbundenGemerkt
        ? t("betrieb_zu_verbunden_gemerkt", { server: e.verbunden_name || e.verbunden_server })
        : t("betrieb_zu_verbunden");
      $("#betrieb-folge").textContent = t("betrieb_zu_verbunden_folge");
    }
    const netz = (S.taleward && S.taleward.netz) || {};
    $("#betrieb-regel-zeile").hidden = !(zu === "verbunden" && netz.noetig && netz.vorhanden);
    $("#betrieb-regel").checked = true;
    dialogOeffnen("betrieb", t(zu === "einzelplatz" ? "betrieb_titel_einzelplatz" : "betrieb_titel_verbunden"), t("Wechseln"));
    $("#dialog-ok").dataset.ziel = zu;
  }
  function zurueckFragen() {
    const s = S.taleward && S.taleward.sicherung;
    $("#zurueck-datei").textContent = (s && s.gewaehlt) || t("noch_keine_datei");
    dialogOeffnen("zurueck", t("Sicherung zurückspielen"), t("Zurückspielen"));
  }
  async function dialogBestaetigen() {
    const ok = $("#dialog-ok");
    if (ok.disabled) return;
    ok.disabled = true;
    try {
      if (dialogArt === "betrieb") {
        const regelWeg = !$("#betrieb-regel-zeile").hidden && $("#betrieb-regel").checked;
        const r = await riskant("betrieb_wechseln", ok.dataset.ziel, regelWeg);
        if (!r.ok) return dialogFehler(feldfehler("betrieb_f_", r.fehler));
        dialogZu(); fertigZeigen = false; schritt = null; reiter = "status";
        setTimeout(neuLaden, 300);
      } else if (dialogArt === "speichern") {
        const r = await api.sicherung_speichern($("#sich-passwort").value, $("#sich-passwort2").value);
        if (!r.ok) { if (r.fehler !== "abgebrochen") dialogFehler(feldfehler("sich_f_", r.fehler)); return; }
        dialogZu(); neuLaden();
      } else if (dialogArt === "zurueck") {
        const r = await riskant("sicherung_zurueckspielen", $("#zurueck-passwort").value);
        if (!r.ok) return dialogFehler(feldfehler("sich_f_", r.fehler));
        dialogZu(); neuLaden();
      } else if (dialogArt === "hilfe") {
        const r = await api.hilfe_speichern();
        if (!r.ok) { if (r.fehler !== "abgebrochen") dialogFehler(t("hilfe_f")); return; }
        dialogZu(); toast(t("hilfe_gespeichert", { datei: r.datei }));
      }
    } finally { ok.disabled = false; }
  }

  function ollamaZeigen() {
    const o = S.ollama || {};
    const inst = o.installation;
    let text = "";
    if (inst && inst.phase === "laden") text = t("ollama_laedt", { p: Math.round(inst.anteil * 100) + " %" });
    else if (inst && inst.phase === "entpacken") text = t("ollama_entpackt");
    else if (inst && inst.phase === "fehler") text = t("ollama_fehler", { text: inst.fehler });
    else if (o.an && o.fremd) text = t("ollama_fremd");
    else if (o.an && o.laeuft) text = o.sprachmodell ? t("ollama_bereit_modell", { modell: o.sprachmodell }) : t("ollama_bereit");
    else if (o.an && o.fehler) text = t("ollama_startfehler", { text: o.fehler });
    else if (o.an && o.installiert) text = t("ollama_startet");
    else if (!o.an && o.moeglich === false) text = t("ollama_zu_schwach");
    $("#ollama-stand").textContent = text;
    $("#ollama-stand").hidden = !text;
    const schalter = document.querySelector('[data-einstellung="recaps_lokal"]');
    if (schalter) schalter.disabled = o.moeglich === false && !o.an;
    $("#ollama-entfernen").hidden = !o.installiert;
  }

  async function speicherLaden() {
    const s = await api.speicherbelegung();
    let text = t("speicher_text", { motor: gb(s.motorMb), modelle: gb(s.modelleMb) });
    if (s.ollamaMb) text += " " + t("speicher_ollama", { mb: gb(s.ollamaMb) });
    $("#speicher").textContent = text;
  }

  async function logLaden(ganz) {
    if (reiter !== "protokoll") return;
    const zeilen = await api.protokoll();
    const text = zeilen.join("\n");
    if (text === letzterLog && !ganz) return;
    letzterLog = text;
    const el = $("#log");
    const unten = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.textContent = text;
    if (unten || ganz) el.scrollTop = el.scrollHeight;
  }

  // Folgenreiches braucht den Prüfwert aus dem Zustand (gilt für einen Aufruf)
  async function riskant(name, ...args) {
    if (!S) await neuLaden();
    let r = await api[name](S.pruefwert, ...args);
    if (r && r.fehler === "pruefwert") { await neuLaden(); r = await api[name](S.pruefwert, ...args); }
    return r;
  }

  async function neuLaden() {
    try { S = await api.zustand(); } catch (e) { return; }
    darstellen();
  }

  // ------------------------------------------------------------ Aktionen
  function binden() {
    $$(".reiter button").forEach((b) => { b.onclick = () => zeigeReiter(b.dataset.reiter); });
    $$("[data-zeige]").forEach((b) => { b.onclick = () => zeigeReiter(b.dataset.zeige); });
    $$("[data-zurueck]").forEach((b) => { b.onclick = () => zeigeReiter("status"); });

    const adresse = $("#adresse");
    adresse.addEventListener("input", () => {
      const http = /^http:\/\//i.test(adresse.value.trim()) && !/^http:\/\/(localhost|127\.0\.0\.1)/i.test(adresse.value.trim());
      $("#unsicher-zeile").hidden = !http;
    });
    $("#code").addEventListener("input", (e) => { e.target.value = codeFormen(e.target.value); });
    $("#verbinden").onclick = async () => {
      if (beschaeftigt) return;
      const fehler = $("#koppeln-fehler");
      fehler.hidden = true;
      if ($("#code").value.replace("-", "").length < 8) { fehler.textContent = t("f_code_fehlt"); fehler.hidden = false; return; }
      beschaeftigt = true; $("#verbinden").disabled = true;
      try {
        const pr = await api.server_pruefen(adresse.value);
        if (!pr.ok) { fehler.textContent = t("f_" + pr.fehler); fehler.hidden = false; return; }
        adresse.value = pr.adresse;
        const r = await riskant("koppeln", pr.adresse, $("#code").value, $("#name").value, $("#unsicher").checked);
        if (!r.ok) {
          fehler.textContent = r.text || t("f_" + r.fehler);
          if (r.fehler === "unverschluesselt") $("#unsicher-zeile").hidden = false;
          fehler.hidden = false; return;
        }
        $("#code").value = "";
        await api.hardware_pruefen();
        await neuLaden();
      } finally { beschaeftigt = false; $("#verbinden").disabled = false; }
    };
    $("#wahl-einzelplatz").onclick = async () => { await api.betrieb_waehlen("einzelplatz"); await api.hardware_pruefen(); neuLaden(); };
    $("#wahl-verbunden").onclick = async () => { await api.betrieb_waehlen("verbunden"); neuLaden(); };
    $("#konto-anlegen").onclick = async () => {
      if (beschaeftigt) return;
      const fehler = $("#konto-fehler");
      fehler.hidden = true;
      beschaeftigt = true; $("#konto-anlegen").disabled = true;
      try {
        const r = await api.konto_anlegen($("#konto-name").value, $("#konto-passwort").value, $("#konto-passwort2").value);
        if (!r.ok) { fehler.textContent = t("konto_f_" + r.fehler) !== "konto_f_" + r.fehler ? t("konto_f_" + r.fehler) : t("konto_f_unbekannt"); fehler.hidden = false; return; }
        $("#konto-passwort").value = ""; $("#konto-passwort2").value = "";
        fertigZeigen = true;
        await neuLaden();
      } finally { beschaeftigt = false; }
    };
    $("#taleward-oeffnen").onclick = () => api.taleward_oeffnen();
    $("#taleward-browser").onclick = () => api.im_browser_oeffnen();
    $("#taleward-neustart").onclick = () => { riskant("taleward_neu_starten"); setTimeout(neuLaden, 300); };
    $("#taleward-aktualisieren").onclick = async () => {
      const r = await riskant("taleward_aktualisieren");
      if (!r.ok) toast(t(r.grund === "aktuell" ? "tw_upd_aktuell" : "tw_upd_spaeter"));
      neuLaden();
    };
    $("#netz-freigeben").onclick = async () => {
      const r = await riskant("netz_freigeben");
      if (!r.ok) toast(t("netz_abgelehnt"));
      neuLaden();
    };
    $("#nochmal-pruefen").onclick = async () => { await api.hardware_pruefen(); neuLaden(); };
    $("#weiter-installieren").onclick = () => { schritt = 3; testWahl = false; prozessorWahl = false; darstellen(); };
    $("#prozessor-waehlen").onclick = () => { schritt = 3; testWahl = false; prozessorWahl = true; darstellen(); };
    $("#testmodus-waehlen").onclick = () => { schritt = 3; testWahl = true; prozessorWahl = false; darstellen(); };
    $("#installieren").onclick = async () => { await riskant("installieren", testmodus(), prozessorWahl); fertigZeigen = true; neuLaden(); };
    $("#nur-prozessor").addEventListener("change", async (e) => {
      await api.einstellung("geraet", e.target.checked ? "cpu" : "auto"); neuLaden();
    });
    $$("[data-regler]").forEach((r) => {
      const ein = r.querySelector("[data-regler-eingabe]");
      let zaehler = 0;
      ein.addEventListener("input", async () => {
        ein.dataset.zieht = "1";
        const nr = ++zaehler;
        reglerText(r, null);
        const p = await api.profil_vorschau(reglerWertMb(ein));
        if (nr === zaehler) reglerText(r, p);
      });
      ein.addEventListener("change", async () => {
        await api.einstellung("vram_grenze_mb", reglerWertMb(ein));
        delete ein.dataset.zieht;
        await neuLaden();
      });
    });
    $("#inst-nochmal").onclick = async () => { await riskant("installieren", testmodus()); neuLaden(); };
    $("#inst-abbrechen").onclick = () => api.installation_abbrechen();
    $("#los").onclick = async () => {
      fertigZeigen = false; schritt = null;
      if (einzelplatz()) await api.taleward_oeffnen(); else await api.starten();
      reiter = "status"; neuLaden();
    };

    $$("[data-einstellung]").forEach((el) => {
      el.addEventListener("change", async () => {
        const wert = el.type === "checkbox" ? el.checked : el.value;
        const r = await api.einstellung(el.dataset.einstellung, wert);
        if (r && !r.ok && r.fehler) toast(r.fehler);
        if (el.dataset.einstellung === "sprache") { S.einstellungen.sprache = wert; spracheWaehlen(); }
        neuLaden();
      });
    });
    $("#ordner").onclick = () => api.ordner_oeffnen();
    $("#log-ordner").onclick = () => api.ordner_oeffnen();
    $("#verwaltung").onclick = () => api.taleward_oeffnen();
    $("#ollama-entfernen").onclick = async () => { if (confirm(t("bestaetigen_ollama"))) { await riskant("ollama_entfernen"); speicherLaden(); neuLaden(); } };
    $("#neu-koppeln").onclick = async () => { if (confirm(t("bestaetigen_koppeln", { server: serverName() }))) { await riskant("entkoppeln"); reiter = "status"; neuLaden(); } };
    $("#neu-installieren").onclick = async () => { await riskant("installieren", !!S.einstellungen.testmodus); reiter = "status"; zeigeReiter("status"); neuLaden(); };
    $("#entfernen").onclick = async () => { if (confirm(t("bestaetigen_entfernen"))) { await riskant("ki_entfernen", true); schritt = 2; reiter = "status"; neuLaden(); } };
    $("#beenden").onclick = () => riskant("beenden");
    $("#betrieb-wechseln").onclick = betriebFragen;
    $("#sicherung-speichern").onclick = () => dialogOeffnen("speichern", t("Sicherung speichern"), t("Speichern …"));
    $("#sicherung-zurueck").onclick = zurueckFragen;
    $("#konto-sicherung").onclick = zurueckFragen;
    $("#zurueck-waehlen").onclick = async () => {
      const r = await api.sicherung_waehlen();
      if (r.ok) $("#zurueck-datei").textContent = r.datei;
    };
    $("#hilfe-erstellen").onclick = async () => {
      const r = await api.hilfe_vorschau();
      $("#hilfe-vorschau").value = r.text;
      dialogOeffnen("hilfe", t("Hilfe-Datei"), t("Speichern …"));
    };
    $("#dialog-ok").onclick = dialogBestaetigen;
    $("#dialog-abbrechen").onclick = dialogZu;
    $("#dialog").addEventListener("close", () => {
      ["#sich-passwort", "#sich-passwort2", "#zurueck-passwort"].forEach((id) => { $(id).value = ""; });
      $("#hilfe-vorschau").value = ""; dialogArt = null;
    });
    $$("#dialog input").forEach((el) => el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); dialogBestaetigen(); } }));
    $("#log-kopieren").onclick = () => {
      const text = $("#log").textContent;
      const fertig = () => toast(t("kopiert"));
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(fertig, alt); else alt();
      function alt() {
        const ta = document.createElement("textarea"); ta.value = text; document.body.append(ta); ta.select();
        document.execCommand("copy"); ta.remove(); fertig();
      }
    };
  }

  // ------------------------------------------------------------ Start
  async function start() {
    api = window.TalewardBruecke.api;
    uebersetzen();
    binden();
    await neuLaden();
    if (S && S.einstellungen.gekoppelt && !S.motor && !S.installation) schritt = 2;
    zeigeReiter(reiter);
    setInterval(neuLaden, 700);
    setInterval(() => logLaden(false), 2000);
  }
  window.TalewardBruecke.bereit().then(start);
})();
