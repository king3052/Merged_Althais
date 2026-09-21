/* Althea's interface, shared by every page of the software (Overview, Staff, EMR ...).
 * Builds the landing-page launcher card and chat panel. The element ids are the ones the EMR's own
 * Althea code already uses, so both drive the same interface.  AltheaUI.mount({ chips: [...] }) */
(function () {
  "use strict";
  var MARK = '<svg viewBox="161 142 1032 1001" fill="#3163ed" aria-hidden="true"><path d="M353 179Q390 142 428 177L676 402L420 648L197 406Q162 368 199 331ZM924 177Q963 142 1000 178L1157 332Q1194 368 1158 406L930 648L676 402ZM420 648L676 884L429 1107Q390 1142 353 1105L199 952Q162 915 198 878ZM930 648L1151 878Q1187 915 1150 951L992 1106Q955 1142 917 1107L676 884Z"/></svg>';
  var ARROW = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8h10M9 4l4 4-4 4"/></svg>';
  var SEND = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13V3M4 7l4-4 4 4"/></svg>';
  var CLOSE = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13"/></svg>';
  var MINIMIZE = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3 12h10"/></svg>';
  var EXPAND = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 10.5L8 6l4.5 4.5"/></svg>';
  var GRIP = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><path d="M1.5 10.5l9-9M1.5 6.5l5-5"/></svg>';
  var MIC = '<svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="7" y="2" width="6" height="10" rx="3"/><path d="M4 9.5a6 6 0 0 0 12 0M10 15.5V18"/></svg>';
  var DEFAULT_CHIPS = ["What's my schedule today?", "Add a new patient", "Scribe a visit", "Which claims are at risk?"];
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
        '<button id="althea-resize" class="alt-resize" type="button" aria-label="Resize Althea (drag, or focus and use Shift + arrow keys)" title="Drag to resize">' + GRIP + "</button>" +
        '<header id="althea-head" class="alt-head" tabindex="0" aria-label="Althea. Drag to move, or use the arrow keys. Double-click to minimize.">' +
          '<span class="alt-mark">' + MARK + "</span>" +
          '<div class="alt-id"><b>Althea</b><span id="althea-status" class="alt-status" role="status" aria-live="polite">Ask anything or tap the mic</span></div>' +
          '<button id="althea-stop-btn" class="alt-stop hidden" type="button">Stop</button>' +
          '<button id="althea-min" class="alt-x" type="button" aria-label="Minimize Althea" aria-pressed="false" title="Minimize">' + MINIMIZE + "</button>" +
          '<button id="althea-close" class="alt-x" type="button" aria-label="Close Althea">' + CLOSE + "</button>" +
        "</header>" +
        '<div id="althea-body" class="alt-body">' +
          '<div id="althea-hello" class="alt-msg alt-a">Hi, I’m Althea. Ask about your schedule, claims or where to go next. Tap the mic to talk, and I’ll keep listening until you tap it again.</div>' +
          '<div id="althea-wave" class="alt-wave" style="display:none">' + bars + "</div>" +
          '<div id="althea-transcript" class="alt-msg alt-u"></div>' +
          '<div id="althea-response" class="alt-msg alt-a" aria-live="polite"></div>' +
        "</div>" +
        '<footer class="alt-foot">' +
          '<div id="althea-actions" class="alt-actions hidden" role="group" aria-label="Next step"></div>' +
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
    setupWindow(panel);
    /* the greeting only shows until the first exchange */
    var sync = function () { panel.classList.toggle("has-exchange", !!(document.getElementById("althea-transcript").textContent || document.getElementById("althea-response").innerHTML)); };
    var mo = new MutationObserver(sync);
    ["althea-transcript", "althea-response"].forEach(function (id) { mo.observe(document.getElementById(id), { childList: true, characterData: true, subtree: true }); });
    return fab;
  }

  /* ---------- move, resize and minimize ---------- */
  var KEY = "althea.ui.v1", MIN_W = 340, MIN_BODY = 110;   /* the smallest width, and the least room for the conversation itself */
  function readState() { try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { return {}; } }
  function writeState(patch) { try { var s = readState(); for (var k in patch) s[k] = patch[k]; localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) {} }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function setupWindow(panel) {
    var head = document.getElementById("althea-head"), minBtn = document.getElementById("althea-min"), grip = document.getElementById("althea-resize");
    var foot = panel.querySelector(".alt-foot");
    /* the panel can never be smaller than its own controls: header + chips/buttons/input + a little conversation */
    function minH() { return head.offsetHeight + foot.offsetHeight + MIN_BODY; }
    function applySize(w, h, maxW, maxH) {               /* set the width first: the chips wrap, so the footer's height depends on it */
      w = clamp(w, MIN_W, Math.max(MIN_W, maxW)); panel.style.width = w + "px";
      var lo = minH(); h = clamp(h, lo, Math.max(lo, maxH)); panel.style.height = h + "px";
      return { w: w, h: h };
    }
    var free = false;                                    /* true once the panel has its own left/top (it was moved or resized) */
    var small = function () { return window.innerWidth <= 480; };       /* phones: the panel is already full width, so no dragging */

    /* switch from "stuck to the bottom-right corner" to explicit coordinates, keeping it exactly where it is */
    function freeUp() {
      if (free) return;
      var r = panel.getBoundingClientRect();
      panel.style.position = "fixed"; panel.style.left = r.left + "px"; panel.style.top = r.top + "px";
      panel.style.right = "auto"; panel.style.bottom = "auto"; panel.style.width = r.width + "px"; panel.style.height = r.height + "px"; panel.style.maxHeight = "none";
      free = true;
    }
    function place(x, y) {                               /* keep the WHOLE panel inside the window, so its buttons are always reachable */
      var r = panel.getBoundingClientRect(), m = 8;
      panel.style.left = clamp(x, m, Math.max(m, window.innerWidth - r.width - m)) + "px";
      panel.style.top = clamp(y, m, Math.max(m, window.innerHeight - r.height - m)) + "px";
    }
    function save() {
      var r = panel.getBoundingClientRect(), patch = { x: Math.round(r.left), y: Math.round(r.top) };
      if (!panel.classList.contains("is-min")) { patch.w = Math.round(r.width); patch.h = Math.round(r.height); }   /* keep the normal size while minimized */
      writeState(patch);
    }

    /* restore the saved position and size, and re-fit them if the window is smaller now */
    function restore() {
      var s = readState();
      panel.classList.toggle("is-min", !!s.min); minBtn.setAttribute("aria-pressed", String(!!s.min));
      minBtn.setAttribute("aria-label", s.min ? "Expand Althea" : "Minimize Althea"); minBtn.title = s.min ? "Expand" : "Minimize";
      if (small() || typeof s.x !== "number" || typeof s.y !== "number") return;
      panel.style.position = "fixed"; panel.style.right = "auto"; panel.style.bottom = "auto"; panel.style.maxHeight = "none";
      if (!s.min) applySize(s.w || 372, s.h || 480, window.innerWidth - 16, window.innerHeight - 16); else panel.style.width = clamp(s.w || 372, MIN_W, window.innerWidth - 16) + "px";
      free = true;
      place(s.x, s.y);
    }
    restore();

    /* grabbing the logo must move the panel, not start the browser's own "drag this picture" (which cancels our drag) */
    head.addEventListener("dragstart", function (e) { e.preventDefault(); });
    grip.addEventListener("dragstart", function (e) { e.preventDefault(); });

    /* drag by the header (mouse, touch or pen) */
    var drag = null;
    head.addEventListener("pointerdown", function (e) {
      if (small() || e.button > 0 || e.target.closest("button")) return;
      var r = panel.getBoundingClientRect();
      drag = { sx: e.clientX, sy: e.clientY, dx: e.clientX - r.left, dy: e.clientY - r.top, moved: false };
      try { head.setPointerCapture(e.pointerId); } catch (x) {}
      e.preventDefault();                                    /* we are handling this gesture: no browser text-selection or picture-drag on the logo */
    });
    head.addEventListener("pointermove", function (e) {
      if (!drag) return;
      if (!drag.moved) {                                    /* a plain click (or double-click) is not a drag */
        if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) < 4) return;
        drag.moved = true; freeUp(); panel.classList.add("is-moving");
      }
      place(e.clientX - drag.dx, e.clientY - drag.dy);
    });
    function endDrag() { if (!drag) return; var moved = drag.moved; drag = null; panel.classList.remove("is-moving"); if (moved) save(); }
    head.addEventListener("pointerup", endDrag); head.addEventListener("pointercancel", endDrag);
    /* the same with the keyboard: arrow keys move the panel */
    head.addEventListener("keydown", function (e) {
      if (small() || e.target !== head) return;
      var step = e.shiftKey ? 60 : 20, dx = 0, dy = 0;
      if (e.key === "ArrowLeft") dx = -step; else if (e.key === "ArrowRight") dx = step; else if (e.key === "ArrowUp") dy = -step; else if (e.key === "ArrowDown") dy = step; else return;
      e.preventDefault(); freeUp(); var r = panel.getBoundingClientRect(); place(r.left + dx, r.top + dy); save();
    });

    /* resize from the top-left corner (the panel is anchored bottom-right, so that is the natural corner) */
    var rs = null;
    grip.addEventListener("pointerdown", function (e) {
      if (small() || e.button > 0 || panel.classList.contains("is-min")) return;
      var r = panel.getBoundingClientRect();
      rs = { x: e.clientX, y: e.clientY, l: r.left, t: r.top, w: r.width, h: r.height, right: r.right, bottom: r.bottom, moved: false };
      try { grip.setPointerCapture(e.pointerId); } catch (x) {}
      e.preventDefault();
    });
    grip.addEventListener("pointermove", function (e) {
      if (!rs) return;
      if (!rs.moved) { if (Math.abs(e.clientX - rs.x) + Math.abs(e.clientY - rs.y) < 4) return; rs.moved = true; freeUp(); panel.classList.add("is-moving"); }
      var z = applySize(rs.w - (e.clientX - rs.x), rs.h - (e.clientY - rs.y), Math.min(720, rs.right), Math.min(900, rs.bottom));
      panel.style.left = (rs.right - z.w) + "px"; panel.style.top = (rs.bottom - z.h) + "px";
    });
    function endResize() { if (!rs) return; var moved = rs.moved; rs = null; panel.classList.remove("is-moving"); if (moved) save(); }
    grip.addEventListener("pointerup", endResize); grip.addEventListener("pointercancel", endResize);
    grip.addEventListener("keydown", function (e) {                    /* Shift + arrows resize from the keyboard */
      if (small() || !e.shiftKey || ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].indexOf(e.key) < 0) return;
      e.preventDefault(); freeUp(); var r = panel.getBoundingClientRect(), d = 30;
      var z = applySize(r.width + (e.key === "ArrowLeft" ? d : e.key === "ArrowRight" ? -d : 0), r.height + (e.key === "ArrowUp" ? d : e.key === "ArrowDown" ? -d : 0), Math.min(720, r.right), Math.min(900, r.bottom));
      panel.style.left = (r.right - z.w) + "px"; panel.style.top = (r.bottom - z.h) + "px"; save();
    });
    grip.addEventListener("dblclick", function () { reset(); });         /* double-click the corner: back to the default size and place */

    /* minimize: keeps the header, the next-step buttons and the mic, hides the rest so you can see the page */
    function setMin(on) {
      var normal = panel.getBoundingClientRect();          /* measure BEFORE switching styles, so the normal size is what gets remembered */
      panel.classList.toggle("is-min", on); minBtn.setAttribute("aria-pressed", String(on));
      minBtn.setAttribute("aria-label", on ? "Expand Althea" : "Minimize Althea"); minBtn.title = on ? "Expand" : "Minimize";
      minBtn.innerHTML = on ? EXPAND : MINIMIZE;
      if (on) {
        if (free) { writeState({ min: true, h: Math.round(normal.height), w: Math.round(normal.width) }); panel.style.height = "auto"; }
        else writeState({ min: true });
      } else {
        writeState({ min: false });
        if (free) { var s = readState(); applySize(s.w || 372, s.h || 480, window.innerWidth - 16, window.innerHeight - 16); place(parseFloat(panel.style.left) || 8, parseFloat(panel.style.top) || 8); }
      }
    }
    minBtn.addEventListener("click", function () { setMin(!panel.classList.contains("is-min")); });
    head.addEventListener("dblclick", function (e) { if (!e.target.closest("button")) setMin(!panel.classList.contains("is-min")); });
    if (panel.classList.contains("is-min")) minBtn.innerHTML = EXPAND;

    function reset() {
      free = false; ["position", "left", "top", "right", "bottom", "width", "height", "maxHeight"].forEach(function (k) { panel.style[k] = ""; });
      try { var s = readState(); delete s.x; delete s.y; delete s.w; delete s.h; localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) {}
    }
    /* if the window shrinks, pull the panel back into view */
    window.addEventListener("resize", function () {
      if (small()) { if (free) reset(); return; }
      if (!free) return;
      var r = panel.getBoundingClientRect();
      if (!panel.classList.contains("is-min")) applySize(Math.min(r.width, window.innerWidth - 16), Math.min(r.height, window.innerHeight - 16), window.innerWidth - 16, window.innerHeight - 16);
      place(r.left, r.top);
    });

    /* if something appears in the panel that needs room (next-step buttons, a longer status), grow it upward rather than cut it off */
    function ensureFits() {
      if (!free || small() || panel.classList.contains("is-min") || panel.classList.contains("hidden")) return;
      var r = panel.getBoundingClientRect(), need = minH();
      if (r.height >= need - 1) return;
      panel.style.height = need + "px"; place(r.left, r.bottom - need); save();
    }
    if (window.ResizeObserver) { var ro = new ResizeObserver(ensureFits); ro.observe(foot); ro.observe(head); }
  }

  window.AltheaUI = { mount: mount, supportsVoice: !!SR, SR: SR };
})();
