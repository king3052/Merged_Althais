/* Althea, the assistant, on every section of the software.
 *
 * The EMR page (dashboard.html) has its own engine; this is the same assistant for every other page that
 * uses the shared top bar: Overview, Staff, Patients, Schedule and the rest. Both use the shared interface
 * (static/js/althea-ui.js).
 *
 * The backend (/api/althea) only decides WHICH request was made. Every answer below is built from the
 * organization's own saved data (the same browser storage keys the EMR and the Overview read), never
 * written freely, and clinical requests are refused by the backend. Requests that need the patient chart
 * (open a patient, start a visit, allergies, timers...) are handed to the EMR page, which runs them there.
 *
 * Voice: tap the mic once and it becomes a conversation. You speak, Althea answers out loud, then it listens
 * again, until you tap the mic to stop, so you can give several commands in a row.
 */
(function () {
  "use strict";
  if (document.getElementById("althea-fab") || !window.AltheaUI) return;   /* the EMR page mounts its own */
  var me = window.__ALTHAIS_USER__ || {};
  if (!me.althea) return;                                                  /* switched on per clinic in /admin */
  /* which tool this page belongs to: each tool's Settings can hide Althea on its own pages */
  var path = location.pathname;
  var TOOL = path === "/scribe" ? "scribe" : path === "/coding" ? "coding" : /^\/revenue\//.test(path) ? "insurance"
    : /^\/staff\//.test(path) ? "team" : "emr";
  /* Althea only offers, and only does, what the clinic bought (the server enforces the same: main.py _ALTHEA_INTENT_TOOLS) */
  var PRODUCTS = me.products || [];
  var suite = PRODUCTS.indexOf("suite") !== -1;
  function has(p) { return suite || PRODUCTS.indexOf(p) !== -1; }
  var OFFER = {   /* per tool: what she says she can do, her suggestions, and "how do I" answers */
    scribe: { can: "writing and signing visit notes", chips: ["How does Scribe work?", "How do I sign a note?"],
      help: [[/scribe|record|transcri|write (a|the) note|how.*work/i, "In Write A Note, fill in the patient, check the consent box, then tap Start Recording. When the visit ends, tap Write The Note and I fill in the SOAP fields from the transcript. Check every field, then Save Draft or Sign Note."],
             [/sign|amend|signature/i, "Open the note and tap Sign Note. Once it’s signed, changes are saved as an amendment. You can add credentials like MD to your signature in Settings > Scribe."]] },
    coding: { can: "coding notes and checking codes before billing", chips: ["How do I code a note?", "What does the confidence score mean?"],
      help: [[/confiden/i, "Confidence is how sure I am that a code is supported by the note. Green is 85% and up, amber 70 to 84, red below 70. You can hide low-confidence suggestions in Settings > Coding."],
             [/check|valid|readiness|score/i, "After codes are suggested, tap Check Codes. You get a readiness score out of 100, the problems found and anything missing, before the claim goes out."],
             [/code|cpt|icd|suggest|how.*work/i, "In Code A Note, paste a note or pick a saved one, set the encounter and minutes, then tap Suggest Codes. Review each code, remove or add your own, then Copy or Export CSV."]] },
    insurance: { can: "claims, denials, appeals, payments and payers", chips: ["Which claims are at risk?", "What needs to be appealed?", "How are our payers doing?", "Show recent payments"], help: [] },
    staff: { can: "your team’s credentials, training and onboarding", chips: ["Whose credentials expire soon?", "Who has overdue training?", "How do I add a team member?"],
      help: [[/add|invite|new (hire|person|member)/i, "Go to Staff > Onboarding and tap Invite Team Member for a new hire, or Team > Add Existing Staff for someone already working at the clinic."],
             [/credential|license|certif/i, "Credentials live in Staff > Credentials. Add each license with its expiry date; I warn you ahead of time, and you can change how far ahead in Settings > Team."]] }
  };
  var MINE = ["scribe", "coding", "insurance", "staff"].filter(function (p) { return !suite && has(p); });
  var hello, chips;
  if (!suite && MINE.length) {
    var cans = MINE.map(function (p) { return OFFER[p].can; });
    hello = "Hi, I’m Althea. I can help with " + (cans.length > 1 ? cans.slice(0, -1).join(", ") + " and " + cans[cans.length - 1] : cans[0]) + ". Tap the mic to talk, and I’ll keep listening until you tap it again.";
    chips = []; MINE.forEach(function (p) { OFFER[p].chips.forEach(function (c) { if (chips.length < 6) chips.push(c); }); });
  }
  AltheaUI.mount(hello ? { hello: hello, chips: chips } : undefined);
  fetch("/api/org/settings/" + TOOL + "_prefs", { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) { if (j && j.data && j.data.show_althea === false) { var f = document.getElementById("althea-fab"); if (f) f.style.display = "none"; } })
    .catch(function () {});

  var user = window.__ALTHAIS_USER__ || {};
  var PROVIDER = window.__ALTHAIS_PROVIDER_NAME__ || user.provider_name || "";
  var NS = (function () {
    var base = (user.organization || user.email || "unknown").trim().toLowerCase();
    return "org_" + base.replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") + "::";
  })();
  var K = { patients: NS + "althais.patients.v1", claims: NS + "althais.claims.v1", appts: NS + "althais.appointments.v1", notes: NS + "althais.notes.v1",
            payerRules: NS + "althais.payer_rules.v1", payerNotes: "althais.payer_notes.v1" /* not namespaced: matches the EMR's own key */ };

  /* where "open a section" goes when you are not in the EMR */
  var SECTIONS = {
    overview: "/overview", inbox: "/overview/inbox", activity: "/overview/activity",
    claims: "/revenue/claims", revenue: "/revenue/claims",
    scheduler: "/emr/schedule", patients: "/emr/patients", soap: "/emr", emr: "/emr", settings: "/settings",
    staff: "/staff/team", team: "/staff/team",
    scribe: "/scribe", code_a_note: "/coding", credentials: "/staff/credentials", training: "/staff/training",
    onboarding: "/staff/onboarding", roles: "/staff/roles", compliance: "/staff/compliance"
  };
  var REV = window.AltheaRevenue || null;   /* claims, denials, payer intelligence, payments (static/js/althea-revenue.js) */
  if (REV) Object.keys(REV.SECTIONS).forEach(function (k) { SECTIONS[k] = REV.SECTIONS[k]; });
  /* these need the patient chart, so the EMR page carries them out */
  var HANDOFF = { scribe_visit: 1, new_patient: 1, dictate_visit_note: 1, open_patient: 1, start_visit: 1, check_claim_readiness: 1, read_allergies: 1, read_medications: 1, read_labs: 1, start_visit_timer: 1, stop_visit_timer: 1, claims_denial_scan: 1 };

  function $(id) { return document.getElementById(id); }
  function load(key, fallback) { try { var raw = localStorage.getItem(key); if (!raw) return fallback; var v = JSON.parse(raw); return v == null ? fallback : v; } catch (e) { return fallback; } }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function todayIso() { return new Date().toISOString().slice(0, 10); }
  function spokenName(name) { var s = String(name || "").trim(), m = s.match(/^([^,]+),\s*(.+)$/); return m ? m[2] + " " + m[1] : s; }   /* "Smith, John" -> "John Smith" for speech */
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  var fab = $("althea-fab"), btn = $("althea-btn"), panel = $("althea-panel");
  var statusEl = $("althea-status"), transcriptEl = $("althea-transcript"), responseEl = $("althea-response");
  var textInput = $("althea-text-input"), textSend = $("althea-text-send"), stopBtn = $("althea-stop-btn"), micBtn = $("althea-mic"), closeBtn = $("althea-close");
  var wave = $("althea-wave"), ring = $("althea-ring"), bars = wave.querySelectorAll(".althea-wave-bar");
  var IDLE = "Ask anything or tap the mic";
  var SR = AltheaUI.SR, recognition = null, listening = false, conversation = false, finalTranscript = "", silenceTimer = null, clearOnSpeech = false;
  var history = [], abortCtl = null;
  var audioSession = 0, audioCtx = null, micStream = null, levelRAF = null;

  /* ---------- speaking ---------- */
  function pickVoice() {
    try {
      var vs = window.speechSynthesis.getVoices().filter(function (v) { return /^en(-|_)/i.test(v.lang); });
      return vs.filter(function (v) { return /samantha|karen|moira|serena|google us english|jenny|aria/i.test(v.name); })[0] || vs[0] || null;
    } catch (e) { return null; }
  }
  function showStop(on) { stopBtn.classList.toggle("hidden", !on); }
  function speaking() { try { return !!(window.speechSynthesis && window.speechSynthesis.speaking); } catch (e) { return false; } }
  /* Althea only talks back when you talked to her: a spoken command (or the mic) gets a spoken answer;
     typing a command or clicking a suggestion gets a written one, in silence. */
  var voiceTurn = false;
  function speak(text) {
    if (!text || !voiceTurn) return;
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
    setTimeout(function () { if (statusEl.textContent === "Stopped") statusEl.textContent = listening ? "Listening… tap the mic to stop" : IDLE; }, 1200);
  }

  /* ---------- listening (the mic keeps a conversation going) ---------- */
  function setListeningUI(on) {
    listening = on;
    micBtn.classList.toggle("is-on", on); micBtn.setAttribute("aria-pressed", String(on)); micBtn.setAttribute("aria-label", on ? "Stop listening" : "Talk to Althea");
    statusEl.textContent = on ? "Listening… tap the mic to stop" : IDLE;
  }
  function startMeter() {
    var session = ++audioSession; wave.style.display = "flex";
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      if (session !== audioSession) { stream.getTracks().forEach(function (t) { t.stop(); }); return; }   /* stopped while waiting for permission */
      micStream = stream; audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      var an = audioCtx.createAnalyser(); an.fftSize = 128; audioCtx.createMediaStreamSource(stream).connect(an);
      var n = an.frequencyBinCount, data = new Uint8Array(n);
      (function loop() {
        if (session !== audioSession) return;
        an.getByteFrequencyData(data);
        var sum = 0; for (var i = 0; i < n; i++) sum += data[i];
        var avg = sum / n;
        ring.style.transform = "scale(" + (1 + Math.min(avg / 90, 1) * 0.5).toFixed(3) + ")"; ring.style.opacity = (0.25 + Math.min(avg / 90, 1) * 0.55).toFixed(2);
        var step = Math.floor(n / bars.length) || 1;
        for (var b = 0; b < bars.length; b++) bars[b].style.height = (4 + ((data[b * step] || 0) / 255) * 34).toFixed(1) + "px";
        levelRAF = requestAnimationFrame(loop);
      })();
    }).catch(function () { /* mic permission denied: the wave just stays flat */ });
  }
  function stopMeter() {
    audioSession++; if (levelRAF) { cancelAnimationFrame(levelRAF); levelRAF = null; }
    ring.style.transform = "scale(1)"; ring.style.opacity = "0";
    for (var i = 0; i < bars.length; i++) bars[i].style.height = "6px";
    wave.style.display = "none";
    if (micStream) { micStream.getTracks().forEach(function (t) { t.stop(); }); micStream = null; }
    if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
  }
  function startListening(resume) {
    if (!SR) { statusEl.textContent = "Voice isn’t supported here, type a command instead"; textInput.focus(); return; }
    finalTranscript = "";
    if (resume) { clearOnSpeech = true; }                       /* keep the last answer readable until you speak again */
    else { clearOnSpeech = false; transcriptEl.textContent = ""; responseEl.innerHTML = ""; }
    recognition = new SR(); recognition.lang = "en-US"; recognition.continuous = true; recognition.interimResults = true; recognition.maxAlternatives = 1;
    recognition.onresult = function (e) {
      if (clearOnSpeech) { clearOnSpeech = false; transcriptEl.textContent = ""; responseEl.innerHTML = ""; }
      var interim = "";
      for (var i = e.resultIndex; i < e.results.length; i++) { var chunk = e.results[i][0].transcript; if (e.results[i].isFinal) finalTranscript += chunk + " "; else interim += chunk; }
      transcriptEl.textContent = '"' + (finalTranscript + interim).trim() + '"';
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(function () { stopListening(true); }, 2000);   /* submit after ~2s of silence */
    };
    recognition.onerror = function (ev) { if (ev.error === "no-speech") return; statusEl.textContent = "Didn’t catch that, try again or type below"; };
    recognition.onend = function () { if (listening) { try { recognition.start(); } catch (e) {} } };
    try { recognition.start(); } catch (e) {}
    setListeningUI(true); startMeter();
  }
  function stopListening(submit) {
    setListeningUI(false); stopMeter();
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
    if (recognition) { try { recognition.stop(); } catch (e) {} recognition = null; }
    var text = finalTranscript.trim(); finalTranscript = "";
    if (submit && text) { voiceTurn = true; run(text); }
  }
  /* after an answer (and after Althea has finished speaking it), listen again while the conversation is on */
  function maybeResume() {
    if (!conversation || panel.classList.contains("hidden")) return;
    var tries = 0, t = setInterval(function () {
      tries++;
      if (!conversation || panel.classList.contains("hidden") || tries > 100) { clearInterval(t); return; }
      if (!speaking()) { clearInterval(t); setTimeout(function () { if (conversation && !listening && !panel.classList.contains("hidden")) startListening(true); }, 350); }
    }, 300);
  }
  micBtn.addEventListener("click", function () {
    voiceTurn = true;                                                  /* using the mic means you are talking to her */
    if (listening) { conversation = false; stopListening(true); }      /* end the conversation, sending anything already heard */
    else { conversation = true; startListening(false); }
  });

  /* ---------- open / close ---------- */
  function openPanel() { panel.classList.remove("hidden"); btn.setAttribute("aria-expanded", "true"); }
  function closePanel() { conversation = false; stopListening(false); stopAll(); panel.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); }
  btn.addEventListener("click", function () { openPanel(); if (window.matchMedia && window.matchMedia("(pointer: fine)").matches) textInput.focus(); });
  closeBtn.addEventListener("click", function () { closePanel(); btn.focus(); });
  stopBtn.addEventListener("click", stopAll);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.classList.contains("hidden")) { closePanel(); btn.focus(); } });
  textSend.addEventListener("click", function () { var v = textInput.value.trim(); if (!v) return; textInput.value = ""; ask(v); });
  textInput.addEventListener("keydown", function (e) { if (e.key === "Enter") textSend.click(); });
  $("althea-chips").addEventListener("click", function (e) { var b = e.target.closest("button[data-q]"); if (b) ask(b.getAttribute("data-q")); });
  function ask(text) { voiceTurn = false; transcriptEl.textContent = '"' + text + '"'; run(text); }   /* typed or clicked: answer in writing only */

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
        responseEl.textContent = "Sorry, something went wrong reaching Althea."; statusEl.textContent = IDLE; maybeResume();
      })
      .then(function () { abortCtl = null; if (!speaking()) showStop(false); });
  }

  /* "how do I…" about the clinic's own tools, answered here */
  function helpFor(q) {
    var tools = suite ? ["scribe", "coding", "staff"] : MINE, order = [TOOL === "team" ? "staff" : TOOL].concat(tools);
    for (var i = 0; i < order.length; i++) {
      var o = OFFER[order[i]]; if (!o || !has(order[i])) continue;
      for (var j = 0; j < o.help.length; j++) if (o.help[j][0].test(q)) return o.help[j][1];
    }
    return null;
  }
  /* Team: read the clinic's staff records (/api/staff) */
  function staffAnswer(intent) {
    return fetch("/api/staff", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (res) {
      var d = (res && res.data) || {}, people = {}, today = new Date(new Date().toDateString());
      (d.people || []).forEach(function (p) { people[p.id] = p.name || "Someone"; });
      function days(iso) { return iso ? Math.round((new Date(iso + "T00:00:00") - today) / 864e5) : null; }
      var rows;
      if (intent === "staff_credentials_expiring") {
        rows = (d.credentials || []).map(function (c) { return { who: people[c.personId] || "Someone", what: String(c.type || "Credential").replace(/_/g, " "), d: days(c.expires) }; })
          .filter(function (x) { return x.d != null && x.d <= 60; }).sort(function (a, b) { return a.d - b.d; });
        if (!rows.length) return { spoken: "No credentials are expired or expiring in the next 60 days.", html: "No credentials are expired or expiring in the next 60 days." };
        return { spoken: rows.length + (rows.length === 1 ? " credential needs" : " credentials need") + " attention. " + rows.slice(0, 3).map(function (x) { return x.who + "’s " + x.what + (x.d < 0 ? " expired" : " expires in " + x.d + " days"); }).join(". ") + ".",
          html: rows.map(function (x) { return "<div>" + esc(x.who) + ": " + esc(x.what) + ' <b style="color:' + (x.d < 0 ? "#c83838" : "#b86a00") + '">' + (x.d < 0 ? "expired " + -x.d + "d ago" : "in " + x.d + "d") + "</b></div>"; }).join("") + '<div style="margin-top:4px"><a href="/staff/credentials" style="font-weight:600">Open Credentials →</a></div>' };
      }
      rows = (d.trainings || []).filter(function (t) { return !t.completed; }).map(function (t) { return { who: people[t.personId] || "Someone", what: t.course || "Training", d: days(t.due) }; })
        .filter(function (x) { return x.d != null && x.d <= 14; }).sort(function (a, b) { return a.d - b.d; });
      if (!rows.length) return { spoken: "No training is overdue or due in the next two weeks.", html: "No training is overdue or due in the next two weeks." };
      return { spoken: rows.length + " training " + (rows.length === 1 ? "assignment needs" : "assignments need") + " attention. " + rows.slice(0, 3).map(function (x) { return x.who + ": " + x.what + (x.d < 0 ? ", overdue" : ", due in " + x.d + " days"); }).join(". ") + ".",
        html: rows.map(function (x) { return "<div>" + esc(x.who) + ": " + esc(x.what) + ' <b style="color:' + (x.d < 0 ? "#c83838" : "#b86a00") + '">' + (x.d < 0 ? "overdue" : "due in " + x.d + "d") + "</b></div>"; }).join("") + '<div style="margin-top:4px"><a href="/staff/training" style="font-weight:600">Open Training →</a></div>' };
    }).catch(function () { return { spoken: "I couldn’t read your team’s records just now.", html: "I couldn’t read your team’s records just now." }; });
  }

  function filterProvider(list) { return PROVIDER ? list.filter(function (x) { return x.provider === PROVIDER; }) : list; }

  function execute(data, transcript) {
    var intent = data.intent, params = data.params || {}, spoken = data.spoken_ack || "", html = null, text = null;

    if (HANDOFF[intent]) {
      /* needs the patient chart: the EMR page picks the request up and runs it */
      try { sessionStorage.setItem("althea:pending", JSON.stringify({ transcript: transcript, ts: Date.now(), voice: voiceTurn })); } catch (e) {}
      text = "That one runs in the EMR. Opening it now…"; spoken = spoken || "Opening the EMR.";
      responseEl.textContent = text; statusEl.textContent = "Opening the EMR…"; speak(spoken);
      setTimeout(function () { window.location.href = "/emr"; }, 900);
      return;
    }

    if (REV && REV.handles(intent)) {
      var rev = REV.answer(intent, params, { claims: load(K.claims, []), payerNotes: load(K.payerNotes, []), payerRules: load(K.payerRules, []), spokenName: spokenName });
      spoken = rev.spoken; html = rev.html;
    } else if (intent === "read_schedule") {
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
    } else if (intent === "not_in_plan") {
      text = spoken || "That isn’t part of your plan.";
    } else if (intent === "staff_credentials_expiring" || intent === "staff_training_overdue") {
      statusEl.textContent = "Checking your team…";
      staffAnswer(intent).then(function (r) { responseEl.innerHTML = r.html; statusEl.textContent = IDLE; speak(r.spoken); maybeResume(); });
      return;
    } else if (intent === "general_question") {
      var tip = helpFor(transcript);
      if (tip) { text = spoken = tip; }
      else {
        responseEl.textContent = "Looking that up…";
        fetch("/api/althea-public", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages: [{ role: "user", content: transcript }] }) })
          .then(function (r) { return r.json(); })
          .then(function (d) { var t = d.reply || "I couldn’t find a good answer for that."; responseEl.textContent = t; statusEl.textContent = IDLE; speak(t); maybeResume(); })
          .catch(function () { responseEl.textContent = "I couldn’t reach my knowledge base just now."; statusEl.textContent = IDLE; maybeResume(); });
        return;
      }
    } else if (intent === "open_section") {
      var dest = SECTIONS[String(params.section || "").toLowerCase().trim()];
      if (dest) { text = spoken || "Opening it now."; statusEl.textContent = "Opening…"; responseEl.textContent = text; speak(spoken); setTimeout(function () { window.location.href = dest; }, 500); return; }
      text = spoken || "Which section? Try Overview, Claims, Denials, Payments, Schedule, Patients, Staff or Settings.";
    } else {
      spoken = spoken || (suite ? "I can help with your schedule, claims, denials, payers, payments, prior authorizations, documentation gaps or navigation. Not clinical questions."
        : "I can help with " + MINE.map(function (p) { return OFFER[p].can; }).join(" and ") + ". Not clinical questions.");
      text = spoken;
    }

    if (html != null) responseEl.innerHTML = html; else responseEl.textContent = text || spoken || "Done.";
    statusEl.textContent = IDLE;
    speak(spoken);
    maybeResume();
  }
})();
