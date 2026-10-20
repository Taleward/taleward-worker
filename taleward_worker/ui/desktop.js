/* Taleward Desktop – ein Fenster: Leiste links (Zustand, Bereiche), rechts die App im Rahmen oder die Bereiche des
   Programms. Daten über die Brücke (bruecke.js); die App und die Verwaltung laufen in Rahmen ohne Brücke. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const APP = "http://localhost:8000/app/";
  const MINDEST_SERVER = [0, 4, 112];
  const WEBSITE = "https://taleward.org/";
  const DISCORD = "https://discord.gg/TtJR9FcMbX";
  const ERINNERN_NACH_S = 14 * 24 * 3600;
  let api = null, S = null, V = null, sprache = "de", bereich = "kampagnen", appZiel = "#/", letzterLog = "";
  let adresse = null;

  // ------------------------------------------------------------ Texte
  function t(k, v) {
    const d = window.DTEXTE, w = window.TEXTE;
    let s = [d[sprache], w[sprache], d.de, w.de].map((x) => x && x[k]).find((x) => x !== undefined);
    if (s === undefined) s = k;
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
    if (neu !== sprache) { sprache = neu; uebersetzen(); darstellen(); }
  }
  const ort = () => (sprache === "de" ? "de-DE" : "en-GB");
  const zahl = (n, st = 0) => Number(n).toLocaleString(ort(), { maximumFractionDigits: st, minimumFractionDigits: st });
  const gb = (mb) => (mb >= 1024 ? zahl(mb / 1024, 1) + " GB" : zahl(mb) + " MB");
  const datum = (iso) => new Date(iso).toLocaleDateString(ort(), { day: "numeric", month: "short", year: "numeric" });
  const uhrzeit = (s) => new Date(s * 1000).toLocaleString(ort(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
  function dauer(s) {
    if (s == null) return "–";
    if (s >= 3600) return zahl(s / 3600, 1) + (sprache === "de" ? " Std." : " h");
    return Math.max(1, Math.round(s / 60)) + " min";
  }
  function vor(iso) {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return t("d_gerade_eben");
    if (s < 3600) return t("d_vor_min", { n: Math.round(s / 60) });
    if (s < 86400) return t("d_vor_std", { n: Math.round(s / 3600) });
    if (s < 2 * 86400) return t("d_gestern");
    return t("d_vor_tagen", { n: Math.floor(s / 86400) });
  }
  function toast(text) {
    const el = $("#toast"); el.textContent = text; el.hidden = false;
    clearTimeout(toast.z); toast.z = setTimeout(() => { el.hidden = true; }, 3600);
  }
  function fassungMindestens(v, mindest) {
    const teile = String(v || "").split(".").map((x) => parseInt(x, 10) || 0);
    for (let i = 0; i < mindest.length; i++) {
      if ((teile[i] || 0) !== mindest[i]) return (teile[i] || 0) > mindest[i];
    }
    return true;
  }
  function knopf(text, stil, fn) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "tw-btn tw-btn--" + stil; b.textContent = text;
    b.onclick = async () => { b.disabled = true; try { await fn(); } finally { b.disabled = false; neuLaden(); } };
    return b;
  }
  function knoepfeSetzen(ziel, liste) {
    const schluessel = liste.map((x) => x[0] + x[1]).join("|") + sprache;
    if (ziel.dataset.s === schluessel) return;
    ziel.dataset.s = schluessel;
    ziel.replaceChildren(...liste.map(([text, stil, fn]) => knopf(text, stil, fn)));
  }

  // Folgenreiches braucht den Prüfwert aus dem Zustand (gilt für einen Aufruf)
  async function riskant(name, ...args) {
    if (!S) await neuLaden();
    let r = await api[name](S.pruefwert, ...args);
    if (r && r.fehler === "pruefwert") { await neuLaden(); r = await api[name](S.pruefwert, ...args); }
    return r;
  }

  // ------------------------------------------------------------ Bereiche
  const RAHMEN_APP = ["kampagnen", "schirm"];
  function bereichZeigen(name, vonSelbst) {
    const vorher = bereich;
    bereich = name;
    $$("[data-bereich]").forEach((b) => {
      const aktiv = b.dataset.bereich === name || (name === "verwaltung" && b.dataset.bereich === "einstellungen");
      if (aktiv) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    const ansicht = RAHMEN_APP.includes(name) ? "app" : name;
    $$("[data-ansicht]").forEach((el) => { el.hidden = el.dataset.ansicht !== ansicht; });
    if (name === "schirm") appNavigieren("#/schirm");
    else if (name === "kampagnen" && (vorher === "schirm" || vorher === "kampagnen") && !vonSelbst) appNavigieren("#/");
    if (name === "verwaltung") verwaltungLaden();
    if (name === "hilfe") logLaden(true);
    if (name === "einstellungen") speicherLaden();
    darstellen();
    if (!vonSelbst && !RAHMEN_APP.includes(name)) { const h = $("[data-ansicht=\"" + ansicht + "\"] h1"); if (h) { h.tabIndex = -1; h.focus(); } }
  }
  window.talewardBereich = (name) => bereichZeigen(name, true);

  function appNavigieren(ziel) {
    appZiel = ziel;
    const f = $("#rahmen-app");
    if (!f.getAttribute("src")) return; // wird gesetzt, sobald die App bereitsteht (appZeigen)
    try { f.contentWindow.location.replace(APP + ziel); } catch (e) { f.src = APP + ziel; }
  }
  function appZeigen() {
    const tw = S.taleward || {}, z = (tw.zustand || {}).art;
    const hinweis = $("#app-hinweis"), f = $("#rahmen-app");
    let titel = "", text = "", update = false, browser = false;
    if (S.serverVersion && !fassungMindestens(S.serverVersion, MINDEST_SERVER)) {
      titel = t("d_app_alt"); text = t("d_app_alt_text"); update = true; browser = !!tw.app;
    } else if (z === "fehler") {
      titel = t("d_app_fehler"); text = t("d_app_fehler_text");
    } else if (!tw.app || !S.serverVersion) {
      titel = t("d_app_startet"); text = tw.appFehlt ? t("d_app_fehlt_text") : t("d_app_startet_text");
    }
    const zeigen = !!titel && !f.getAttribute("src");
    hinweis.hidden = !zeigen;
    if (zeigen) {
      $("#app-hinweis-titel").textContent = titel; $("#app-hinweis-text").textContent = text;
      $("#app-hinweis-update").hidden = !update; $("#app-hinweis-browser").hidden = !browser;
    }
    // Einmal geladen, bleibt die App, wie sie ist – auch wenn Taleward kurz neu startet (eine laufende Aufnahme im
    // Rahmen darf nie durch Neuladen abbrechen)
    if (!titel && !f.getAttribute("src")) f.src = APP + appZiel;
  }
  async function verwaltungLaden() {
    const f = $("#rahmen-verwaltung");
    const seite = verwaltungLaden.seite || "";
    verwaltungLaden.seite = "";
    if (f.getAttribute("src") && !seite) return;
    const r = await api.verwaltung_adresse(seite);
    if (r && r.ok) f.src = r.adresse;
  }

  // ------------------------------------------------------------ Zustand (Leiste und Übersicht)
  function schrittText(lauf, typ) {
    const nr = lauf && lauf.chapterNumber;
    const schritt = (lauf && lauf.step) || ({ transcribe: "transcribing", summarize: "summarizing", revise: "revising",
      document: "document", voice_enroll: "voice" })[typ] || "transcribing";
    if (lauf && !lauf.own) return t("d_z_fremd");
    if (schritt === "document") return t("d_z_unterlage");
    if (schritt === "voice") return t("d_z_stimme");
    const k = schritt === "summarizing" ? "d_z_kapitel" : schritt === "revising" ? "d_z_ueberarbeitet" : "d_z_abschrift";
    return nr ? t(k, { n: nr }) : t(k + "_ohne");
  }
  function restText(z) {
    const p = z.p || 0, seit = z.seit ? Date.now() / 1000 - z.seit : 0;
    if (p < 0.05 || seit < 60) return seit >= 60 ? t("d_z_laeuft_seit", { min: Math.round(seit / 60) }) : "";
    const rest = seit * (1 - p) / p;
    if (rest >= 5400) return t("d_z_rest_std", { std: zahl(rest / 3600, 1) });
    const min = rest > 15 * 60 ? Math.round(rest / 300) * 5 : Math.max(1, Math.round(rest / 60));
    return t("d_z_rest", { min });
  }
  function netzStand() {
    const n = (S.taleward && S.taleward.netz) || {};
    const profile = n.profile || [];
    return { noetig: !!n.noetig, oeffentlich: profile.includes("Public") && !profile.includes("Private"),
             ohneRegel: !!n.noetig && n.regel === false, bekannt: n.regel !== undefined };
  }
  function zustand() {
    const tw = S.taleward || {}, z = tw.zustand || {}, d = S.dienst.zustand || {}, upd = tw.update || {};
    const imUpdate = ["sichern", "tauschen", "starten", "pruefen", "zurueck"].includes(upd.phase);
    const inst = S.installation, instLaeuft = inst && !["fertig", "fehler", "abgebrochen"].includes(inst.phase);
    if (imUpdate) return { art: "arbeit", titel: t("d_z_update"), text: t("tw_upd_" + upd.phase, { neu: upd.neu || "", alt: upd.alt || "" }) };
    if (instLaeuft) return { art: "arbeit", titel: t("d_z_update"), text: t("ph_" + inst.phase), p: inst.anteil };
    if (z.art === "fehler") return { art: "achtung", titel: t("d_z_hilfe"), text: t("tw_f_" + (z.code || "startet_nicht")), knopf: "neustart" };
    if (!S.motor && !S.entwicklung) return { art: "achtung", titel: t("d_z_hilfe"), text: t("d_z_programmteile"), knopf: "installieren" };
    if (["startet", "abgestuerzt", "gestoppt"].includes(z.art) || !z.art) return { art: "arbeit", titel: t("d_z_startet"), text: t("d_z_startet_text") };
    if (d.art === "fehler" && d.code === "modell") return { art: "achtung", titel: t("d_z_hilfe"), text: t("d_z_stimmen"), knopf: "stimmen" };
    if (d.art === "fehler") return { art: "achtung", titel: t("d_z_hilfe"), text: d.text || t("f_" + (d.code || "einrichtung")), knopf: "neustart" };
    if (d.art === "arbeitet") {
      const lauf = V && V.current;
      return { art: "arbeit", titel: schrittText(lauf, d.typ), text: restText(d), p: d.p || 0 };
    }
    if (S.einstellungen.pausiert || d.art === "pausiert") return { art: "pause", titel: t("d_z_pause"), text: t("d_z_pause_text") };
    return { art: "ruhe", titel: t("d_z_laeuft"), text: t("d_z_laeuft_text") };
  }
  function hinweiseZahl() {
    const n = netzStand();
    return (n.oeffentlich ? 1 : 0) + (n.ohneRegel ? 1 : 0) + (zustand().art === "achtung" ? 1 : 0);
  }
  function erinnern() {
    if (!S.einstellungen.sicherung_erinnern || !V) return false;
    const zuletzt = S.einstellungen.sicherung_zuletzt || 0;
    if (Date.now() / 1000 - zuletzt < ERINNERN_NACH_S) return false;
    return (V.recent || []).some((r) => r.step === "transcribing" && r.result === "done" && r.finishedAt
      && new Date(r.finishedAt).getTime() / 1000 > zuletzt);
  }

  function leisteZeigen() {
    const z = zustand();
    const feld = $("#zustand");
    feld.dataset.art = z.art;
    $("#zustand-titel").textContent = z.titel;
    $("#zustand-neben").textContent = z.text || "";
    feld.setAttribute("aria-label", z.titel + (z.text ? ". " + z.text : ""));
    const balken = $("#zustand-balken");
    balken.hidden = z.p == null;
    if (z.p != null) balken.firstElementChild.style.width = Math.round(z.p * 100) + "%";
    const pruefen = (V && V.awaitingReview) || 0, hinweise = hinweiseZahl();
    zaehler("kampagnen", pruefen, t("d_zaehler_kampagnen", { n: pruefen }));
    zaehler("uebersicht", hinweise, t("d_zaehler_uebersicht", { n: hinweise }));
    const upd = (S.taleward && S.taleward.update) || {};
    const fassung = (S.motor && S.motor.fassung) || S.serverVersion || "";
    $("#fuss-fassung").textContent = fassung ? t("d_fassung", { fassung }) + " · " + t(upd.angebot ? "d_neue_da" : "d_neueste") : "";
    document.body.classList.toggle("schmal", !!S.einstellungen.leiste_schmal);
    const klappe = $("#leiste-klappe");
    klappe.setAttribute("aria-expanded", String(!S.einstellungen.leiste_schmal));
    klappe.querySelector(".bereiche__name").textContent = t(S.einstellungen.leiste_schmal ? "d_leiste_ausklappen" : "d_leiste_einklappen");
    klappe.setAttribute("aria-label", t(S.einstellungen.leiste_schmal ? "d_leiste_ausklappen" : "d_leiste_einklappen"));
    $$("[data-bereich]").forEach((b) => b.setAttribute("aria-label", b.querySelector(".bereiche__name").textContent
      + (b.querySelector(".zaehler:not([hidden])") ? ", " + b.querySelector(".zaehler").getAttribute("aria-label") : "")));
  }
  function zaehler(name, n, text) {
    const el = $("[data-zaehler=\"" + name + "\"]");
    el.hidden = !n; el.textContent = n > 9 ? "9+" : String(n); el.setAttribute("aria-label", text);
  }

  // ------------------------------------------------------------ Übersicht
  function uebersichtZeigen() {
    const z = zustand(), tw = S.taleward || {};
    $("#ue-zustand").dataset.art = z.art;
    $("#ue-titel").textContent = z.titel;
    let text = z.text || "";
    if (z.art === "ruhe" || z.art === "pause") {
      text = t("d_laeuft_text", { name: S.einstellungen.anmeldename || "–" });
      if (tw.zustand && tw.zustand.seit) text += " " + t("d_laeuft_seit", { zeit: uhrzeit(tw.zustand.seit) });
    }
    $("#ue-text").textContent = text;
    const liste = [];
    if (z.knopf === "neustart") liste.push([t("d_neu_starten"), "primary", () => riskant("taleward_neu_starten")]);
    if (z.knopf === "installieren") liste.push([t("d_installieren"), "primary", () => riskant("installieren", !!S.einstellungen.testmodus)]);
    if (z.knopf === "stimmen") liste.push([t("d_einrichten"), "primary", () => { verwaltungLaden.seite = "transkription#hf"; bereichZeigen("verwaltung"); }]);
    const n = netzStand();
    if (n.ohneRegel && !liste.length) liste.push([t("d_erlauben"), "primary", erlauben]);
    knoepfeSetzen($("#ue-knoepfe"), liste);
    const um = $("#ue-update"), upd = tw.update || {};
    const hinweis = upd.phase === "zurueckgenommen" ? t("tw_upd_zurueckgenommen", { neu: upd.neu || "", alt: upd.alt || "" })
      : upd.phase === "fertig" ? t("tw_upd_fertig", { neu: upd.neu || "" }) : "";
    um.hidden = !hinweis; um.textContent = hinweis;
    // Handys
    const zeilen = [];
    if (!n.noetig) zeilen.push(["ja", t("d_netz_erlaubt"), ""]);
    else if (!n.bekannt) zeilen.push(["warn", t("d_netz_unbekannt"), ""]);
    else {
      zeilen.push(n.oeffentlich ? ["nein", t("d_netz_oeffentlich"), t("d_netz_oeffentlich_text")] : ["ja", t("d_netz_privat"), ""]);
      zeilen.push(n.ohneRegel ? ["nein", t("d_netz_nicht_erlaubt"), t("d_netz_nicht_erlaubt_text")] : ["ja", t("d_netz_erlaubt"), ""]);
    }
    pruefliste($("#ue-handys"), zeilen);
    $("#ue-adresse-zeile").hidden = !adresse;
    if (adresse) $("#ue-adresse").textContent = adresse;
    // Verarbeitung
    const st = S.dienst.statistik || {};
    $("#ue-heute").textContent = zahl(st.heute || 0);
    $("#ue-gesamt").textContent = zahl(st.gesamt || 0);
    const letzte = V && (V.recent || []).find((r) => r.result === "done");
    $("#ue-zuletzt").textContent = letzte ? t("d_zuletzt", { was: rundeName(letzte), wann: vor(letzte.finishedAt) }) : t("d_zuletzt_nie");
    // Kapitel schreiben
    const test = (S.motor && S.motor.testmodus) || S.einstellungen.testmodus;
    $("#ue-abschrift").textContent = test ? t("d_abschrift_test") : (S.profil && S.profil.geraet === "cpu") ? t("d_abschrift_cpu") : t("d_abschrift_hier");
    $("#ue-kapitel").textContent = S.ollama && S.ollama.an ? t("d_kapitel_lokal") : t("d_kapitel_server");
    // Sicherung
    const zuletzt = S.einstellungen.sicherung_zuletzt;
    $("#ue-sicherung").textContent = zuletzt ? t("d_sicherung_am", { datum: datum(zuletzt * 1000) }) : t("d_sicherung_nie");
    $("#ue-erinnerung").hidden = !erinnern();
    // Dieser PC
    const k = S.hardware && S.hardware.grafikkarte;
    $("#ue-gpu").textContent = k && S.hardware.grafikkarteNutzbar ? k.name + " · " + gb(k.vram_mb) : t("d_keine_grafikkarte");
    const g = S.einstellungen.vram_grenze_mb;
    $("#ue-grenze").textContent = !k || !S.hardware.grafikkarteNutzbar ? "–" : g ? gb(g) : t("d_alles");
    $("#ue-platz").textContent = S.hardware && S.hardware.platzGb != null ? t("d_frei_gb", { gb: zahl(S.hardware.platzGb) }) : "–";
    $("#ue-autostart").textContent = t(S.autostart ? "d_an" : "d_aus");
  }
  function pruefliste(ziel, zeilen) {
    const schluessel = JSON.stringify(zeilen);
    if (ziel.dataset.s === schluessel) return;
    ziel.dataset.s = schluessel;
    ziel.replaceChildren(...zeilen.map(([ok, titel, wert]) => {
      const li = document.createElement("li"); li.dataset.ok = ok;
      const z = document.createElement("span"); z.className = "zeichen"; z.textContent = ok === "ja" ? "✓" : "!"; z.setAttribute("aria-hidden", "true");
      const ti = document.createElement("span"); ti.className = "titel"; ti.textContent = titel;
      li.append(z, ti);
      if (wert) { const w = document.createElement("span"); w.className = "wert"; w.textContent = wert; li.append(w); }
      return li;
    }));
  }
  async function erlauben() {
    const r = await riskant("netz_freigeben");
    if (!r || !r.ok) toast(t("netz_abgelehnt"));
  }
  function rundeName(r) {
    if (!r.own) return t("d_fremd");
    const teile = [];
    if (r.chapterNumber) teile.push(t("d_kapitel_nr", { n: r.chapterNumber }));
    if (r.campaignTitle) teile.push(r.campaignTitle);
    return teile.join(" · ") || t("d_s_" + (r.step || "other"));
  }

  // ------------------------------------------------------------ Verarbeitung
  function verarbeitungZeigen() {
    const d = S.dienst.zustand || {}, lauf = V && V.current;
    const arbeitet = d.art === "arbeitet" || !!lauf;
    $("#vb-lauf").hidden = !arbeitet;
    $("#vb-leer").hidden = arbeitet;
    $("#vb-leer-text").textContent = S.einstellungen.pausiert ? t("d_pausiert_gerade") : t("d_nichts_gerade");
    if (arbeitet) {
      $("#vb-titel").textContent = schrittText(lauf, d.typ);
      const neben = [];
      if (lauf && lauf.own && lauf.campaignTitle) neben.push(lauf.campaignTitle);
      if (lauf && lauf.playedAt) neben.push(t("d_aufgenommen", { datum: datum(lauf.playedAt) }));
      if (lauf && lauf.durationSeconds) neben.push(t("d_laenge", { dauer: dauer(lauf.durationSeconds) }));
      $("#vb-neben").textContent = neben.join(" · ");
      const schritt = (lauf && lauf.step) || ({ summarize: "summarizing" })[d.typ] || "transcribing";
      const folge = ["transcribing", "summarizing", "pruefen"], pos = folge.indexOf(schritt === "revising" ? "summarizing" : schritt);
      $("#vb-stufen").hidden = pos < 0;
      $$("#vb-stufen li").forEach((li, i) => { li.dataset.stand = i < pos ? "fertig" : i === pos ? "aktiv" : ""; });
      const p = d.art === "arbeitet" ? (d.p || 0) : (lauf && lauf.progress) || 0;
      $("#vb-balken span").style.width = Math.round(p * 100) + "%";
      $("#vb-prozent").textContent = Math.round(p * 100) + " %";
      $("#vb-rest").textContent = d.art === "arbeitet" ? restText(d) : "";
    }
    const warten = (V && V.queued) || 0;
    $("#vb-wartet").textContent = warten === 1 ? t("d_wartet_eins") : warten > 1 ? t("d_wartet", { n: warten }) : "";
    const pause = $("#vb-pause");
    pause.textContent = t(S.einstellungen.pausiert ? "d_fortsetzen" : "d_pausieren");
    // Tabelle
    const fertig = (V && V.recent) || [];
    $("#vb-tabelle").hidden = !fertig.length;
    $("#vb-tabelle-leer").hidden = !!fertig.length;
    const tb = $("#vb-tabelle tbody"), schluessel = JSON.stringify(fertig) + sprache;
    if (tb.dataset.s !== schluessel) {
      tb.dataset.s = schluessel;
      tb.replaceChildren(...fertig.map((r) => {
        const tr = document.createElement("tr");
        const zellen = [
          r.own && r.playedAt ? datum(r.playedAt) + (r.chapterNumber ? " · " + t("d_kapitel_nr", { n: r.chapterNumber }) : "") : "–",
          r.own ? (r.campaignTitle || "–") : t("d_fremd"),
          t("d_s_" + (r.step || "other")),
          r.own && r.durationSeconds ? dauer(r.durationSeconds) : "–",
          dauer(r.seconds),
          t(r.result === "done" ? "d_e_done" : "d_e_failed"),
        ];
        zellen.forEach((text, i) => {
          const td = document.createElement("td"); td.textContent = text;
          if (i === 5 && r.result !== "done") td.className = "ergebnis--fehler";
          tr.append(td);
        });
        return tr;
      }));
    }
    // Leistung
    reglerZeigen();
    const k = S.hardware && S.hardware.grafikkarte;
    $("#vb-prozessor-zeile").hidden = !(k && S.hardware.grafikkarteNutzbar) || !!S.einstellungen.testmodus;
    if (document.activeElement !== $("#vb-prozessor")) $("#vb-prozessor").checked = S.einstellungen.geraet === "cpu";
  }

  function profilText(p) {
    if (!p) return "–";
    if (p.geraet === "cpu") return t("pw_cpu", { modell: p.modell });
    let s = t(p.ausrichten === "cpu" ? "pw_gpu_teil" : "pw_gpu", { modell: p.modell });
    if (p.grenzeMb) s += " · " + t("pw_grenze", { gb: gb(p.grenzeMb) });
    return s;
  }
  function dauerSpanne(d) {
    if (!d) return "";
    return d[1] >= 120 ? t("dauer_std", { a: zahl(d[0] / 60, d[0] % 60 ? 1 : 0), b: zahl(d[1] / 60, d[1] % 60 ? 1 : 0) })
      : t("dauer_min", { a: d[0], b: d[1] });
  }
  function reglerZeigen() {
    const k = S.hardware && S.hardware.grafikkarte;
    const sichtbar = !!(k && S.hardware.grafikkarteNutzbar && S.einstellungen.geraet !== "cpu" && !S.einstellungen.testmodus);
    const maxGb = k ? Math.floor(k.vram_mb / 512) / 2 : 8;
    $$("[data-regler]").forEach((r) => {
      r.hidden = !sichtbar;
      if (!sichtbar) return;
      const ein = r.querySelector("[data-regler-eingabe]");
      if (Number(ein.max) !== maxGb) ein.max = maxGb;
      r.querySelector("[data-regler-max]").textContent = zahl(maxGb, maxGb % 1 ? 1 : 0) + " GB";
      if (document.activeElement !== ein && !ein.dataset.zieht) {
        const g = S.einstellungen.vram_grenze_mb;
        ein.value = g ? Math.min(maxGb, g / 1024) : maxGb;
        reglerText(r, S.profil);
      }
    });
  }
  const reglerWertMb = (ein) => (Number(ein.value) >= Number(ein.max) ? 0 : Math.round(Number(ein.value) * 1024));
  function reglerText(r, p) {
    const ein = r.querySelector("[data-regler-eingabe]");
    const v = Number(ein.value), max = Number(ein.max);
    r.querySelector("[data-regler-wert]").textContent = v >= max ? t("regler_alles") + " · " + zahl(max, max % 1 ? 1 : 0) + " GB"
      : t("regler_von", { a: zahl(v, v % 1 ? 1 : 0) + " GB", b: zahl(max, max % 1 ? 1 : 0) + " GB" });
    ein.setAttribute("aria-valuetext", r.querySelector("[data-regler-wert]").textContent);
    if (!p) return;
    const folge = r.querySelector("[data-regler-folge]");
    const st = document.createElement("strong"); st.textContent = profilText(p);
    folge.replaceChildren(st, document.createElement("br"), document.createTextNode(t("pw_dauer", { dauer: dauerSpanne(p.dauerMin) })));
    if (S.dienst.neustartOffen) folge.append(document.createElement("br"), document.createTextNode(t("neustart_offen")));
  }

  // ------------------------------------------------------------ Sicherung
  function sicherungZeigen() {
    const zuletzt = S.einstellungen.sicherung_zuletzt;
    $("#si-zuletzt").textContent = zuletzt ? t("d_sicherung_am", { datum: datum(zuletzt * 1000) }) : t("d_sicherung_nie");
    let seitdem = "";
    if (zuletzt && V) {
      const n = (V.recent || []).filter((r) => r.step === "transcribing" && r.result === "done" && new Date(r.finishedAt).getTime() / 1000 > zuletzt).length;
      seitdem = n === 0 ? t("d_si_seitdem_keine") : n === 1 ? t("d_si_seitdem_eins") : t("d_si_seitdem", { n });
    }
    $("#si-seitdem").textContent = seitdem;
    $("#si-erinnerung").hidden = !erinnern();
    const s = S.taleward && S.taleward.sicherung;
    const el = $("#si-stand");
    let x = null;
    if (s && s.phase === "speichern") x = ["", t("sich_laeuft_speichern")];
    else if (s && s.phase === "zurueckspielen") x = ["", t("sich_laeuft_zurueck")];
    else if (s && s.phase === "fertig") x = ["", t(s.art === "speichern" ? "sich_fertig_speichern" : "sich_fertig_zurueck", { datei: s.datei })];
    else if (s && s.phase === "fehler") x = ["meldung--fehler", fehlerText("sich_f_", s.fehler)];
    const kennung = s ? [s.phase, s.art, s.datei].join("|") : "";
    if (kennung !== sicherungZeigen.zuletzt && sicherungZeigen.zuletzt && s && ["fertig", "fehler"].includes(s.phase)) toast(x[1]);
    sicherungZeigen.zuletzt = kennung;
    el.hidden = !x;
    if (x) { el.className = "meldung " + x[0]; el.textContent = x[1]; }
    const laeuft = !!(s && ["speichern", "zurueckspielen"].includes(s.phase));
    $("#si-speichern").disabled = laeuft; $("#si-zurueck").disabled = laeuft;
  }
  const fehlerText = (praefix, code) => (t(praefix + code) !== praefix + code ? t(praefix + code) : t(praefix + "unbekannt"));

  // ------------------------------------------------------------ Einstellungen
  function einstellungenZeigen() {
    $$("[data-einstellung]").forEach((el) => {
      if (el === document.activeElement) return;
      const n = el.dataset.einstellung;
      const wert = n === "autostart" ? S.autostart : S.einstellungen[n];
      if (el.type === "checkbox") el.checked = !!wert; else el.value = wert || (n === "modell" ? "auto" : "");
    });
    const upd = (S.taleward && S.taleward.update) || {};
    const werte = { neu: upd.neu || upd.angebot || "", alt: upd.alt || "", prozent: upd.anteil != null ? Math.round(upd.anteil * 100) + " %" : "…" };
    const imUpdate = ["vorbereiten", "sichern", "tauschen", "starten", "pruefen", "zurueck"].includes(upd.phase);
    let text = upd.phase && t("tw_upd_" + upd.phase) !== "tw_upd_" + upd.phase ? t("tw_upd_" + upd.phase, werte)
      : upd.angebot ? t(S.einstellungen.auto_update && upd.angebot !== upd.gescheitert ? "tw_upd_angebot_auto" : "tw_upd_angebot", werte) : "";
    const u = S.appUpdate;
    if (u) text = (text ? text + " " : "") + t("d_programm_neu", { version: u.version });
    $("#es-update").hidden = !text; $("#es-update").textContent = text;
    $("#es-taleward-aktualisieren").hidden = imUpdate || !upd.angebot;
    $("#es-programm-aktualisieren").hidden = !(u && u.moeglich && ["bereit", "fehler"].includes(u.phase));
    // Lokales Modell
    const o = S.ollama || {}, inst = o.installation;
    let stand = "";
    if (inst && inst.phase === "laden") stand = t("d_lokal_laedt", { p: Math.round(inst.anteil * 100) + " %" });
    else if (inst && inst.phase === "entpacken") stand = t("d_lokal_einrichten");
    else if (inst && inst.phase === "fehler") stand = t("d_lokal_fehler");
    else if (o.an && o.fremd) stand = t("d_lokal_fremd");
    else if (o.an && o.laeuft) stand = t("d_lokal_bereit");
    else if (o.an && o.fehler) stand = t("d_lokal_fehler");
    else if (o.an && o.installiert) stand = t("d_lokal_startet");
    else if (!o.an && o.moeglich === false) stand = t("d_lokal_zu_schwach");
    $("#es-lokal-stand").textContent = stand; $("#es-lokal-stand").hidden = !stand;
    const schalter = document.querySelector("#es-recaps-zeile [data-einstellung]");
    schalter.disabled = o.moeglich === false && !o.an;
    $("#es-recaps-zeile").hidden = !!S.einstellungen.testmodus;
    $("#es-lokal-entfernen").hidden = !o.installiert;
  }
  async function speicherLaden() {
    const s = await api.speicherbelegung();
    let text = t("d_speicher", { motor: gb(s.motorMb), modelle: gb(s.modelleMb) });
    if (s.ollamaMb) text += " " + t("d_speicher_lokal", { mb: gb(s.ollamaMb) });
    $("#es-speicher").textContent = text;
  }

  // ------------------------------------------------------------ Hilfe
  async function logLaden(ganz) {
    if (bereich !== "hilfe" || !$("#hi-protokoll").open && !ganz) return;
    const zeilen = await api.protokoll();
    const text = zeilen.join("\n");
    if (text === letzterLog && !ganz) return;
    letzterLog = text;
    const el = $("#hi-log");
    const unten = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.textContent = text;
    if (unten || ganz) el.scrollTop = el.scrollHeight;
  }

  // ------------------------------------------------------------ Rückfragen
  let dialogArt = null, frageAktion = null;
  function dialogOeffnen(art, titel, okText) {
    dialogArt = art;
    $("#dialog-titel").textContent = titel;
    $$("[data-dialog]").forEach((el) => { el.hidden = el.dataset.dialog !== art; });
    $("#dialog-fehler").hidden = true;
    const ok = $("#dialog-ok");
    ok.textContent = okText; ok.disabled = false;
    const d = $("#dialog");
    if (!d.open) d.showModal();
    const erstes = d.querySelector("[data-dialog=\"" + art + "\"] input:not([type=checkbox]), [data-dialog=\"" + art + "\"] textarea");
    (erstes || $("#dialog-abbrechen")).focus();
  }
  function fragen(titel, text, okText, aktion) {
    $("#frage-text").textContent = text;
    frageAktion = aktion;
    dialogOeffnen("frage", titel, okText);
  }
  const dialogFehler = (text) => { const f = $("#dialog-fehler"); f.textContent = text; f.hidden = false; };
  const dialogZu = () => { const d = $("#dialog"); if (d.open) d.close(); };
  async function dialogBestaetigen() {
    const ok = $("#dialog-ok");
    if (ok.disabled) return;
    ok.disabled = true;
    try {
      if (dialogArt === "betrieb") {
        const regelWeg = !$("#betrieb-regel-zeile").hidden && $("#betrieb-regel").checked;
        const r = await riskant("betrieb_wechseln", "verbunden", regelWeg);
        if (!r || !r.ok) return dialogFehler(fehlerText("betrieb_f_", r && r.fehler));
        dialogZu();
      } else if (dialogArt === "speichern") {
        const r = await api.sicherung_speichern($("#sich-passwort").value, $("#sich-passwort2").value);
        if (!r.ok) { if (r.fehler !== "abgebrochen") dialogFehler(fehlerText("sich_f_", r.fehler)); return; }
        dialogZu();
      } else if (dialogArt === "zurueck") {
        const r = await riskant("sicherung_zurueckspielen", $("#zurueck-passwort").value);
        if (!r || !r.ok) return dialogFehler(fehlerText("sich_f_", r && r.fehler));
        dialogZu();
      } else if (dialogArt === "hilfe") {
        const r = await api.hilfe_speichern();
        if (!r.ok) { if (r.fehler !== "abgebrochen") dialogFehler(t("hilfe_f")); return; }
        dialogZu(); toast(t("hilfe_gespeichert", { datei: r.datei }));
      } else if (dialogArt === "frage" && frageAktion) {
        const aktion = frageAktion;
        dialogZu();
        await aktion();
      }
    } finally { ok.disabled = false; neuLaden(); }
  }

  // ------------------------------------------------------------ Darstellen
  function darstellen() {
    if (!S) return;
    leisteZeigen();
    if (RAHMEN_APP.includes(bereich)) appZeigen();
    if (bereich === "uebersicht") uebersichtZeigen();
    if (bereich === "verarbeitung") verarbeitungZeigen();
    if (bereich === "sicherung") sicherungZeigen();
    if (bereich === "einstellungen") einstellungenZeigen();
  }
  async function neuLaden() {
    try { S = await api.zustand(); } catch (e) { return; }
    if (S.ansicht && S.ansicht !== "desktop") return; // das Programm stellt gleich auf das kleine Fenster um
    spracheWaehlen();
    darstellen();
  }
  async function verarbeitungLaden() {
    try { V = await api.verarbeitung(); } catch (e) { V = null; }
    if (bereich === "uebersicht" || bereich === "verarbeitung" || bereich === "sicherung") darstellen();
  }
  async function adresseLaden() {
    try { const r = await api.handy_adresse(); adresse = (r && r.adresse) || null; } catch (e) { adresse = null; }
  }

  // ------------------------------------------------------------ Aktionen
  function binden() {
    $$("[data-bereich]").forEach((b) => { b.onclick = () => bereichZeigen(b.dataset.bereich); });
    $$("[data-gehe]").forEach((b) => { b.onclick = () => bereichZeigen(b.dataset.gehe); });
    $("#zustand").onclick = () => bereichZeigen("uebersicht");
    $("#leiste-klappe").onclick = async () => { await api.einstellung("leiste_schmal", !S.einstellungen.leiste_schmal); neuLaden(); };
    $("#app-hinweis-update").onclick = async () => {
      const r = await riskant("taleward_aktualisieren");
      if (!r || !r.ok) toast(t(r && r.grund === "aktuell" ? "tw_upd_aktuell" : "tw_upd_spaeter"));
    };
    $("#app-hinweis-browser").onclick = () => api.im_browser_oeffnen();
    $("#verwaltung-zurueck").onclick = () => bereichZeigen("einstellungen");
    $("#vb-pause").onclick = async () => { await api.pausieren(!S.einstellungen.pausiert); neuLaden(); };
    $("#vb-prozessor").addEventListener("change", async (e) => { await api.einstellung("geraet", e.target.checked ? "cpu" : "auto"); neuLaden(); });
    $$("[data-regler]").forEach((r) => {
      const ein = r.querySelector("[data-regler-eingabe]");
      let zaehlerNr = 0;
      ein.addEventListener("input", async () => {
        ein.dataset.zieht = "1";
        const nr = ++zaehlerNr;
        reglerText(r, null);
        const p = await api.profil_vorschau(reglerWertMb(ein));
        if (nr === zaehlerNr) reglerText(r, p);
      });
      ein.addEventListener("change", async () => {
        await api.einstellung("vram_grenze_mb", reglerWertMb(ein));
        delete ein.dataset.zieht;
        neuLaden();
      });
    });
    $$("[data-einstellung]").forEach((el) => {
      el.addEventListener("change", async () => {
        const wert = el.type === "checkbox" ? el.checked : el.value;
        const r = await api.einstellung(el.dataset.einstellung, wert);
        if (r && !r.ok && r.fehler) toast(r.fehler);
        neuLaden();
      });
    });
    $("#si-speichern").onclick = () => dialogOeffnen("speichern", t("Sicherung speichern"), t("Speichern …"));
    $("#si-zurueck").onclick = () => {
      const s = S.taleward && S.taleward.sicherung;
      $("#zurueck-datei").textContent = (s && s.gewaehlt) || t("noch_keine_datei");
      dialogOeffnen("zurueck", t("Sicherung zurückspielen"), t("Zurückspielen"));
    };
    $("#zurueck-waehlen").onclick = async () => { const r = await api.sicherung_waehlen(); if (r.ok) $("#zurueck-datei").textContent = r.datei; };
    $("#es-taleward-aktualisieren").onclick = async () => {
      const r = await riskant("taleward_aktualisieren");
      if (!r || !r.ok) toast(t(r && r.grund === "aktuell" ? "tw_upd_aktuell" : "tw_upd_spaeter"));
      neuLaden();
    };
    $("#es-programm-aktualisieren").onclick = async () => { await riskant("app_aktualisieren"); neuLaden(); };
    $("#es-ordner").onclick = () => api.ordner_oeffnen();
    $("#hi-ordner").onclick = () => api.ordner_oeffnen();
    $("#es-lokal-entfernen").onclick = () => fragen(t("d_lokal_entfernen"), t("d_lokal_entfernen_frage"), t("d_lokal_entfernen"),
      async () => { await riskant("ollama_entfernen"); speicherLaden(); });
    $("#es-verwaltung").onclick = () => bereichZeigen("verwaltung");
    $("#es-betrieb").onclick = () => {
      const netz = (S.taleward && S.taleward.netz) || {};
      $("#betrieb-regel-zeile").hidden = !(netz.noetig && netz.vorhanden);
      $("#betrieb-regel").checked = true;
      dialogOeffnen("betrieb", t("d_zu_worker_titel"), t("d_wechseln"));
    };
    $("#es-neu-installieren").onclick = () => fragen(t("d_neu_installieren"), t("d_neu_installieren_frage"), t("d_neu_installieren_knopf"),
      async () => { await riskant("installieren", !!S.einstellungen.testmodus); bereichZeigen("uebersicht"); });
    $("#es-beenden").onclick = () => fragen(t("d_beenden"), t("d_beenden_frage"), t("d_beenden"), () => riskant("beenden"));
    $("#hi-datei").onclick = async () => {
      const r = await api.hilfe_vorschau();
      $("#hilfe-vorschau").value = r.text;
      dialogOeffnen("hilfe", t("Hilfe-Datei"), t("Speichern …"));
    };
    $("#hi-website").onclick = () => api.link_oeffnen(WEBSITE);
    $("#hi-discord").onclick = () => api.link_oeffnen(DISCORD);
    $("#hi-protokoll").addEventListener("toggle", () => logLaden(true));
    $("#hi-kopieren").onclick = () => {
      const text = $("#hi-log").textContent;
      const fertig = () => toast(t("kopiert"));
      const alt = () => {
        const ta = document.createElement("textarea"); ta.value = text; document.body.append(ta); ta.select();
        document.execCommand("copy"); ta.remove(); fertig();
      };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(fertig, alt); else alt();
    };
    $("#dialog-ok").onclick = dialogBestaetigen;
    $("#dialog-abbrechen").onclick = dialogZu;
    $("#dialog").addEventListener("close", () => {
      ["#sich-passwort", "#sich-passwort2", "#zurueck-passwort"].forEach((id) => { $(id).value = ""; });
      $("#hilfe-vorschau").value = ""; dialogArt = null; frageAktion = null;
    });
    $$("#dialog input").forEach((el) => el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); dialogBestaetigen(); } }));
    // Pfeiltasten in der Leiste
    $("#bereiche").addEventListener("keydown", (e) => {
      if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
      const knoepfe = $$("#bereiche [data-bereich]"), i = knoepfe.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      knoepfe[(i + (e.key === "ArrowDown" ? 1 : knoepfe.length - 1)) % knoepfe.length].focus();
    });
  }

  // ------------------------------------------------------------ Start
  async function start() {
    api = window.TalewardBruecke.api;
    uebersetzen();
    binden();
    await neuLaden();
    bereichZeigen("kampagnen", true);
    verarbeitungLaden(); adresseLaden();
    setInterval(neuLaden, 1000);
    setInterval(verarbeitungLaden, 4000);
    setInterval(adresseLaden, 60000);
    setInterval(() => logLaden(false), 2500);
  }
  window.TalewardBruecke.bereit().then(start);
})();
