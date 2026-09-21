/* Althea's interface, shared by every page of the software (Overview, Staff, EMR ...).
 * Builds the landing-page launcher card and chat panel. The element ids are the ones the EMR's own
 * Althea code already uses, so both drive the same interface.  AltheaUI.mount({ chips: [...] }) */
(function () {
  "use strict";
  var MARK = '<svg viewBox="161 142 1032 1001" fill="#3163ed" aria-hidden="true"><path d="M353 179Q390 142 428 177L676 402L420 648L197 406Q162 368 199 331ZM924 177Q963 142 1000 178L1157 332Q1194 368 1158 406L930 648L676 402ZM420 648L676 884L429 1107Q390 1142 353 1105L199 952Q162 915 198 878ZM930 648L1151 878Q1187 915 1150 951L992 1106Q955 1142 917 1107L676 884Z"/></svg>';
  var ARROW = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8h10M9 4l4 4-4 4"/></svg>';
  var SEND = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13V3M4 7l4-4 4 4"/></svg>';
  var CLOSE = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13"/></svg>';
  var MIC = '<svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="7" y="2" width="6" height="10" rx="3"/><path d="M4 9.5a6 6 0 0 0 12 0M10 15.5V18"/></svg>';
  var DEFAULT_CHIPS = ["What's my schedule today?", "Add a new patient", "Summarize my claims", "Which claims are at risk?"];
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function mount(opts) {
    opts = opts || {};
    var existing = document.getElementById("althea-fab");
    if (existing) return existing;
    if (!document.querySelector('link[href*="/static/css/althea.css"]')) {
      var l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/css/althea.css"; document.head.appendChild(l);
    }
    var chips = (opts.chips || DEFAULT_CHIPS).map(function (c) { return '<button type="button" data-q="' + esc(c) + '">' + esc(c) + "</button>"; }).join("");
    var bars = ""; for (var i = 0; i < 24; i++) bars += '<span class="althea-wave-bar"></span>';
    var fab = document.createElement("div");
    fab.id = "althea-fab";
    fab.innerHTML =
      '<div id="althea-timer-badge" class="alt-timer hidden"><i></i><span id="althea-timer-text">0:00</span></div>' +
      '<button id="althea-btn" class="alt-launch" type="button" title="Ask Althea" aria-haspopup="dialog" aria-expanded="false" aria-controls="althea-panel">' +
        '<span class="alt-mark">' + MARK + '</span>' +
        '<span class="alt-tx"><b>Althea, ready to help!</b><span>Ask anything.</span></span>' +
        '<span class="alt-go" aria-hidden="true">' + ARROW + "</span>" +
      "</button>" +
      '<section id="althea-panel" class="alt-panel hidden" role="dialog" aria-label="Althea">' +
        '<header class="alt-head">' +
          '<span class="alt-mark">' + MARK + "</span>" +
          '<div class="alt-id"><b>Althea</b><span id="althea-status" class="alt-status" role="status" aria-live="polite">Ask anything or tap the mic</span></div>' +
          '<button id="althea-stop-btn" class="alt-stop hidden" type="button">Stop</button>' +
          '<button id="althea-close" class="alt-x" type="button" aria-label="Close Althea">' + CLOSE + "</button>" +
        "</header>" +
        '<div id="althea-body" class="alt-body">' +
          '<div id="althea-hello" class="alt-msg alt-a">Hi, I’m Althea. Ask about your schedule, claims or where to go next. Tap the mic to talk, and I’ll keep listening until you tap it again.</div>' +
          '<div id="althea-wave" class="alt-wave" style="display:none">' + bars + "</div>" +
          '<div id="althea-transcript" class="alt-msg alt-u"></div>' +
          '<div id="althea-response" class="alt-msg alt-a" aria-live="polite"></div>' +
        "</div>" +
        '<footer class="alt-foot">' +
          '<div id="althea-chips" class="alt-chips">' + chips + "</div>" +
          '<div class="alt-form">' +
            '<input id="althea-text-input" type="text" autocomplete="off" placeholder="Type a command…" aria-label="Type a command for Althea">' +
            '<span class="alt-mic-wrap"><span id="althea-ring" class="alt-ring"></span><button id="althea-mic" class="alt-mic" type="button" aria-label="Talk to Althea" aria-pressed="false" title="Talk to Althea">' + MIC + "</button></span>" +
            '<button id="althea-text-send" class="alt-send" type="button" aria-label="Send">' + SEND + "</button>" +
          "</div>" +
          '<p class="alt-note">Tap the mic to talk. Althea keeps listening until you tap it again, and only speaks when you talk to her.</p>' +
        "</footer>" +
      "</section>";
    (document.body || document.documentElement).appendChild(fab);

    var panel = document.getElementById("althea-panel");
    if (!SR) panel.classList.add("no-voice");
    /* the greeting only shows until the first exchange */
    var sync = function () { panel.classList.toggle("has-exchange", !!(document.getElementById("althea-transcript").textContent || document.getElementById("althea-response").innerHTML)); };
    var mo = new MutationObserver(sync);
    ["althea-transcript", "althea-response"].forEach(function (id) { mo.observe(document.getElementById(id), { childList: true, characterData: true, subtree: true }); });
    return fab;
  }

  window.AltheaUI = { mount: mount, supportsVoice: !!SR, SR: SR };
})();
