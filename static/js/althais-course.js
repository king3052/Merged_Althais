/* Althais Training course player (Staff Portal > Training > Althais Training).
 *
 *   AlthaisCourse.mount(container, { api, esc, fmtDate, toast, onChange })
 *
 * Content, the knowledge check and grading all come from the server (althais_training.py): this only presents them.
 * Card kinds:
 *   text        title, body, bullets
 *   recap       takeaways, shown as a checklist
 *   video       the demo video with chapters; captions follow playback, guided mode pauses at each chapter
 *   hotspots    a real Althais screen with numbered hotspots and a close-up of the one you're looking at
 *   workspace   tap each area
 *   sequence    put the steps of a visit in order
 *   spot        find the errors in a note, claim or registration
 *   scenario    a run of what-would-you-do situations
 *   althea_sim  a practice Althea with sample answers
 *   check       one practice question (not graded)
 *   althea      an example conversation
 * Practice exercises must be finished before Next (unless the lesson is already done). Only the knowledge check is graded.
 */
(function () {
  "use strict";

  var DK = "html[data-theme-resolved='dark'] ";
  var CSS = [
    ".ac-shot{position:relative;border:1px solid #e4e6eb;border-radius:3px;overflow:hidden;background:#f4f5f7;margin:0 auto}",
    ".ac-shot img{display:block;width:100%;height:auto;user-select:none}",
    ".ac-pin{position:absolute;transform:translate(-50%,-50%);width:26px;height:26px;border-radius:50%;border:2px solid #fff;",
    "  background:var(--brand);color:var(--brand-on,#fff);font:700 12px/22px system-ui,sans-serif;text-align:center;cursor:pointer;",
    "  box-shadow:0 2px 8px rgba(0,0,0,.35);transition:transform .15s}",
    ".ac-pin:hover{transform:translate(-50%,-50%) scale(1.15)}",
    ".ac-pin.is-seen{background:#0c8a4f}",
    ".ac-pin.is-cur{transform:translate(-50%,-50%) scale(1.25);background:#fff;color:var(--brand);border-color:var(--brand)}",
    ".ac-pin:not(.is-seen):not(.is-cur)::after{content:'';position:absolute;inset:-6px;border-radius:50%;border:2px solid var(--brand);animation:acPulse 1.8s ease-out infinite}",
    "@keyframes acPulse{0%{opacity:.8;transform:scale(.7)}100%{opacity:0;transform:scale(1.5)}}",
    ".ac-lens{height:150px;border:1px solid #e4e6eb;border-radius:3px;background-repeat:no-repeat;background-color:#fff;position:relative;overflow:hidden}",
    ".ac-lens::after{content:'';position:absolute;width:36px;height:36px;border:2px solid var(--brand);border-radius:50%;pointer-events:none;",
    "  left:var(--lx);top:var(--ly);transform:translate(-50%,-50%)}",
    ".ac-video{position:relative;background:#000;border-radius:3px;overflow:hidden}",
    ".ac-video video{display:block;width:100%;height:auto;cursor:pointer}",
    ".ac-cap{position:absolute;left:0;right:0;bottom:0;padding:26px 14px 10px;background:linear-gradient(transparent,rgba(0,0,0,.82));color:#fff;pointer-events:none}",
    ".ac-cap b{display:block;font-size:13px}.ac-cap span{display:block;font-size:12.5px;opacity:.92;line-height:1.45;margin-top:2px}",
    ".ac-track{position:relative;height:6px;background:#e4e6eb;border-radius:3px;margin:10px 0 4px;cursor:pointer}",
    ".ac-track i{position:absolute;left:0;top:0;bottom:0;background:var(--brand);border-radius:3px}",
    ".ac-track u{position:absolute;top:-3px;width:3px;height:12px;background:#2c313b;border-radius:1px;text-decoration:none}",
    ".ac-chap{display:flex;gap:10px;align-items:flex-start;width:100%;text-align:left;padding:8px 10px;border:1px solid #e4e6eb;border-radius:3px;font-size:12.5px;background:#fff}",
    ".ac-chap:hover{background:#f4f5f7}.ac-chap.is-cur{border-color:var(--brand);background:var(--brand-50,#f4f7ff)}",
    ".ac-tag{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:1px 6px;border-radius:2px;background:var(--brand-100);color:var(--brand-text-strong);margin-left:6px;vertical-align:1px}",
    ".ac-seg{display:inline-flex;border:1px solid #e4e6eb;border-radius:3px;overflow:hidden}.ac-seg button{font-size:11.5px;padding:4px 9px;background:#fff}",
    ".ac-seg button.is-on{background:var(--brand);color:var(--brand-on,#fff)}",
    ".ac-step{display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid #e4e6eb;border-radius:3px;font-size:13px;background:#fff;width:100%;text-align:left}",
    ".ac-step.is-bad{animation:acShake .35s;border-color:#c83838;background:#fdecec}",
    "@keyframes acShake{0%,100%{transform:none}25%{transform:translateX(-5px)}75%{transform:translateX(5px)}}",
    ".ac-num{width:22px;height:22px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:700;background:#0c8a4f;color:#fff}",
    ".ac-doc{border:1px solid #e4e6eb;border-radius:3px;background:#fff;overflow:hidden}",
    ".ac-doc-h{padding:8px 14px;background:#f4f5f7;border-bottom:1px solid #e4e6eb;font-size:12px;font-weight:700;color:#2c313b;letter-spacing:.02em}",
    ".ac-row{display:grid;grid-template-columns:minmax(110px,190px) 1fr;gap:12px;width:100%;text-align:left;padding:10px 14px;border-bottom:1px solid #eceef2;font-size:13px;cursor:pointer}",
    ".ac-row:last-child{border-bottom:0}.ac-row:hover{background:#fafbfc}",
    ".ac-row .l{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#6b7280;font-weight:600;padding-top:1px}",
    ".ac-row.is-issue{background:#fdecec;box-shadow:inset 3px 0 #c83838;cursor:default}.ac-row.is-ok{background:#eaf6ef;box-shadow:inset 3px 0 #0c8a4f;cursor:default}",
    ".ac-row .why{display:block;font-size:12px;margin-top:4px;line-height:1.45}",
    ".ac-chat{border:1px solid #e4e6eb;border-radius:12px;background:#fff;overflow:hidden}",
    ".ac-chat-h{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #eceef2;font-size:13px;font-weight:600}",
    ".ac-orb{width:26px;height:26px;border-radius:50%;background:radial-gradient(circle at 30% 30%,rgba(255,255,255,.6),transparent 45%),var(--brand);color:#fff;font:700 12px/26px system-ui;text-align:center}",
    ".ac-msgs{height:250px;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:8px;background:#fafbfc}",
    ".ac-me{align-self:flex-end;max-width:80%;padding:7px 11px;border-radius:12px 12px 2px 12px;background:var(--brand);color:var(--brand-on,#fff);font-size:13px}",
    ".ac-her{align-self:flex-start;max-width:85%;padding:7px 11px;border-radius:12px 12px 12px 2px;background:#fff;border:1px solid #e4e6eb;font-size:13px;color:#1a1d24;line-height:1.5}",
    ".ac-dots span{display:inline-block;width:6px;height:6px;margin:0 2px;border-radius:50%;background:#9aa0ab;animation:acDot 1s infinite}",
    ".ac-dots span:nth-child(2){animation-delay:.15s}.ac-dots span:nth-child(3){animation-delay:.3s}",
    "@keyframes acDot{0%,80%,100%{opacity:.3}40%{opacity:1}}",
    ".ac-chips{display:flex;flex-wrap:wrap;gap:6px;padding:10px 14px 0}.ac-chips button{font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid #e4e6eb;background:#fff}",
    ".ac-chips button:hover{border-color:var(--brand);color:var(--brand-text-strong)}.ac-chips button.is-used{opacity:.5}",
    ".ac-recap li{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid #eceef2;font-size:13.5px}.ac-recap li:last-child{border-bottom:0}",
    "@media (max-width:1023px){.ac-list:not(.is-open){display:none}.ac-list.is-open{margin-top:6px}}",
    ".ac-take{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border-radius:4px;background:var(--brand-50,#f4f7ff);border:1px solid var(--brand-100)}",
    ".ac-take-i{width:28px;height:28px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;background:var(--brand);color:var(--brand-on,#fff);font-size:13px}",
    ".ac-tutor{border-top:1px dashed #d7dae0;padding-top:14px}",
    ".ac-tbtn{font-size:12.5px;padding:5px 11px;border-radius:999px;border:1px solid #d7dae0;background:#fff;color:#2c313b;font-weight:500}",
    ".ac-tbtn:hover{border-color:var(--brand);color:var(--brand-text-strong)}.ac-tbtn.is-on{background:var(--brand-100);border-color:var(--brand);color:var(--brand-text-strong)}",
    ".ac-tans{margin-top:10px;padding:12px 14px;border-radius:10px 10px 10px 2px;background:#f4f5f7;border:1px solid #e4e6eb;font-size:14px;line-height:1.55;color:#1a1d24}",
    ".ac-why{padding:10px 12px;border:1px solid #e4e6eb;border-radius:3px;background:#fff}.ac-why.is-ok{border-color:#0c8a4f;background:#eaf6ef}.ac-why.is-bad{border-color:rgba(200,56,56,.4);background:#fdecec}",
    ".ac-meter{height:4px;border-radius:2px;background:#e4e6eb;overflow:hidden}.ac-meter i{display:block;height:100%;background:#0c8a4f;transition:width .3s}",
    DK + ".ac-shot," + DK + ".ac-lens{border-color:#2c313b}",
    DK + ".ac-chap," + DK + ".ac-step," + DK + ".ac-doc," + DK + ".ac-chat," + DK + ".ac-her," + DK + ".ac-chips button," + DK + ".ac-seg button{background:#1a1d24;border-color:#2c313b;color:#d6dae5}",
    DK + ".ac-doc-h," + DK + ".ac-msgs{background:#14181f;border-color:#2c313b;color:#d6dae5}",
    DK + ".ac-row{border-color:#2c313b}" + DK + ".ac-row:hover{background:#20242c}",
    DK + ".ac-row.is-issue{background:rgba(200,56,56,.16)}" + DK + ".ac-row.is-ok{background:rgba(12,138,79,.16)}",
    DK + ".ac-recap li," + DK + ".ac-chat-h{border-color:#2c313b}",
    DK + ".ac-track," + DK + ".ac-meter{background:#2c313b}" + DK + ".ac-track u{background:#d6dae5}",
    DK + ".ac-chap.is-cur{background:var(--brand-dark-tint)}" + DK + ".ac-take{background:var(--brand-dark-tint);border-color:#2c313b}" + DK + ".ac-tbtn," + DK + ".ac-why{background:#1a1d24;border-color:#2c313b;color:#d6dae5}",
    DK + ".ac-tans{background:#14181f;border-color:#2c313b;color:#d6dae5}" + DK + ".ac-why.is-ok{background:rgba(12,138,79,.16)}" + DK + ".ac-why.is-bad{background:rgba(200,56,56,.16)}" + DK + ".ac-tutor{border-color:#2c313b}" + DK + ".ac-seg button.is-on{background:var(--brand);color:var(--brand-on,#fff)}"
  ].join("\n");

  function mount(el, opt) {
    var api = opt.api, esc = opt.esc, C = null, mi = 0, ci = 0, st = {}, quiz = null, result = null, video = null, outline = false, CK = {}, TU = {};

    if (!document.getElementById("ac-css")) { var s = document.createElement("style"); s.id = "ac-css"; s.textContent = CSS; document.head.appendChild(s); }

    function load() {
      return api("GET", "/api/portal/course/althais").then(function (c) {
        C = c; C.practice = C.practice || {}; C.visited = C.visited || []; C.fails = C.fails || {};
        var start = opt.start ? c.modules.findIndex(function (m) { return m.key === opt.start; }) : -1;
        if (start >= 0) { mi = start; ci = Math.max(0, Math.min(Number(opt.card) || 0, c.modules[start].cards.length - 1)); return render(); }
        if (c.record && c.record.completed) { mi = c.modules.length - 1; }
        else { var i = c.modules.findIndex(function (m) { return isLesson(m) && c.progress.indexOf(m.key) < 0; }); mi = i < 0 ? c.modules.length - 2 : i; }
        ci = 0; render();
      }).catch(function (e) { el.innerHTML = '<div class="card p-5 text-[13px] text-risk-600">' + esc(e.message) + "</div>"; });
    }

    function key() { return mi + ":" + ci; }
    function S() { var k = key(); return st[k] = st[k] || {}; }
    function cur() { var m = C.modules[mi]; return m && m.cards[ci]; }
    function done(k) { return C.progress.indexOf(k) >= 0 || (k === "quiz" && !!(C.record && C.record.completed)); }
    function seen(k) { return C.visited.indexOf(k) >= 0 || done(k); }
    function isLesson(m) { return m.key !== "quiz" && m.key !== "complete"; }
    function lessons() { return C.modules.filter(isLesson); }
    function allMastered() { return lessons().every(function (m) { return done(m.key); }) || !!(C.record && C.record.completed); }
    function steps(m) { return m.cards.length + (m.check ? 1 : 0); }
    function mins() { return C.modules.reduce(function (a, m) { return a + (m.minutes || 0); }, 0); }
    function minsLeft() { return C.modules.reduce(function (a, m) { return a + (done(m.key) ? 0 : (m.minutes || 0)); }, 0); }
    function shuffle(a) { a = a.slice(); for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; } return a; }
    function meter(n, of, label) {
      return '<div class="mt-3 flex items-center gap-3"><div class="ac-meter flex-1"><i style="width:' + Math.round(100 * n / Math.max(1, of)) + '%"></i></div>' +
             '<span class="text-[11.5px] text-ink-500 whitespace-nowrap">' + n + " of " + of + " " + label + "</span></div>";
    }
    function yours(on) { return on ? '<span class="ac-tag">Your Part</span>' : ""; }

    /* ---------- the outline: the basics everyone takes, then this person's role path ---------- */
    function nav() {
      var L = lessons(), count = L.filter(function (m) { return done(m.key); }).length, pct = Math.round(100 * count / L.length), left = minsLeft();
      var groups = [["basics", "The Basics · Everyone"], ["role", "Your Path · " + (C.path || "Your Role")], ["finish", "Finish"]];
      function item(m) {
        var i = C.modules.indexOf(m), d = done(m.key), v = seen(m.key), isCur = i === mi;
        var open = m.key === "complete" ? !!(C.record && C.record.completed) : m.key === "quiz" ? allMastered() : (i <= mi || v || C.modules.slice(0, i).filter(isLesson).every(function (x) { return seen(x.key); }));
        var mark = d ? '<span class="w-4 text-center text-ok-600" title="Mastered">✓</span>' : v && m.check ? '<span class="w-4 text-center text-warn-600" title="Read, check not passed yet">◐</span>' : '<span class="w-4 text-center text-ink-400">' + (L.indexOf(m) + 1 || "") + "</span>";
        return '<button type="button" class="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-sm text-[13px] ' + (isCur ? "bg-med-100 font-semibold text-ink-900" : "text-ink-700 hover:bg-shell") + '" data-mod="' + i + '"' + (open ? "" : ' disabled style="opacity:.45"') + ">" +
          mark + '<span class="min-w-0">' + esc(m.title) + "</span>" + (m.minutes ? '<span class="ml-auto text-[10.5px] text-ink-400 font-normal whitespace-nowrap">' + m.minutes + " min</span>" : "") + "</button>";
      }
      return '<div class="card p-3 lg:sticky lg:top-[72px]"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold px-1">Althais Training · v' + esc(C.version) + "</div>" +
        '<div class="text-[12px] text-ink-500 px-1 mb-2">' + count + " of " + L.length + " lessons mastered" + (left ? " · about " + left + " min left" : "") + "</div>" +
        '<div class="ac-meter mb-2 mx-1"><i style="width:' + pct + '%"></i></div>' +
        '<button type="button" class="lg:hidden w-full flex justify-between items-center px-2 py-1.5 rounded-sm bg-med-100 text-[12.5px] font-semibold text-ink-900" data-outline>' +
        "<span>" + esc(C.modules[mi].title) + '</span><span class="text-ink-500 font-normal">' + (outline ? "Hide ▴" : "All lessons ▾") + "</span></button>" +
        '<div class="ac-list' + (outline ? " is-open" : "") + '">' + groups.map(function (g) {
          var ms = C.modules.filter(function (m) { return m.path === g[0]; });
          return ms.length ? '<div class="text-[10.5px] uppercase tracking-wider text-ink-400 font-semibold px-2 pt-2.5 pb-1">' + esc(g[1]) + "</div>" + ms.map(item).join("") : "";
        }).join("") + '<div class="text-[11px] text-ink-400 px-2 pt-2 leading-snug">✓ mastered · ◐ read, check to pass</div></div></div>';
    }

    /* ---------- cards ---------- */
    function head(c) {
      return '<h3 class="text-[22px] leading-tight font-semibold text-ink-900">' + esc(c.title) + "</h3>" + (c.body ? '<p class="text-[15.5px] text-ink-700 mt-3 leading-relaxed">' + esc(c.body) + "</p>" : "") +
        (c.bullets.length && c.kind !== "recap" ? '<ul class="mt-4 space-y-2">' + c.bullets.map(function (b) { return '<li class="flex gap-2.5 text-[15px] text-ink-700 leading-relaxed"><span class="text-med-600 font-bold">•</span><span>' + esc(b) + "</span></li>"; }).join("") + "</ul>" : "") +
        (c.image ? '<figure class="mt-5"><img src="' + esc(c.image) + '" alt="" class="rounded-sm border border-line max-h-[340px] w-auto">' + (c.imageCaption ? '<figcaption class="text-[12px] text-ink-500 mt-1.5">' + esc(c.imageCaption) + "</figcaption>" : "") + "</figure>" : "");
    }
    function chapTime(t) { return Math.floor(t / 60) + ":" + ("0" + Math.floor(t % 60)).slice(-2); }

    var KINDS = {
      recap: function (c) {
        return '<ul class="ac-recap mt-4 px-4 py-2 rounded-sm border border-line bg-shell">' + c.bullets.map(function (b) {
          return '<li><span class="ac-num">✓</span><span class="text-ink-800">' + esc(b) + "</span></li>"; }).join("") + "</ul>";
      },

      video: function (c) {
        var v = S(), ch = c.chapters, n = Object.keys(v.visited || {}).length, i0 = v.idx || 0;
        return '<div class="mt-4 ac-video"><video id="ac-vid" src="' + esc(c.src) + '" playsinline muted preload="metadata"></video>' +
          '<div class="ac-cap" id="ac-cap"><b>' + esc(ch[i0].title) + (ch[i0].yours ? " · Your Part" : "") + "</b><span>" + esc(ch[i0].text) + "</span></div></div>" +
          '<div class="ac-track" id="ac-track"><i id="ac-prog" style="width:0"></i>' + ch.map(function (x, i) { return i ? '<u data-at="' + x.t + '" title="' + esc(x.title) + '"></u>' : ""; }).join("") + "</div>" +
          '<div class="flex items-center gap-2 flex-wrap mt-2"><button type="button" class="btn btn-primary" id="ac-play">▶ Play</button>' +
          '<button type="button" class="btn btn-line" data-vid-step="-1">‹ Chapter</button><button type="button" class="btn btn-line" data-vid-step="1">Chapter ›</button>' +
          '<span class="ac-seg ml-1" aria-label="Playback speed">' + [0.5, 0.75, 1].map(function (r) { return '<button type="button" data-rate="' + r + '"' + ((v.rate || 0.75) === r ? ' class="is-on"' : "") + ">" + r + "×</button>"; }).join("") + "</span>" +
          '<label class="ml-auto text-[12px] text-ink-600 flex items-center gap-1.5"><input type="checkbox" id="ac-guided"' + (v.guided === false ? "" : " checked") + "> Pause at each chapter</label></div>" +
          '<div class="mt-3 grid sm:grid-cols-2 gap-1.5">' + ch.map(function (x, i) {
            return '<button type="button" class="ac-chap' + (v.idx === i ? " is-cur" : "") + '" data-chap="' + i + '"><span class="text-ink-400 font-mono text-[11.5px] pt-px w-9 flex-shrink-0">' + chapTime(x.t) +
              '</span><span class="min-w-0"><span class="font-semibold text-ink-900">' + esc(x.title) + "</span>" + yours(x.yours) +
              '<span class="block ac-w text-ink-500 text-[11.5px] mt-0.5">' + ((v.visited || {})[i] ? "✓ Watched" : "Not watched yet") + "</span></span></button>";
          }).join("") + "</div>" + '<div id="ac-vmeter">' + meter(n, ch.length, "chapters watched") + "</div>" +
          (c.caption ? '<p class="text-[11.5px] text-ink-400 mt-2">' + esc(c.caption) + " The video has no sound; the captions follow along.</p>" : "");
      },

      hotspots: function (c) {
        var v = S(), seen = v.seen || {}, i = v.cur, tall = c.h > c.w, spot = i != null ? c.spots[i] : null;
        var lens = spot ? '<div class="ac-lens ' + (tall ? "mb-3" : "w-full sm:w-[240px] flex-shrink-0") + '" style="background-image:url(\'' + esc(c.image) + "');background-size:" + (tall ? 260 : 320) +
          "%;background-position:" + spot.x + "% " + spot.y + "%;--lx:" + spot.x + "%;--ly:" + spot.y + '%" aria-hidden="true"></div>' : "";
        var text = '<div class="min-w-0 flex-1">' + (spot
            ? '<div class="text-[11px] text-ink-500 font-semibold uppercase tracking-wider">' + (i + 1) + " of " + c.spots.length + '</div><div class="text-[14.5px] font-semibold text-ink-900 mt-0.5">' + esc(spot.title) + "</div>" +
              '<p class="text-[13px] text-ink-700 mt-1.5 leading-relaxed">' + esc(spot.text) + "</p>"
            : '<div class="text-[14px] font-semibold text-ink-900">Explore this screen</div><p class="text-[13px] text-ink-600 mt-1 leading-relaxed">Tap a pulsing number, or press Start Tour to go through them in order (the arrow keys work too). Each one opens a close-up.</p>') +
          '<div class="flex gap-2 mt-4"><button type="button" class="btn btn-line" data-spot-step="-1"' + (i == null || i === 0 ? " disabled" : "") + ' aria-label="Previous hotspot">‹</button>' +
          '<button type="button" class="btn btn-primary ' + (tall ? "flex-1 justify-center" : "") + '" data-spot-step="1"' + (i === c.spots.length - 1 ? " disabled" : "") + ">" + (i == null ? "Start Tour" : "Next Hotspot") + " ›</button></div>" +
          meter(Object.keys(seen).length, c.spots.length, "explored") + "</div>";
        return '<div class="mt-4 grid gap-4 ' + (tall ? "md:grid-cols-[minmax(0,1fr)_280px]" : "") + ' items-start">' +
          '<div class="ac-shot" style="max-width:' + (tall ? 560 : c.w) + 'px;width:100%"><img src="' + esc(c.image) + '" alt="' + esc(c.title) + '" width="' + c.w + '" height="' + c.h + '">' +
          c.spots.map(function (p, j) {
            return '<button type="button" class="ac-pin' + (seen[j] ? " is-seen" : "") + (i === j ? " is-cur" : "") + '" style="left:' + p.x + "%;top:" + p.y + '%" data-spot="' + j + '" aria-label="' + esc((j + 1) + ". " + p.title) + '">' + (j + 1) + "</button>";
          }).join("") + "</div>" +
          '<div class="rounded-sm border border-line bg-shell p-4 ' + (tall ? "md:sticky md:top-[80px]" : "flex flex-col sm:flex-row gap-3 sm:gap-5 sm:min-h-[180px]") + '">' + lens + text + "</div></div>" +
          (c.caption ? '<p class="text-[11.5px] text-ink-400 mt-2">' + esc(c.caption) + "</p>" : "");
      },

      workspace: function (c) {
        var v = S(), seen = v.seen || {};
        return '<div class="mt-4 grid sm:grid-cols-[210px_1fr] gap-3"><div class="rounded-sm border border-line overflow-hidden">' +
          c.tour.map(function (t, i) { return '<button type="button" data-tour="' + i + '" class="w-full text-left px-3 py-2 text-[12.5px] border-b border-lineSoft ' + (v.cur === i ? "bg-med-100 font-semibold" : "hover:bg-shell") + '">' + (seen[i] ? '<span class="text-ok-600 mr-1">✓</span>' : "") + esc(t.name) + "</button>"; }).join("") +
          '</div><div class="rounded-sm border border-line p-4 bg-shell min-h-[140px]">' + (v.cur != null ? '<div class="text-[14.5px] font-semibold text-ink-900">' + esc(c.tour[v.cur].name) + '</div><p class="text-[13px] text-ink-700 mt-1.5 leading-relaxed">' + esc(c.tour[v.cur].text) + "</p>"
            : '<p class="text-[13px] text-ink-500">Tap an area on the left.</p>') + meter(Object.keys(seen).length, c.tour.length, "explored") + "</div></div>";
      },

      sequence: function (c) {
        var v = S();
        if (!v.pool) { v.pool = shuffle(c.steps.map(function (x, i) { return i; })); v.order = []; v.tries = 0; }
        var finished = v.order.length === c.steps.length;
        return '<div class="mt-4 grid md:grid-cols-2 gap-4"><div><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold mb-2">' + (finished ? "All placed" : "Tap the step that comes next") + '</div><div class="space-y-1.5">' +
          v.pool.filter(function (i) { return v.order.indexOf(i) < 0; }).map(function (i) {
            return '<button type="button" class="ac-step hover:bg-shell' + (v.bad === i ? " is-bad" : "") + '" data-seq="' + i + '"><span class="text-ink-400">⋮⋮</span><span>' + esc(c.steps[i].label) + "</span></button>";
          }).join("") + "</div>" + (finished ? '<div class="p-3 rounded-sm bg-ok-100 text-ok-600 text-[13px] font-semibold">✓ ' + (v.tries ? "Done, with " + v.tries + " wrong tr" + (v.tries === 1 ? "y" : "ies") + "." : "Perfect, first try.") + "</div>" : "") +
          (v.hint ? '<p class="text-[12.5px] text-risk-600 mt-2">' + esc(v.hint) + "</p>" : "") + "</div>" +
          '<div><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold mb-2">The visit, in order</div><ol class="space-y-1.5">' +
          c.steps.map(function (s, i) {
            var placed = i < v.order.length;
            return '<li class="ac-step' + (placed ? "" : " opacity-50") + '"' + (placed ? "" : ' style="border-style:dashed"') + '><span class="ac-num"' + (placed ? "" : ' style="background:#c7cad1"') + ">" + (i + 1) + "</span><span>" +
              (placed ? esc(c.steps[v.order[i]].label) + yours(c.steps[v.order[i]].yours) : "&nbsp;") + "</span></li>";
          }).join("") + "</ol>" + (v.order.length ? '<button type="button" class="text-[12px] text-ink-500 underline mt-2" data-seq-reset>Start over</button>' : "") + "</div></div>";
      },

      spot: function (c) {
        var v = S(), open = v.open || {}, issues = c.fields.filter(function (f) { return f.issue; }).length;
        var found = c.fields.filter(function (f, i) { return f.issue && open[i]; }).length;
        return '<div class="mt-4 ac-doc"><div class="ac-doc-h">' + esc(c.doc) + "</div>" + c.fields.map(function (f, i) {
          var o = open[i], cls = o ? (f.issue ? " is-issue" : " is-ok") : "";
          return '<button type="button" class="ac-row' + cls + '" data-field="' + i + '"' + (o ? " disabled" : "") + '><span class="l">' + esc(f.label) + '</span><span class="text-ink-900">' + esc(f.value) +
            (o ? '<span class="why ' + (f.issue ? "text-risk-600" : "text-ok-600") + '">' + (f.issue ? "✗ Problem: " + esc(f.issue) : "✓ This one's fine. " + esc(f.ok)) + "</span>" : "") + "</span></button>";
        }).join("") + "</div>" + meter(found, issues, "problems found") +
        (found === issues ? '<p class="text-[13px] text-ok-600 font-semibold mt-2">You found them all.' + (v.wrong ? " (" + v.wrong + " line" + (v.wrong === 1 ? " you tapped was" : "s you tapped were") + " fine.)" : "") + "</p>" : "");
      },

      scenario: function (c) {
        var v = S(), a = v.a || [], at = v.at || 0;
        if (at >= c.steps.length) {
          var good = a.filter(function (x, j) { return x === c.steps[j].answer; }).length;
          return '<div class="mt-4 p-5 rounded-sm border border-line bg-shell"><div class="text-[15px] font-semibold text-ink-900">' + good + " of " + c.steps.length + " best choices</div>" +
            '<ul class="mt-3 space-y-2.5">' + c.steps.map(function (s, j) {
              return '<li class="text-[12.5px] text-ink-800"><span class="' + (a[j] === s.answer ? "text-ok-600" : "text-risk-600") + ' font-semibold">' + (a[j] === s.answer ? "✓" : "✗") + "</span> " + esc(s.situation) +
                '<div class="text-ink-600 mt-0.5 ml-4">' + esc(s.explain) + "</div></li>"; }).join("") + "</ul>" +
            '<button type="button" class="btn btn-line mt-4" data-scn-reset>Try Again</button></div>';
        }
        var step = c.steps[at], pick = a[at];
        return '<div class="mt-4 p-5 rounded-sm border border-line bg-shell"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">Situation ' + (at + 1) + " of " + c.steps.length + "</div>" +
          '<p class="text-[14.5px] text-ink-900 font-medium mt-2 leading-relaxed">' + esc(step.situation) + '</p><div class="mt-3 space-y-1.5">' +
          step.options.map(function (o, j) {
            var cls = pick == null ? "hover:bg-shell" : j === step.answer ? "border-ok-600 bg-ok-100" : j === pick ? "border-risk-600/30 bg-risk-100" : "opacity-60";
            return '<button type="button" data-scn="' + j + '" class="w-full text-left px-3 py-2.5 rounded-sm border border-line text-[13px] bg-panel ' + cls + '"' + (pick != null ? " disabled" : "") + ">" + esc(o) + "</button>";
          }).join("") + "</div>" +
          (pick != null ? '<p class="text-[13px] mt-3 ' + (pick === step.answer ? "text-ok-600" : "text-ink-700") + '"><b>' + (pick === step.answer ? "Right." : "Not quite.") + "</b> " + esc(step.explain) + "</p>" +
            '<button type="button" class="btn btn-primary mt-3" data-scn-next>' + (at === c.steps.length - 1 ? "See Results" : "Next Situation ›") + "</button>" : "") + "</div>";
      },

      althea_sim: function (c) {
        var v = S(), msgs = v.msgs || [];
        return '<div class="mt-4 ac-chat"><div class="ac-chat-h"><span class="ac-orb">A</span>Althea <span class="text-[11px] text-ink-400 font-normal ml-auto">Practice mode · sample answers</span></div>' +
          '<div class="ac-msgs" id="ac-msgs">' + (msgs.length ? "" : '<div class="ac-her">Hi, I’m Althea. Ask me something, or tap a suggestion below.</div>') +
          msgs.map(function (m) { return m.me ? '<div class="ac-me">' + esc(m.t) + "</div>" : '<div class="ac-her">' + (m.typing ? '<span class="ac-dots"><span></span><span></span><span></span></span>' : esc(m.t)) + "</div>"; }).join("") + "</div>" +
          '<div class="ac-chips">' + c.prompts.map(function (p, i) { return '<button type="button" data-sim="' + i + '"' + ((v.used || {})[i] ? ' class="is-used"' : "") + ">" + esc(p.q) + "</button>"; }).join("") + "</div>" +
          '<form class="flex gap-2 p-3" data-sim-form><input class="fld flex-1" id="ac-sim-in" placeholder="Ask Althea…" autocomplete="off" aria-label="Ask the practice Althea"><button class="btn btn-primary" type="submit">Send</button></form></div>' +
          meter(Math.min(3, v.asked || 0), 3, "questions asked");
      },

      check: function (c) {
        var a = S().a;
        return '<div class="mt-3 p-4 rounded-sm border border-line bg-shell"><div class="text-[13.5px] font-semibold text-ink-900">' + esc(c.question) + '</div><div class="mt-2 space-y-1.5">' +
          c.options.map(function (o, i) {
            var cls = a == null ? "hover:bg-panel" : i === c.answer ? "border-ok-600 bg-ok-100" : i === a ? "border-risk-600/30 bg-risk-100" : "opacity-60";
            return '<button type="button" data-check="' + i + '" class="w-full text-left px-3 py-2 rounded-sm border border-line text-[13px] ' + cls + '"' + (a != null ? " disabled" : "") + ">" + esc(o) + "</button>";
          }).join("") + "</div>" + (a != null ? '<p class="text-[12.5px] mt-2 ' + (a === c.answer ? "text-ok-600" : "text-ink-700") + '">' + (a === c.answer ? "Right. " : "Not quite. ") + esc(c.explain) + "</p>" : "") + "</div>";
      },

      sandbox: function () { return '<div class="mt-5" id="ac-sandbox"></div>'; },

      althea: function (c) {
        var n = S().n || 1;
        return '<div class="mt-4 space-y-2">' + c.exchanges.slice(0, n).map(function (x) {
          return '<div class="flex justify-end"><div class="max-w-[80%] px-3 py-2 rounded-lg bg-med-600 text-white text-[13px]">' + esc(x[0]) + "</div></div>" +
                 '<div class="flex"><div class="max-w-[80%] px-3 py-2 rounded-lg bg-shell border border-line text-[13px] text-ink-800"><b class="text-[11px] text-ink-500 block">Althea</b>' + esc(x[1]) + "</div></div>";
        }).join("") + "</div>" + (n < c.exchanges.length ? '<button type="button" class="btn btn-line mt-3" data-more>Show Another Example</button>' : "");
      }
    };

    function cardHtml(c) {
      return head(c) + (KINDS[c.kind] ? KINDS[c.kind](c) : "") +
        (c.takeaway ? '<div class="ac-take mt-5"><span class="ac-take-i" aria-hidden="true">★</span><div><div class="text-[11px] uppercase tracking-wider font-semibold text-ink-500">Key takeaway</div><div class="text-[15px] font-semibold text-ink-900 mt-0.5">' + esc(c.takeaway) + "</div></div></div>" : "");
    }

    function ready(c, k) {
      if (done(C.modules[mi].key)) return true;
      var v = st[k] || {};
      switch (c.kind) {
        case "video": return Object.keys(v.visited || {}).length >= c.chapters.length;
        case "hotspots": return Object.keys(v.seen || {}).length >= c.spots.length;
        case "workspace": return Object.keys(v.seen || {}).length >= c.tour.length;
        case "sequence": return !!v.order && v.order.length === c.steps.length;
        case "spot": return c.fields.every(function (f, i) { return !f.issue || (v.open || {})[i]; });
        case "scenario": return (v.at || 0) >= c.steps.length;
        case "althea_sim": return (v.asked || 0) >= 3;
        case "check": return v.a != null;
        case "sandbox": return c.tasks.every(function (t) { return (C.practice.done || []).indexOf(t.key) >= 0; });
      }
      return true;
    }
    var NEEDS = { video: "Watch every chapter to continue", hotspots: "Explore every hotspot to continue", workspace: "Tap every area to continue",
                  sequence: "Put every step in order to continue", spot: "Find every problem to continue", scenario: "Finish the situations to continue",
                  althea_sim: "Ask Althea three questions to continue", check: "Answer the question to continue", sandbox: "Finish every practice task to continue" };

    function stepDots(m) {
      var n = steps(m);
      return '<div class="flex gap-1 items-center" aria-hidden="true">' + Array.apply(null, Array(n)).map(function (x, i) {
        return '<span style="width:18px;height:5px;border-radius:3px;background:' + (i < ci ? "#0c8a4f" : i === ci ? "var(--brand)" : "#d7dae0") + '"></span>'; }).join("") + "</div>";
    }
    function header(m) {
      var L = lessons(), n = steps(m), left = Math.max(1, Math.round((m.minutes || 1) * (n - ci) / n));
      return '<div class="flex items-center justify-between mb-4 gap-3 flex-wrap"><div><div class="text-[13px] font-semibold text-ink-900">Lesson ' + (L.indexOf(m) + 1) + " of " + L.length + " · About " + left + " minute" + (left === 1 ? "" : "s") + " remaining</div>" +
        '<div class="text-[12px] text-ink-500">' + esc(m.title) + " · " + (ci >= m.cards.length ? "Check your understanding" : "Step " + (ci + 1) + " of " + n) + (done(m.key) ? ' · <span class="text-ok-600 font-semibold">Mastered ✓</span>' : "") + "</div></div>" + stepDots(m) + "</div>";
    }
    function tutorHtml(m) {
      if (!m.tutor) return "";
      var t = TU[key()] || {};
      return '<div class="ac-tutor mt-6" id="ac-tutor"><div class="flex items-center gap-2 flex-wrap"><span class="ac-orb" style="width:24px;height:24px;line-height:24px;font-size:11px">A</span>' +
        '<span class="text-[12.5px] font-semibold text-ink-900 mr-1">Ask Althea</span>' +
        [["simple", "Explain this simply"], ["example", "Show an example"], ["walk", "Walk me through it"]].map(function (b) {
          return '<button type="button" class="ac-tbtn' + (t.mode === b[0] ? " is-on" : "") + '" data-tutor="' + b[0] + '">' + b[1] + "</button>"; }).join("") + "</div>" +
        '<form class="flex gap-2 mt-2" data-tutor-form><input class="fld flex-1" id="ac-tq" placeholder="Or ask about this step in your own words…" autocomplete="off" aria-label="Ask Althea about this step"><button type="submit" class="btn btn-line">Ask</button></form>' +
        (t.loading ? '<div class="ac-tans"><span class="ac-dots"><span></span><span></span><span></span></span></div>' : t.answer ? '<div class="ac-tans" role="status">' + esc(t.answer).replace(/\n/g, "<br>") +
          (t.sources && t.sources.length ? '<div class="text-[11px] text-ink-400 mt-2">From the course: ' + t.sources.map(function (x) { return esc(x.title); }).join(" · ") + "</div>" : "") + "</div>" : "") + "</div>";
    }
    function moduleHtml() {
      var m = C.modules[mi];
      if (m.key === "quiz") return quizHtml();
      if (m.key === "complete") return completeHtml();
      if (ci >= m.cards.length) return '<div class="card p-6 md:p-8">' + header(m) + '<div id="ac-card">' + checkHtml(m) + "</div>" + tutorHtml(m) + "</div>";
      var c = m.cards[ci], last = ci === m.cards.length - 1, ok = ready(c, key());
      var nextLabel = !last ? "Next ›" : m.check ? (done(m.key) ? "Finish Lesson ›" : "Check Your Understanding ›") : "Finish Lesson ›";
      return '<div class="card p-6 md:p-8">' + header(m) + '<div id="ac-card">' + cardHtml(c) + "</div>" + tutorHtml(m) +
        '<div class="mt-6 flex items-center justify-between gap-3"><button type="button" class="btn btn-line" data-back' + (mi === 0 && ci === 0 ? ' disabled style="visibility:hidden"' : "") + ">‹ Back</button>" +
        '<span class="text-[12.5px] text-ink-500 text-center" id="ac-need">' + (ok ? "" : esc(NEEDS[c.kind] || "")) + "</span>" +
        '<button type="button" class="btn btn-primary" id="ac-next" data-next' + (ok ? "" : " disabled") + ">" + nextLabel + "</button></div></div>";
    }

    /* ---------- the check at the end of a lesson: two real-life situations, graded on the server ---------- */
    function checkHtml(m) {
      var k = CK[m.key] || {};
      if (k.result) {
        var r = k.result;
        return '<h3 class="text-[22px] font-semibold text-ink-900">' + (r.passed ? "Lesson mastered ✓" : "Not quite yet") + "</h3>" +
          '<p class="text-[15px] text-ink-700 mt-2">' + (r.passed ? "You got both situations right. Here's why each answer is right or wrong:" : "You got " + r.right + " of " + r.total + ". Read why each answer is right or wrong, then try again with new situations.") + "</p>" +
          r.results.map(function (x, qi) {
            var q = k.q.questions[qi];
            return '<div class="mt-5"><div class="text-[15px] font-semibold text-ink-900">' + (x.correct ? '<span class="text-ok-600">✓</span> ' : '<span class="text-risk-600">✗</span> ') + esc(q.q) + '</div><div class="mt-2 space-y-1.5">' +
              x.options.map(function (o, oi) {
                var mine = oi === x.picked;
                return '<div class="ac-why ' + (o.ok ? "is-ok" : mine ? "is-bad" : "") + '"><div class="text-[14px] ' + (o.ok ? "font-semibold text-ink-900" : "text-ink-800") + '">' + (o.ok ? "✓ " : "✗ ") + esc(o.t) +
                  (mine ? ' <span class="ac-tag" style="margin-left:6px">Your answer</span>' : "") + '</div><div class="text-[13px] text-ink-600 mt-0.5">' + esc(o.why) + "</div></div>";
              }).join("") + "</div></div>";
          }).join("") +
          (r.passed ? '<div class="mt-6 flex justify-end"><button type="button" class="btn btn-primary" data-check-continue>Continue ›</button></div>'
            : '<div class="ac-tans mt-5"><b class="block text-[12px] uppercase tracking-wider text-ink-500 mb-1">Althea · the short version</b>' + esc(r.help || "") + "</div>" +
              '<div class="mt-5 flex gap-2 flex-wrap justify-end"><button type="button" class="btn btn-line" data-check-review>Review The Lesson</button><button type="button" class="btn btn-primary" data-check-start>Try Again With New Situations</button></div>');
      }
      if (k.q) {
        var n = Object.keys(k.answers).length;
        return '<h3 class="text-[22px] font-semibold text-ink-900">Check your understanding</h3><p class="text-[14px] text-ink-600 mt-1">' + n + " of " + k.q.questions.length + " answered</p>" +
          k.q.questions.map(function (q, qi) {
            return '<fieldset class="mt-5"><legend class="text-[16px] font-semibold text-ink-900 mb-2 leading-snug">' + (qi + 1) + ". " + esc(q.q) + "</legend>" + q.options.map(function (o, i) {
              return '<label class="flex items-start gap-2.5 px-3.5 py-3 rounded-sm border border-line mb-2 text-[14.5px] cursor-pointer hover:bg-shell"><input type="radio" name="ck-' + esc(q.id) + '" data-ck="' + esc(q.id) + '" value="' + i + '" class="mt-1"' + (k.answers[q.id] === i ? " checked" : "") + "><span>" + esc(o) + "</span></label>";
            }).join("") + "</fieldset>";
          }).join("") + '<div class="mt-5 flex justify-between"><button type="button" class="btn btn-line" data-check-review>‹ Back To The Lesson</button><button type="button" class="btn btn-primary" data-check-submit' + (n < k.q.questions.length ? " disabled" : "") + ">Submit Answers</button></div>";
      }
      if (done(m.key)) {
        return '<h3 class="text-[22px] font-semibold text-ink-900">You\'ve mastered this lesson ✓</h3><p class="text-[15px] text-ink-700 mt-2">You can practice the situations again any time.</p>' +
          '<div class="mt-6 flex gap-2 justify-end"><button type="button" class="btn btn-line" data-check-start>Practice Again</button><button type="button" class="btn btn-primary" data-check-continue>Continue ›</button></div>';
      }
      return '<h3 class="text-[22px] font-semibold text-ink-900">Check your understanding</h3>' +
        '<p class="text-[15.5px] text-ink-700 mt-3 leading-relaxed">' + (C.checkSize || 2) + " real-life situations from this lesson. Get " + ((C.checkSize || 2) === 2 ? "both" : "all") + " right and the lesson counts as mastered. " +
        "If you miss one, you'll see why, get a simpler explanation, and try again with different situations.</p>" +
        (C.fails[m.key] ? '<p class="text-[13px] text-warn-600 mt-2">Tried ' + C.fails[m.key] + " time" + (C.fails[m.key] === 1 ? "" : "s") + " so far. Use the Althea buttons below if you'd like it explained another way.</p>" : "") +
        '<div class="mt-6 flex justify-between"><button type="button" class="btn btn-line" data-check-review>‹ Back To The Lesson</button><button type="button" class="btn btn-primary" data-check-start>Start The Check</button></div>';
    }
    function refreshNext() {
      var c = cur(), b = document.getElementById("ac-next"), n = document.getElementById("ac-need");
      if (!c || !b) return;
      var ok = ready(c, key()); b.disabled = !ok; if (n) n.textContent = ok ? "" : (NEEDS[c.kind] || "");
    }
    function renderCard() {
      var box = document.getElementById("ac-card");
      if (!box) return render();
      var m = C.modules[mi];
      box.innerHTML = ci >= m.cards.length ? checkHtml(m) : cardHtml(cur()); refreshNext(); wire();
    }
    function renderTutor() { var t = document.getElementById("ac-tutor"); if (t) t.outerHTML = tutorHtml(C.modules[mi]); }
    function askTutor(mode, question) {
      var m = C.modules[mi], k = key();
      TU[k] = { mode: mode, loading: true }; renderTutor();
      api("POST", "/api/portal/course/althais/tutor", { module: m.key, card: ci < m.cards.length ? ci : null, mode: mode, question: question || "" }).then(function (r) {
        TU[k] = { mode: mode, answer: r.answer, sources: r.sources }; if (key() === k) renderTutor();
      }).catch(function (e) { TU[k] = { mode: mode, answer: e.message }; if (key() === k) renderTutor(); });
    }

    function quizHtml() {
      var lessonNo = C.modules.findIndex(function (m) { return m.key === "quiz"; }) + 1;
      if (C.record && C.record.completed && !quiz && !(result && !result.passed)) return completeHtml();
      if (result && !result.passed) {
        return '<div class="card p-6"><h3 class="text-[17px] font-semibold text-ink-900">Almost there</h3><p class="text-[13.5px] text-ink-700 mt-1">You scored ' + result.score + "% (" + result.right + " of " + result.total + "). You need " + result.passScore +
          "% to complete the course. Here's what to look at again:</p>" +
          '<div class="mt-3 space-y-2">' + result.missed.map(function (x) { return '<div class="p-3 rounded-sm border border-line bg-shell"><div class="text-[13px] text-ink-900 font-medium">' + esc(x.q) + '</div><div class="text-[12.5px] text-ink-700 mt-1">' + esc(x.concept) + '</div><div class="text-[12px] text-ok-600 mt-1">Best answer: ' + esc(x.correct) + "</div></div>"; }).join("") + "</div>" +
          '<div class="mt-5 flex gap-2"><button type="button" class="btn btn-line" data-mod="0">Review The Lessons</button><button type="button" class="btn btn-primary" data-quiz-start>Try Again</button></div></div>';
      }
      if (!quiz && !allMastered()) {
        var left = lessons().filter(function (m) { return !done(m.key); });
        return '<div class="card p-6 md:p-8"><h3 class="text-[22px] font-semibold text-ink-900">Almost ready for the final check</h3><p class="text-[15px] text-ink-700 mt-2">Master every lesson first. Still to go:</p>' +
          '<div class="mt-3 space-y-1.5">' + left.map(function (m) { return '<button type="button" class="w-full text-left px-3 py-2.5 rounded-sm border border-line hover:bg-shell text-[14px]" data-mod="' + C.modules.indexOf(m) + '">' + esc(m.title) + (seen(m.key) && m.check ? ' <span class="text-warn-600 text-[12.5px]">· check not passed yet</span>' : "") + "</button>"; }).join("") + "</div></div>";
      }
      if (!quiz) {
        return '<div class="card p-6"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">Lesson ' + lessonNo + ' · Knowledge Check</div><h3 class="text-[17px] font-semibold text-ink-900 mt-2">Show what you know</h3>' +
          '<p class="text-[13.5px] text-ink-700 mt-2 leading-relaxed">' + C.quizSize + " practical questions drawn from the whole course: privacy, the visit-to-claim workflow, Althea and using AI safely. " +
          "You need " + C.passScore + "% to complete the course. The questions change on every attempt, and you can try again if you need to.</p>" +
          '<button type="button" class="btn btn-primary mt-5" data-quiz-start>Start Knowledge Check</button></div>';
      }
      var answered = Object.keys(quiz.answers).length;
      return '<div class="card p-6"><div class="flex justify-between items-center mb-4"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">Knowledge Check</div>' +
        '<div class="text-[12px] text-ink-500" id="ac-qcount">' + answered + " of " + quiz.questions.length + " answered</div></div>" + quiz.questions.map(function (q, qi) {
        return '<fieldset class="mb-5"><legend class="text-[13.5px] font-semibold text-ink-900 mb-2">' + (qi + 1) + ". " + esc(q.q) + "</legend>" + q.options.map(function (o, i) {
          return '<label class="flex items-start gap-2 px-3 py-2 rounded-sm border border-line mb-1.5 text-[13px] cursor-pointer hover:bg-shell"><input type="radio" name="' + esc(q.id) + '" value="' + i + '" class="mt-0.5"' + (quiz.answers[q.id] === i ? " checked" : "") + "><span>" + esc(o) + "</span></label>";
        }).join("") + "</fieldset>";
      }).join("") + '<button type="button" class="btn btn-primary" data-quiz-submit>Submit Answers</button></div>';
    }

    function completeHtml() {
      var r = C.record || {};
      return '<div class="card p-8 text-center"><div class="text-[40px] text-ok-600 leading-none">✓</div><h3 class="text-[20px] font-semibold text-ink-900 mt-2">Althais Training Complete</h3>' +
        '<dl class="inline-grid grid-cols-[auto_auto] gap-x-6 gap-y-1 text-left text-[13px] mt-5"><dt class="text-ink-500">Name</dt><dd class="text-ink-900 font-medium">' + esc(C.name) + "</dd>" +
        '<dt class="text-ink-500">Completed</dt><dd class="text-ink-900 font-medium">' + esc(opt.fmtDate(r.completed)) + "</dd>" +
        '<dt class="text-ink-500">Course Version</dt><dd class="text-ink-900 font-medium">' + esc(r.version || C.version) + "</dd>" +
        '<dt class="text-ink-500">Score</dt><dd class="text-ink-900 font-medium">' + esc(r.score != null ? r.score + "%" : "-") + "</dd></dl>" +
        '<p class="text-[12.5px] text-ink-500 mt-5">This is saved to your staff record, and your Althais Training onboarding item is complete.</p>' +
        '<div class="mt-5 flex justify-center gap-2"><button type="button" class="btn btn-line" data-mod="0">Review The Course</button><a class="btn btn-primary" href="#training">Back To Training</a></div></div>';
    }

    function render() {
      if (video) { try { video.pause(); } catch (e) {} video = null; }
      el.innerHTML = '<div class="grid gap-4 lg:grid-cols-[230px_minmax(0,1fr)] items-start">' + nav() + "<div>" + moduleHtml() + "</div></div>";
      wire();
    }

    /* ---------- the video: updated in place, so playback is never interrupted ---------- */
    function showChapter(c, i) {
      var cap = document.getElementById("ac-cap"), x = c.chapters[i];
      if (cap) cap.innerHTML = "<b>" + esc(x.title) + (x.yours ? " · Your Part" : "") + "</b><span>" + esc(x.text) + "</span>";
      el.querySelectorAll("[data-chap]").forEach(function (b) { b.classList.toggle("is-cur", Number(b.dataset.chap) === i); });
    }
    function markChapter(c, v, i) {
      v.visited = v.visited || {};
      if (v.visited[i]) return;
      v.visited[i] = 1;
      var b = el.querySelector('[data-chap="' + i + '"] .ac-w'); if (b) b.textContent = "✓ Watched";
      var m = document.getElementById("ac-vmeter"); if (m) m.innerHTML = meter(Object.keys(v.visited).length, c.chapters.length, "chapters watched");
      refreshNext();
    }
    function wire() {
      var sb = document.getElementById("ac-sandbox"), c0 = cur();
      if (sb && c0 && c0.kind === "sandbox" && window.AlthaisPractice) {
        window.AlthaisPractice.render(sb, c0, { api: api, esc: esc, done: C.practice.done || [], onChange: function (r) {
          C.practice.done = r.done; C.progress = r.progress || C.progress; refreshNext();
          if (r.ok && ready(c0, key())) { var nv = el.querySelector(".card.p-3"); if (nv) nv.outerHTML = nav(); }
        } });
      }
      var c = cur(), vid = document.getElementById("ac-vid");
      if (!c || c.kind !== "video" || !vid) return;
      if (video && video !== vid) { try { video.pause(); } catch (e) {} }
      video = vid;
      var v = S(), ch = c.chapters, k = key();
      if (v.guided == null) v.guided = true;
      vid.playbackRate = v.rate || 0.75;
      function at(t) { var i = 0; for (var j = 0; j < ch.length; j++) if (t + 0.05 >= ch[j].t) i = j; return i; }
      vid.addEventListener("loadedmetadata", function () {
        var d = vid.duration || 1;
        el.querySelectorAll("#ac-track u").forEach(function (u) { u.style.left = (100 * Number(u.dataset.at) / d) + "%"; });
        if (v.time) vid.currentTime = v.time;
        vid.playbackRate = v.rate || 0.75;
      });
      vid.addEventListener("timeupdate", function () {
        if (key() !== k) return;
        var t = vid.currentTime, i = at(t), p = document.getElementById("ac-prog");
        v.time = t;
        if (p) p.style.width = (100 * t / (vid.duration || 1)) + "%";
        if (i !== v.idx) {
          var forward = v.idx != null && i === v.idx + 1 && !v.seeking;
          v.idx = i; showChapter(c, i); markChapter(c, v, i);
          if (forward && v.guided && !vid.paused) vid.pause();
        }
        v.seeking = false;
      });
      vid.addEventListener("play", function () { var b = document.getElementById("ac-play"); if (b) b.textContent = "❚❚ Pause"; if (v.idx == null) { v.idx = 0; showChapter(c, 0); markChapter(c, v, 0); } });
      vid.addEventListener("pause", function () { var b = document.getElementById("ac-play"); if (b) b.textContent = vid.ended ? "↺ Replay" : "▶ Play"; });
      vid.addEventListener("ended", function () { ch.forEach(function (x, i) { markChapter(c, v, i); }); });
      vid.addEventListener("click", function () { if (vid.paused) vid.play().catch(function () {}); else vid.pause(); });
    }
    function seekChapter(i) {
      var c = cur(), v = S(); if (!video) return;
      i = Math.max(0, Math.min(c.chapters.length - 1, i));
      v.seeking = true; v.idx = i; video.currentTime = c.chapters[i].t + 0.05;
      showChapter(c, i); markChapter(c, v, i);
      video.play().catch(function () {});
    }

    /* ---------- the practice Althea ---------- */
    function simMatch(c, q) {
      var t = q.toLowerCase(), best = -1, score = 0;
      c.prompts.forEach(function (p, i) {
        var s = p.q.toLowerCase() === t ? 99 : p.keys.filter(function (k) { return t.indexOf(k) >= 0; }).length;
        if (s > score) { score = s; best = i; }
      });
      return best;
    }
    function simAsk(q) {
      var c = cur(), v = S(), k = key();
      if (!q || v.busy) return;
      v.msgs = v.msgs || []; v.used = v.used || {};
      var i = simMatch(c, q), reply = { typing: 1 };
      if (i >= 0) v.used[i] = 1;
      v.msgs.push({ me: 1, t: q }); v.msgs.push(reply); v.busy = true;
      renderCard(); scrollMsgs();
      setTimeout(function () {
        reply.typing = 0; reply.t = i >= 0 ? c.prompts[i].a : c.fallback; v.busy = false; v.asked = (v.asked || 0) + 1;
        if (key() === k && document.getElementById("ac-msgs")) {
          renderCard(); scrollMsgs();
          var inp = document.getElementById("ac-sim-in"); if (inp && window.matchMedia && window.matchMedia("(pointer: fine)").matches) inp.focus();
        }
      }, 600 + Math.min(900, (i >= 0 ? c.prompts[i].a.length : 60) * 4));
    }
    function scrollMsgs() { var m = document.getElementById("ac-msgs"); if (m) m.scrollTop = m.scrollHeight; }

    function recordVisit(m) {
      return seen(m.key) && (done(m.key) || m.check) ? Promise.resolve() : api("POST", "/api/portal/course/althais/progress", { module: m.key }).then(function (r) { C.progress = r.progress; C.visited = r.visited || C.visited; });
    }
    function finishModule() {
      var m = C.modules[mi];
      return recordVisit(m).then(function () {
        if (m.check && !done(m.key) && ci < m.cards.length) { ci = m.cards.length; render(); window.scrollTo(0, 0); return; }
        mi = Math.min(mi + 1, C.modules.length - 1); ci = 0; render(); window.scrollTo(0, 0); if (opt.onChange) opt.onChange();
      });
    }
    function nextModule() { mi = Math.min(mi + 1, C.modules.length - 1); ci = 0; render(); window.scrollTo(0, 0); if (opt.onChange) opt.onChange(); }
    function go(nci) { ci = nci; render(); var top = el.getBoundingClientRect().top + window.scrollY - 70; if (window.scrollY > top) window.scrollTo(0, top); }
    function stepSpot(c, d) { var v = S(); v.cur = v.cur == null ? 0 : Math.max(0, Math.min(c.spots.length - 1, v.cur + d)); (v.seen = v.seen || {})[v.cur] = 1; renderCard(); }

    el.addEventListener("click", function (e) {
      var t, c = C && cur(), v;
      if (e.target.closest("[data-outline]")) { outline = !outline; render(); return; }
      if ((t = e.target.closest("[data-tutor]"))) { askTutor(t.dataset.tutor); return; }
      if (e.target.closest("[data-check-review]")) { var mm = C.modules[mi]; delete CK[mm.key]; go(0); return; }
      if (e.target.closest("[data-check-continue]")) { delete CK[C.modules[mi].key]; nextModule(); return; }
      if ((t = e.target.closest("[data-check-start]"))) {
        var km = C.modules[mi].key; t.disabled = true;
        api("POST", "/api/portal/course/althais/check", { module: km }).then(function (q) { CK[km] = { q: q, answers: {} }; renderCard(); }).catch(function (er) { t.disabled = false; opt.toast(er.message, "error"); });
        return;
      }
      if ((t = e.target.closest("[data-check-submit]")) && !t.disabled) {
        var ks = C.modules[mi].key, K = CK[ks]; t.disabled = true;
        api("POST", "/api/portal/course/althais/check/submit", { attempt: K.q.attempt, answers: K.answers }).then(function (r) {
          K.result = r; C.progress = r.progress; if (!r.passed) C.fails[ks] = r.fails;
          render(); var top = el.getBoundingClientRect().top + window.scrollY - 70; window.scrollTo(0, Math.max(0, top)); if (opt.onChange) opt.onChange();
        }).catch(function (er) { t.disabled = false; opt.toast(er.message, "error"); });
        return;
      }
      if ((t = e.target.closest("[data-mod]")) && !t.disabled) { outline = false; mi = Number(t.dataset.mod); ci = 0; result = null; if (C.modules[mi].key !== "quiz") quiz = null; render(); return; }
      if ((t = e.target.closest("[data-spot]"))) { v = S(); v.cur = Number(t.dataset.spot); (v.seen = v.seen || {})[v.cur] = 1; renderCard(); return; }
      if ((t = e.target.closest("[data-spot-step]")) && !t.disabled) { stepSpot(c, S().cur == null ? 0 : Number(t.dataset.spotStep)); return; }
      if ((t = e.target.closest("[data-tour]"))) { v = S(); v.cur = Number(t.dataset.tour); (v.seen = v.seen || {})[v.cur] = 1; renderCard(); return; }
      if ((t = e.target.closest("[data-seq]"))) {
        v = S(); var n = Number(t.dataset.seq);
        if (n === v.order.length) { v.order.push(n); v.bad = null; v.hint = ""; }
        else { v.tries++; v.bad = n; v.hint = "Not yet. Something else has to happen before “" + c.steps[n].label.charAt(0).toLowerCase() + c.steps[n].label.slice(1) + "”."; }
        renderCard(); return;
      }
      if (e.target.closest("[data-seq-reset]") || e.target.closest("[data-scn-reset]")) { st[key()] = {}; renderCard(); return; }
      if ((t = e.target.closest("[data-field]")) && !t.disabled) { v = S(); var f = Number(t.dataset.field); (v.open = v.open || {})[f] = 1; if (!c.fields[f].issue) v.wrong = (v.wrong || 0) + 1; renderCard(); return; }
      if ((t = e.target.closest("[data-scn]")) && !t.disabled) { v = S(); (v.a = v.a || [])[v.at || 0] = Number(t.dataset.scn); renderCard(); return; }
      if (e.target.closest("[data-scn-next]")) { v = S(); v.at = (v.at || 0) + 1; renderCard(); return; }
      if ((t = e.target.closest("[data-sim]"))) { simAsk(c.prompts[Number(t.dataset.sim)].q); return; }
      if ((t = e.target.closest("[data-check]"))) { S().a = Number(t.dataset.check); renderCard(); return; }
      if (e.target.closest("[data-more]")) { v = S(); v.n = (v.n || 1) + 1; renderCard(); return; }
      if ((t = e.target.closest("[data-chap]"))) { seekChapter(Number(t.dataset.chap)); return; }
      if ((t = e.target.closest("[data-vid-step]"))) { v = S(); seekChapter((v.idx == null ? 0 : v.idx) + Number(t.dataset.vidStep)); return; }
      if (e.target.closest("#ac-play")) { if (video) { if (video.paused) video.play().catch(function () {}); else video.pause(); } return; }
      if ((t = e.target.closest("[data-rate]"))) { v = S(); v.rate = Number(t.dataset.rate); if (video) video.playbackRate = v.rate; el.querySelectorAll("[data-rate]").forEach(function (b) { b.classList.toggle("is-on", b === t); }); return; }
      if ((t = e.target.closest("#ac-track")) && video && video.duration) { var r = t.getBoundingClientRect(); S().seeking = true; video.currentTime = video.duration * Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); return; }
      if (e.target.closest("[data-back]")) { if (ci > 0) go(ci - 1); else if (mi > 0) { mi--; go(Math.max(0, C.modules[mi].cards.length - 1)); } return; }
      if ((t = e.target.closest("[data-next]")) && !t.disabled) { if (ci < C.modules[mi].cards.length - 1) go(ci + 1); else finishModule().catch(function (er) { opt.toast(er.message, "error"); }); return; }
      if (e.target.closest("[data-quiz-start]")) {
        mi = C.modules.findIndex(function (m) { return m.key === "quiz"; }); result = null;
        api("POST", "/api/portal/course/althais/quiz").then(function (q) { quiz = q; quiz.answers = {}; render(); window.scrollTo(0, 0); }).catch(function (er) { opt.toast(er.message, "error"); });
        return;
      }
      if (e.target.closest("[data-quiz-submit]")) {
        var missing = quiz.questions.filter(function (q) { return quiz.answers[q.id] == null; }).length;
        if (missing) return opt.toast("Answer every question first (" + missing + " left)", "error");
        api("POST", "/api/portal/course/althais/quiz/submit", { attempt: quiz.attempt, answers: quiz.answers }).then(function (r) {
          result = r; quiz = null;
          if (r.passed) { C.record = { completed: r.completed, score: r.score, version: r.version }; mi = C.modules.length - 1; if (opt.onChange) opt.onChange(); }
          render(); window.scrollTo(0, 0);
        }).catch(function (er) { opt.toast(er.message, "error"); });
      }
    });
    el.addEventListener("submit", function (e) {
      if (e.target.closest("[data-tutor-form]")) { e.preventDefault(); var tq = document.getElementById("ac-tq"), qv = tq.value.trim(); if (qv) askTutor("ask", qv); return; }
      if (!e.target.closest("[data-sim-form]")) return;
      e.preventDefault();
      var inp = document.getElementById("ac-sim-in"), q = inp.value.trim(); inp.value = ""; simAsk(q);
    });
    el.addEventListener("change", function (e) {
      if (quiz && e.target.type === "radio" && !e.target.dataset.ck) { quiz.answers[e.target.name] = Number(e.target.value); var qc = document.getElementById("ac-qcount"); if (qc) qc.textContent = Object.keys(quiz.answers).length + " of " + quiz.questions.length + " answered"; }
      if (e.target.id === "ac-guided") S().guided = e.target.checked;
      if (e.target.dataset && e.target.dataset.ck) {
        var K2 = CK[C.modules[mi].key]; if (!K2) return;
        K2.answers[e.target.dataset.ck] = Number(e.target.value);
        var sb2 = el.querySelector("[data-check-submit]"); if (sb2) sb2.disabled = Object.keys(K2.answers).length < K2.q.questions.length;
      }
    });
    /* arrow keys step through a screen tour; one listener, whichever course is mounted */
    if (window.__acKeys) document.removeEventListener("keydown", window.__acKeys);
    window.__acKeys = function (e) {
      if (!el.isConnected) { if (video) { try { video.pause(); } catch (x) {} } return; }
      if (!C || /input|textarea|select/i.test(e.target.tagName || "")) return;
      var c = cur(); if (!c || c.kind !== "hotspots" || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
      e.preventDefault(); stepSpot(c, S().cur == null ? 0 : (e.key === "ArrowRight" ? 1 : -1));
    };
    document.addEventListener("keydown", window.__acKeys);

    load();
  }

  window.AlthaisCourse = { mount: mount };
})();
