/* Althea in the Staff Portal: the same assistant, look and voice conversation as everywhere else in Althais
 * (static/js/althea-ui.js builds the interface; this mirrors static/js/althea.js), limited to staff-portal
 * questions. Answers come from /api/portal/althea (staff_assistant.py), built only from the signed-in person's
 * own staff record, never anyone else's.
 */
(function () {
  "use strict";
  if (!window.AltheaUI || document.getElementById("althea-fab")) return;

  AltheaUI.mount({
    chips: ["What do I still need to do?", "When does my BLS expire?", "Which training do I need?", "Where do I upload my license?",
            "Why wasn't my document accepted?", "What do I need before my first day?"],
    hello: "Hi, I’m Althea. Ask me about your onboarding, documents, credentials and training. Tap the mic to talk, and I’ll keep listening until you tap it again."
  });

  var $ = function (id) { return document.getElementById(id); };
  /* staff answers can be long lists: once there's a conversation, the suggestions step aside for the answer */
  var css = document.createElement("style");
  css.textContent = "#althea-panel.is-portal.has-exchange .alt-chips{display:none}";
  document.head.appendChild(css);
  $("althea-panel").classList.add("is-portal");
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  var btn = $("althea-btn"), panel = $("althea-panel");
  var statusEl = $("althea-status"), transcriptEl = $("althea-transcript"), responseEl = $("althea-response"), actionsEl = $("althea-actions");
  var textInput = $("althea-text-input"), textSend = $("althea-text-send"), stopBtn = $("althea-stop-btn"), micBtn = $("althea-mic"), closeBtn = $("althea-close");
  var wave = $("althea-wave"), ring = $("althea-ring"), bars = wave.querySelectorAll(".althea-wave-bar");
  var IDLE = "Ask about your onboarding or tap the mic";
  statusEl.textContent = IDLE;
  textInput.placeholder = "Ask about your onboarding…";
  textInput.setAttribute("aria-label", "Ask Althea about your onboarding");
  var SR = AltheaUI.SR, recognition = null, listening = false, conversation = false, finalTranscript = "", silenceTimer = null, clearOnSpeech = false;
  var abortCtl = null, audioSession = 0, audioCtx = null, micStream = null, levelRAF = null, voiceTurn = false;

  /* ---------- speaking: only when you talked to her ---------- */
  function pickVoice() {
    try {
      var vs = window.speechSynthesis.getVoices().filter(function (v) { return /^en(-|_)/i.test(v.lang); });
      return vs.filter(function (v) { return /samantha|karen|moira|serena|google us english|jenny|aria/i.test(v.name); })[0] || vs[0] || null;
    } catch (e) { return null; }
  }
  function showStop(on) { stopBtn.classList.toggle("hidden", !on); }
  function speaking() { try { return !!(window.speechSynthesis && window.speechSynthesis.speaking); } catch (e) { return false; } }
  function speak(text) {
    if (!text || !voiceTurn) return;
    try {
      if (!("speechSynthesis" in window)) return;
      var u = new SpeechSynthesisUtterance(text.replace(/•/g, "").replace(/\n+/g, ". ")); u.rate = 1.02;
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

  /* ---------- listening: the mic keeps a conversation going ---------- */
  function setListeningUI(on) {
    listening = on;
    micBtn.classList.toggle("is-on", on); micBtn.setAttribute("aria-pressed", String(on)); micBtn.setAttribute("aria-label", on ? "Stop listening" : "Talk to Althea");
    statusEl.textContent = on ? "Listening… tap the mic to stop" : IDLE;
  }
  function startMeter() {
    var session = ++audioSession; wave.style.display = "flex";
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      if (session !== audioSession) { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
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
    }).catch(function () {});
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
    if (!SR) { statusEl.textContent = "Voice isn’t supported here, type your question instead"; textInput.focus(); return; }
    finalTranscript = "";
    if (resume) { clearOnSpeech = true; } else { clearOnSpeech = false; transcriptEl.textContent = ""; responseEl.innerHTML = ""; clearActions(); }
    recognition = new SR(); recognition.lang = "en-US"; recognition.continuous = true; recognition.interimResults = true; recognition.maxAlternatives = 1;
    recognition.onresult = function (e) {
      if (clearOnSpeech) { clearOnSpeech = false; transcriptEl.textContent = ""; responseEl.innerHTML = ""; clearActions(); }
      var interim = "";
      for (var i = e.resultIndex; i < e.results.length; i++) { var chunk = e.results[i][0].transcript; if (e.results[i].isFinal) finalTranscript += chunk + " "; else interim += chunk; }
      transcriptEl.textContent = '"' + (finalTranscript + interim).trim() + '"';
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(function () { stopListening(true); }, 2000);
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
  function maybeResume() {
    if (!conversation || panel.classList.contains("hidden")) return;
    var tries = 0, t = setInterval(function () {
      tries++;
      if (!conversation || panel.classList.contains("hidden") || tries > 100) { clearInterval(t); return; }
      if (!speaking()) { clearInterval(t); setTimeout(function () { if (conversation && !listening && !panel.classList.contains("hidden")) startListening(true); }, 350); }
    }, 300);
  }
  micBtn.addEventListener("click", function () {
    voiceTurn = true;
    if (listening) { conversation = false; stopListening(true); } else { conversation = true; startListening(false); }
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
  function ask(text) { voiceTurn = false; transcriptEl.textContent = '"' + text + '"'; run(text); }

  /* ---------- the answer, and where to go next ---------- */
  function clearActions() { actionsEl.innerHTML = ""; actionsEl.classList.add("hidden"); }
  function showActions(links) {
    if (!links || !links.length) return clearActions();
    actionsEl.innerHTML = links.map(function (l, i) { return '<button type="button"' + (i === 0 ? ' class="primary"' : "") + ' data-href="' + esc(l.href) + '">' + esc(l.label) + "</button>"; }).join("");
    actionsEl.classList.remove("hidden");
  }
  actionsEl.addEventListener("click", function (e) { var b = e.target.closest("button[data-href]"); if (b) location.hash = b.getAttribute("data-href").replace(/^#/, ""); });
  function run(question) {
    openPanel(); statusEl.textContent = "Thinking…"; showStop(true); clearActions();
    abortCtl = window.AbortController ? new AbortController() : null;
    fetch("/api/portal/althea", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ question: question }), signal: abortCtl ? abortCtl.signal : undefined })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || j.detail || "error"); return j; }); })
      .then(function (data) {
        responseEl.innerHTML = esc(data.answer).replace(/\n/g, "<br>");
        showActions(data.links);
        statusEl.textContent = IDLE;
        speak(data.answer);
        maybeResume();
      })
      .catch(function (e) {
        if (e && e.name === "AbortError") return;
        responseEl.textContent = "Sorry, I couldn’t check your records just now."; statusEl.textContent = IDLE; maybeResume();
      })
      .then(function () { abortCtl = null; if (!speaking()) showStop(false); });
  }
})();
