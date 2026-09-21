/* Althea, the assistant, on every section of the software.
 *
 * The EMR page (dashboard.html) has its own copy of Althea. This is the same assistant for every
 * other page that uses the shared top bar: Overview, Staff, Patients, Schedule and the rest.
 *
 * The backend (/api/althea) only decides WHICH request was made. Every answer below is built from the
 * organization's own saved data (the same browser storage keys the EMR and the Overview read), never
 * written freely, and clinical requests are refused by the backend. Requests that need the patient
 * chart (open a patient, start a visit, allergies, timers...) are handed to the EMR page, which runs
 * them there.
 */
(function () {
  "use strict";
  if (document.getElementById("althea-fab")) return;   /* the EMR page already has one */

  var user = window.__ALTHAIS_USER__ || {};
  var PROVIDER = window.__ALTHAIS_PROVIDER_NAME__ || user.provider_name || "";
  var NS = (function () {
    var base = (user.organization || user.email || "unknown").trim().toLowerCase();
    return "org_" + base.replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") + "::";
  })();
  var K = { patients: NS + "althais.patients.v1", claims: NS + "althais.claims.v1", appts: NS + "althais.appointments.v1", notes: NS + "althais.notes.v1" };

  /* where "open a section" goes when you are not in the EMR */
  var SECTIONS = {
    overview: "/overview", inbox: "/overview/inbox", activity: "/overview/activity",
    claims: "/revenue/claims", revenue: "/revenue/claims",
    scheduler: "/emr/schedule", patients: "/emr/patients", soap: "/emr", emr: "/emr", settings: "/emr#settings",
    staff: "/staff/team", team: "/staff/team"
  };
  /* these need the patient chart, so the EMR page carries them out */
  var HANDOFF = { open_patient: 1, start_visit: 1, check_claim_readiness: 1, read_allergies: 1, read_medications: 1, read_labs: 1, start_visit_timer: 1, stop_visit_timer: 1, claims_denial_scan: 1 };

  var MARK = '<svg viewBox="161 142 1032 1001" fill="#fff" aria-hidden="true" style="width:29px;height:29px;display:block;filter:drop-shadow(0 1px 2px rgba(0,0,0,.22));"><path d="M353 179Q390 142 428 177L676 402L420 648L197 406Q162 368 199 331ZM924 177Q963 142 1000 178L1157 332Q1194 368 1158 406L930 648L676 402ZM420 648L676 884L429 1107Q390 1142 353 1105L199 952Q162 915 198 878ZM930 648L1151 878Q1187 915 1150 951L992 1106Q955 1142 917 1107L676 884Z"/></svg>';

  function load(key, fallback) { try { var raw = localStorage.getItem(key); if (!raw) return fallback; var v = JSON.parse(raw); return v == null ? fallback : v; } catch (e) { return fallback; } }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function todayIso() { return new Date().toISOString().slice(0, 10); }
  function spokenName(name) { var s = String(name || "").trim(), m = s.match(/^([^,]+),\s*(.+)$/); return m ? m[2] + " " + m[1] : s; }   /* "Smith, John" -> "John Smith" for speech */
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  /* ---------- markup ---------- */
  var style = document.createElement("style");
  style.textContent =
    "#althea-fab[hidden],#althea-panel[hidden],#althea-stop-btn[hidden]{display:none!important}" +
    "#althea-fab{animation:althea-float 4.5s ease-in-out infinite}" +
    "@keyframes althea-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}" +
    "#althea-glow{position:absolute;inset:-6px;border-radius:9999px;z-index:-1;background:radial-gradient(circle,rgba(13,91,215,.55),rgba(13,91,215,0) 70%);animation:althea-halo 3.2s ease-in-out infinite;pointer-events:none}" +
    "@keyframes althea-halo{0%,100%{transform:scale(.9);opacity:.45}50%{transform:scale(1.25);opacity:.85}}" +
    "#althea-btn{transition:transform .25s cubic-bezier(.34,1.56,.64,1),box-shadow .25s ease}" +
    "#althea-btn:hover{transform:scale(1.09);box-shadow:0 8px 22px rgba(13,91,215,.55),inset 0 1px 0 rgba(255,255,255,.12)}" +
    "#althea-btn:active{transform:scale(.96)}" +
    "#althea-btn:focus-visible{outline:2px solid #fff;outline-offset:3px;box-shadow:0 0 0 5px #0d5bd7}" +
    "#althea-fab:hover,#althea-fab.is-open{animation-play-state:paused}" +
    "#althea-fab:hover #althea-glow{animation-play-state:paused;opacity:.9;transform:scale(1.15)}" +
    "#althea-btn.althea-active{animation:althea-breathe 1.6s ease-in-out infinite!important}" +
    "@keyframes althea-breathe{0%,100%{box-shadow:0 0 0 4px rgba(13,91,215,.30),0 4px 14px rgba(0,0,0,.28)}50%{box-shadow:0 0 0 8px rgba(13,91,215,.14),0 4px 14px rgba(0,0,0,.28)}}" +
    "#althea-response div{margin-bottom:4px}#althea-response .m{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600}" +
    "@media (prefers-reduced-motion:reduce){#althea-fab,#althea-glow,#althea-btn.althea-active{animation:none!important}#althea-btn{transition:none}}" +
    "@media (max-width:420px){#althea-panel{width:calc(100vw - 32px)!important}}";
  document.head.appendChild(style);

  var fab = document.createElement("div");
  fab.id = "althea-fab";
  fab.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:250;";
  fab.innerHTML =
    '<div id="althea-glow"></div>' +
    '<button id="althea-btn" type="button" title="Ask Althea" aria-label="Ask Althea" aria-expanded="false" aria-controls="althea-panel" style="position:relative;width:56px;height:56px;border-radius:9999px;background:linear-gradient(140deg,#2f7ff0 0%,#0d5bd7 55%,#0a49ad 100%);box-shadow:0 6px 18px rgba(13,91,215,.45),inset 0 1px 0 rgba(255,255,255,.25),inset 0 -3px 8px rgba(0,0,0,.15);display:flex;align-items:center;justify-content:center;border:none;cursor:pointer;color:#fff;">' + MARK + '</button>' +
    '<div id="althea-panel" role="dialog" aria-label="Althea" hidden style="position:absolute;bottom:68px;right:0;width:320px;background:#fff;border:1px solid #d0d3db;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.22);padding:14px;font-family:Inter,system-ui,sans-serif;">' +
      '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px;">' +
        '<span style="font-weight:700;font-size:13.5px;color:#0f1116;">Althea</span>' +
        '<span id="althea-status" role="status" style="font-size:11px;color:#9aa0ac;">Tap the mic and ask</span>' +
        '<button id="althea-stop-btn" type="button" hidden style="margin-left:auto;font-size:10.5px;font-weight:600;color:#c83838;background:#ffe1e1;border:none;border-radius:5px;padding:3px 8px;cursor:pointer;">Stop</button>' +
      '</div>' +
      '<div id="althea-transcript" style="font-size:12px;color:#6b7280;font-style:italic;min-height:16px;margin-bottom:6px;"></div>' +
      '<div id="althea-response" aria-live="polite" style="font-size:12.5px;color:#0f1116;line-height:1.5;"></div>' +
      '<div style="display:flex;gap:6px;margin-top:10px;">' +
        '<input id="althea-text-input" type="text" autocomplete="off" placeholder="Or type a command…" aria-label="Type a command for Althea" style="flex:1;min-width:0;font-size:12px;border:1px solid #d0d3db;border-radius:6px;padding:6px 8px;color:#0f1116;background:#fff;">' +
        '<button id="althea-text-send" type="button" style="font-size:12px;background:#0d5bd7;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-weight:600;cursor:pointer;">Go</button>' +
      '</div>' +
    '</div>';
  (document.body || document.documentElement).appendChild(fab);

  var btn = document.getElementById("althea-btn"), panel = document.getElementById("althea-panel");
  var statusEl = document.getElementById("althea-status"), transcriptEl = document.getElementById("althea-transcript"), responseEl = document.getElementById("althea-response");
  var textInput = document.getElementById("althea-text-input"), textSend = document.getElementById("althea-text-send"), stopBtn = document.getElementById("althea-stop-btn");
  var IDLE = "Tap the mic and ask";

  /* ---------- speech ---------- */
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition, recognition = null, listening = false, finalTranscript = "", silenceTimer = null;
  var history = [], abortCtl = null;

  function pickVoice() {
    try {
      var vs = window.speechSynthesis.getVoices().filter(function (v) { return /^en(-|_)/i.test(v.lang); });
      return vs.filter(function (v) { return /samantha|karen|moira|serena|google us english|jenny|aria/i.test(v.name); })[0] || vs[0] || null;
    } catch (e) { return null; }
  }
  function showStop(on) { stopBtn.hidden = !on; }
  function speak(text) {
    if (!text) return;
    try {
      if (!("speechSynthesis" in window)) return;
      var u = new SpeechSynthesisUtterance(text); u.rate = 1.02;
      var v = pickVoice(); if (v) u.voice = v;
      u.onstart = function () { showStop(true); }; u.onend = u.onerror = function () { showStop(false); };
      window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
    } catch (e) {}
  }
  function stopAll() {
    try { window.speechSynthesis.cancel(); } catch (e) {}
    if (abortCtl) { abortCtl.abort(); abortCtl = null; }
    showStop(false); statusEl.textContent = "Stopped";
    setTimeout(function () { if (statusEl.textContent === "Stopped") statusEl.textContent = IDLE; }, 1500);
  }
  function setListeningUI(on) { listening = on; btn.classList.toggle("althea-active", on); statusEl.textContent = on ? "Listening…" : IDLE; }
  function startListening() {
    if (!SR) { statusEl.textContent = "Voice not supported here, type a command instead"; textInput.focus(); return; }
    finalTranscript = ""; transcriptEl.textContent = ""; responseEl.innerHTML = "";
    recognition = new SR(); recognition.lang = "en-US"; recognition.continuous = true; recognition.interimResults = true; recognition.maxAlternatives = 1;
    recognition.onresult = function (e) {
      var interim = "";
      for (var i = e.resultIndex; i < e.results.length; i++) { var chunk = e.results[i][0].transcript; if (e.results[i].isFinal) finalTranscript += chunk + " "; else interim += chunk; }
      transcriptEl.textContent = '"' + (finalTranscript + interim).trim() + '"';
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(function () { stopListening(true); }, 2000);   /* submit after ~2s of silence */
    };
    recognition.onerror = function (ev) { if (ev.error === "no-speech") return; statusEl.textContent = "Didn't catch that, try again or type below"; };
    recognition.onend = function () { if (listening) { try { recognition.start(); } catch (e) {} } };
    try { recognition.start(); } catch (e) {}
    setListeningUI(true);
  }
  function stopListening(submit) {
    setListeningUI(false);
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
    if (recognition) { try { recognition.stop(); } catch (e) {} recognition = null; }
    var text = finalTranscript.trim();
    if (submit && text) run(text);
  }

  /* ---------- open / close ---------- */
  function openPanel() { panel.hidden = false; fab.classList.add("is-open"); btn.setAttribute("aria-expanded", "true"); }
  function closePanel() { stopListening(false); stopAll(); panel.hidden = true; fab.classList.remove("is-open"); btn.setAttribute("aria-expanded", "false"); }
  btn.addEventListener("click", function () { if (panel.hidden) { openPanel(); startListening(); } else { closePanel(); } });
  stopBtn.addEventListener("click", stopAll);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) { closePanel(); btn.focus(); } });
  textSend.addEventListener("click", function () { var v = textInput.value.trim(); if (!v) return; transcriptEl.textContent = '"' + v + '"'; textInput.value = ""; run(v); });
  textInput.addEventListener("keydown", function (e) { if (e.key === "Enter") textSend.click(); });

  /* ---------- ask the backend which request this is, then answer from local data ---------- */
  function run(transcript) {
    openPanel(); statusEl.textContent = "Thinking…"; showStop(true);
    abortCtl = window.AbortController ? new AbortController() : null;
    fetch("/api/althea", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ transcript: transcript, history: history }), signal: abortCtl ? abortCtl.signal : undefined })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        history.push({ transcript: transcript, intent: data.intent }); if (history.length > 6) history = history.slice(-6);
        execute(data, transcript);
      })
      .catch(function (e) {
        if (e && e.name === "AbortError") return;
        responseEl.textContent = "Sorry, something went wrong reaching Althea."; statusEl.textContent = IDLE;
      })
      .then(function () { abortCtl = null; if (!(window.speechSynthesis && window.speechSynthesis.speaking)) showStop(false); });
  }

  function filterProvider(list) { return PROVIDER ? list.filter(function (x) { return x.provider === PROVIDER; }) : list; }

  function execute(data, transcript) {
    var intent = data.intent, params = data.params || {}, spoken = data.spoken_ack || "", html = null, text = null;

    if (HANDOFF[intent]) {
      /* needs the patient chart: the EMR page picks the request up and runs it */
      try { sessionStorage.setItem("althea:pending", JSON.stringify({ transcript: transcript, ts: Date.now() })); } catch (e) {}
      text = "That one runs in the EMR. Opening it now…"; spoken = spoken || "Opening the EMR.";
      responseEl.textContent = text; statusEl.textContent = "Opening the EMR…"; speak(spoken);
      setTimeout(function () { window.location.href = "/emr"; }, 900);
      return;
    }

    if (intent === "read_schedule") {
      var tomorrow = String(params.date || "").toLowerCase().trim() === "tomorrow";
      var target = tomorrow ? new Date(Date.now() + 86400000).toISOString().slice(0, 10) : todayIso(), day = tomorrow ? "tomorrow" : "today";
      var appts = filterProvider(load(K.appts, []).filter(function (a) { return a.date === target; })).sort(function (a, b) { return (a.time || "").localeCompare(b.time || ""); });
      var who = PROVIDER ? "You have" : "There are", across = PROVIDER ? "" : " across the practice";
      if (!appts.length) { spoken = who + " no appointments scheduled " + day + across + "."; text = spoken; }
      else {
        spoken = who + " " + plural(appts.length, "appointment", "appointments") + " " + day + across + ". " + appts.map(function (a) { return a.time + " with " + spokenName(a.patient) + " for " + a.type; }).join(". ") + ".";
        html = appts.map(function (a) { return '<div><span class="m">' + esc(a.time) + "</span> · " + esc(a.patient) + ' <span style="color:#9aa0ac;">(' + esc(a.type) + ")</span></div>"; }).join("");
      }
    } else if (intent === "next_appointment") {
      var now = new Date();
      var upcoming = filterProvider(load(K.appts, [])).map(function (a) { a._dt = new Date(a.date + "T" + (a.time || "00:00")); return a; })
        .filter(function (a) { return !isNaN(a._dt) && a._dt >= now; }).sort(function (a, b) { return a._dt - b._dt; });
      var next = upcoming[0];
      if (!next) { spoken = "You have no upcoming appointments" + (PROVIDER ? "" : " across the practice") + "."; text = spoken; }
      else {
        var isToday = next.date === todayIso();
        spoken = "Your next appointment is " + (isToday ? "today at " : "on " + next.date + " at ") + next.time + " with " + spokenName(next.patient) + " for " + next.type + ".";
        html = '<div><span class="m">' + esc(next.time) + "</span>" + (isToday ? "" : " · " + esc(next.date)) + " · " + esc(next.patient) + ' <span style="color:#9aa0ac;">(' + esc(next.type) + ")</span></div>";
      }
    } else if (intent === "claims_summary") {
      var claims = filterProvider(load(K.claims, []));
      var n = function (re) { return claims.filter(function (c) { return re.test(c.status || ""); }).length; };
      var pending = n(/submitted|ready to submit/i), denied = n(/denied/i), paid = n(/paid/i), drafts = n(/draft/i);
      var total = claims.reduce(function (s, c) { return s + (Number(c.amount) || 0); }, 0);
      spoken = (PROVIDER ? "You have " : "The practice has ") + pending + " submitted, " + denied + " denied, and " + paid + " paid claims" + (drafts ? ", plus " + drafts + " drafts" : "") + ". Total billed is $" + Math.round(total) + ".";
      html = "<div>Submitted: <b>" + pending + "</b> &nbsp; Denied: <b>" + denied + "</b> &nbsp; Paid: <b>" + paid + "</b>" + (drafts ? " &nbsp; Drafts: <b>" + drafts + "</b>" : "") + "</div><div style=\"margin-top:4px;\">Total billed: <b>$" + Math.round(total).toLocaleString() + "</b></div>";
    } else if (intent === "claims_at_risk") {
      var risky = load(K.claims, []).filter(function (c) { return Number(c.conf || 0) < 75 && !/paid|denied/i.test(c.status || ""); }).sort(function (a, b) { return Number(a.conf || 0) - Number(b.conf || 0); }).slice(0, 5);
      if (!risky.length) { spoken = "No claims currently at high risk of denial."; text = spoken; }
      else {
        spoken = plural(risky.length, "claim is", "claims are") + " at high risk: " + risky.map(function (c) { return spokenName(c.patient) + ", " + (c.conf || 0) + "% confidence"; }).join(". ") + ".";
        html = risky.map(function (c) { return "<div>" + esc(c.patient || "") + ' <span class="m" style="color:#9aa0ac;">' + esc(c.claimId || "") + '</span> · <span style="color:#c83838;">' + (c.conf || 0) + "% conf</span></div>"; }).join("");
      }
    } else if (intent === "documentation_gaps_today") {
      var today = todayIso(), todays = load(K.appts, []).filter(function (a) { return a.date === today; }), notes = load(K.notes, []).filter(function (x) { return x.date === today; });
      var missing = todays.filter(function (a) { return !notes.some(function (x) { return x.mrn === a.mrn; }); });
      if (!todays.length) { spoken = "No appointments scheduled today."; text = spoken; }
      else if (!missing.length) { spoken = "All of today’s visits have documentation on file."; text = spoken; }
      else {
        spoken = missing.length + " of today’s patients " + (missing.length === 1 ? "doesn’t" : "don’t") + " have a note yet: " + missing.map(function (a) { return spokenName(a.patient); }).join(", ") + ".";
        html = missing.map(function (a) { return "<div>" + esc(a.time || "") + " · " + esc(a.patient || "") + "</div>"; }).join("");
      }
    } else if (intent === "prior_auth_pending") {
      var pend = [];
      load(K.patients, []).forEach(function (p) { (p.priorAuths || []).forEach(function (pa) { if (/pending/i.test(pa.status || "")) pend.push({ patient: p.name, service: pa.service, payer: pa.payer }); }); });
      if (!pend.length) { spoken = "No pending prior authorizations."; text = spoken; }
      else {
        spoken = plural(pend.length, "pending prior authorization", "pending prior authorizations") + ": " + pend.map(function (x) { return spokenName(x.patient) + " for " + x.service; }).join(". ") + ".";
        html = pend.map(function (x) { return "<div>" + esc(x.patient) + " · " + esc(x.service || "") + ' <span style="color:#9aa0ac;">(' + esc(x.payer || "") + ")</span></div>"; }).join("");
      }
    } else if (intent === "coding_complexity_check") {
      var em = function (code) {
        var c = String(code || "").trim(), t = { "99281": 1, "99282": 2, "99283": 3, "99284": 4, "99285": 5, "99202": 1, "99203": 2, "99204": 3, "99205": 4, "99212": 1, "99213": 2, "99214": 3, "99215": 4 };
        return t[c] == null ? null : t[c];
      };
      var by = {};
      load(K.claims, []).forEach(function (c) { var l = em(c.cpt); if (l == null || !c.provider) return; (by[c.provider] = by[c.provider] || []).push(l); });
      var provs = Object.keys(by);
      if (provs.length < 2) { spoken = "Not enough coded visits yet to compare providers."; text = spoken; }
      else {
        var avgs = provs.map(function (p) { var ls = by[p]; return { provider: p, avg: ls.reduce(function (s, v) { return s + v; }, 0) / ls.length, n: ls.length }; });
        var overall = avgs.reduce(function (s, a) { return s + a.avg; }, 0) / avgs.length;
        var below = avgs.filter(function (a) { return a.avg < overall - 0.4; }).sort(function (a, b) { return a.avg - b.avg; });
        if (!below.length) { spoken = "Coding levels look consistent across providers, no outliers."; text = spoken; }
        else {
          spoken = below.map(function (a) { return a.provider + " averages level " + a.avg.toFixed(1); }).join(". ") + ". Practice average is " + overall.toFixed(1) + ".";
          html = '<div style="margin-bottom:3px;color:#9aa0ac;">Practice average: ' + overall.toFixed(1) + "</div>" + below.map(function (a) { return "<div>" + esc(a.provider) + ': <b style="color:#b86a00;">' + a.avg.toFixed(1) + '</b> <span style="color:#9aa0ac;">(' + a.n + " visits)</span></div>"; }).join("") + '<div style="margin-top:4px;font-size:10.5px;color:#9aa0ac;">Coding-pattern signal only, not a judgment on any single visit.</div>';
        }
      }
    } else if (intent === "open_section") {
      var dest = SECTIONS[String(params.section || "").toLowerCase().trim()];
      if (dest) { text = spoken || "Opening it now."; statusEl.textContent = "Opening…"; responseEl.textContent = text; speak(spoken); setTimeout(function () { window.location.href = dest; }, 500); return; }
      text = spoken || "Which section? Try Overview, Claims, Schedule, Patients, Staff or Settings.";
    } else {
      spoken = spoken || "I can help with your schedule, claims, prior authorizations, documentation gaps or navigation. Not clinical questions.";
      text = spoken;
    }

    if (html != null) responseEl.innerHTML = html; else responseEl.textContent = text || spoken || "Done.";
    statusEl.textContent = IDLE;
    speak(spoken);
  }
})();
