/* Althais Training course player (Staff Portal > Training > Althais Training).
 *
 *   AlthaisCourse.mount(container, { api, esc, fmtDate, toast, onChange })
 *
 * Content, the knowledge check and grading all come from the server (althais_training.py): this only presents them.
 * Card kinds: text, workspace (tap each area), check (instant feedback, not graded), althea (example conversation).
 */
(function () {
  "use strict";

  function mount(el, opt) {
    var api = opt.api, esc = opt.esc, C = null, mi = 0, ci = 0, seen = {}, answered = {}, shown = {}, quiz = null, result = null;

    function load() {
      return api("GET", "/api/portal/course/althais").then(function (c) {
        C = c;
        if (c.record && c.record.completed) { mi = c.modules.length - 1; }
        else { var i = c.modules.findIndex(function (m) { return m.key !== "quiz" && m.key !== "complete" && c.progress.indexOf(m.key) < 0; }); mi = i < 0 ? c.modules.length - 2 : i; }
        ci = 0; render();
      }).catch(function (e) { el.innerHTML = '<div class="card p-5 text-[13px] text-risk-600">' + esc(e.message) + "</div>"; });
    }

    function done(key) { return C.progress.indexOf(key) >= 0 || (key === "quiz" && !!(C.record && C.record.completed)); }
    function nav() {
      var total = C.modules.length - 1, count = C.modules.filter(function (m) { return m.key !== "complete" && done(m.key); }).length;
      return '<div class="card p-3"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold px-1 mb-1">Althais Training · v' + esc(C.version) + '</div>' +
        '<div class="w-full h-1.5 bg-shell rounded-sm overflow-hidden mb-2 mx-1" style="width:calc(100% - 8px)"><div class="h-full bg-ok-600" style="width:' + Math.round(100 * count / total) + '%"></div></div>' +
        C.modules.map(function (m, i) {
          var d = done(m.key), cur = i === mi, open = i <= mi || d || C.modules.slice(0, i).every(function (x) { return x.key === "complete" || done(x.key); });
          return '<button type="button" class="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] ' + (cur ? "bg-med-100 font-semibold text-ink-900" : "text-ink-700 hover:bg-shell") + '" data-mod="' + i + '"' + (open ? "" : " disabled style=\"opacity:.45\"") + '>' +
            '<span class="w-4 text-center ' + (d ? "text-ok-600" : "text-ink-400") + '">' + (d ? "✓" : i + 1) + "</span>" + esc(m.title) +
            (m.minutes ? '<span class="ml-auto text-[10.5px] text-ink-400 font-normal">' + m.minutes + " min</span>" : "") + "</button>";
        }).join("") + "</div>";
    }

    function cardHtml(c, key) {
      var head = '<h3 class="text-[16px] font-semibold text-ink-900">' + esc(c.title) + "</h3>" + (c.body ? '<p class="text-[13.5px] text-ink-700 mt-2 leading-relaxed">' + esc(c.body) + "</p>" : "") +
        (c.bullets.length ? '<ul class="mt-3 space-y-1.5">' + c.bullets.map(function (b) { return '<li class="flex gap-2 text-[13px] text-ink-700"><span class="text-med-600">•</span><span>' + esc(b) + "</span></li>"; }).join("") + "</ul>" : "");
      if (c.kind === "workspace") {
        var v = seen[key] || {};
        return head + '<div class="mt-4 grid sm:grid-cols-[200px_1fr] gap-3"><div class="rounded-sm border border-line overflow-hidden">' +
          c.tour.map(function (t, i) { return '<button type="button" data-tour="' + i + '" class="w-full text-left px-3 py-2 text-[12.5px] border-b border-lineSoft ' + (v.cur === i ? "bg-med-100 font-semibold" : "hover:bg-shell") + '">' + (v[i] ? '<span class="text-ok-600 mr-1">✓</span>' : "") + esc(t.name) + "</button>"; }).join("") +
          '</div><div class="rounded-sm border border-line p-4 bg-shell min-h-[120px]">' + (v.cur != null ? '<div class="text-[14px] font-semibold text-ink-900">' + esc(c.tour[v.cur].name) + '</div><p class="text-[13px] text-ink-700 mt-1">' + esc(c.tour[v.cur].text) + "</p>"
            : '<p class="text-[13px] text-ink-500">Tap an area on the left.</p>') +
          '<div class="text-[11.5px] text-ink-400 mt-3">' + Object.keys(v).filter(function (k) { return k !== "cur"; }).length + " of " + c.tour.length + " explored</div></div></div>";
      }
      if (c.kind === "check") {
        var a = answered[key];
        return head + '<div class="mt-3 p-4 rounded-sm border border-line bg-shell"><div class="text-[13.5px] font-semibold text-ink-900">' + esc(c.question) + '</div><div class="mt-2 space-y-1.5">' +
          c.options.map(function (o, i) {
            var st = a == null ? "hover:bg-panel" : i === c.answer ? "border-ok-600 bg-ok-100" : i === a ? "border-risk-600/30 bg-risk-100" : "opacity-60";
            return '<button type="button" data-check="' + i + '" class="w-full text-left px-3 py-2 rounded-sm border border-line text-[13px] ' + st + '"' + (a != null ? " disabled" : "") + ">" + esc(o) + "</button>";
          }).join("") + "</div>" + (a != null ? '<p class="text-[12.5px] mt-2 ' + (a === c.answer ? "text-ok-600" : "text-ink-700") + '">' + (a === c.answer ? "Right. " : "Not quite. ") + esc(c.explain) + "</p>" : "") + "</div>";
      }
      if (c.kind === "althea") {
        var n = shown[key] || 1;
        return head + '<div class="mt-4 space-y-2">' + c.exchanges.slice(0, n).map(function (x) {
          return '<div class="flex justify-end"><div class="max-w-[80%] px-3 py-2 rounded-lg bg-med-600 text-white text-[13px]">' + esc(x[0]) + '</div></div>' +
                 '<div class="flex"><div class="max-w-[80%] px-3 py-2 rounded-lg bg-shell border border-line text-[13px] text-ink-800"><b class="text-[11px] text-ink-500 block">Althea</b>' + esc(x[1]) + "</div></div>";
        }).join("") + "</div>" + (n < c.exchanges.length ? '<button type="button" class="btn btn-line mt-3" data-more>Show Another Example</button>' : "");
      }
      return head;
    }

    function canContinue(c, key) {
      if (c.kind === "workspace") return Object.keys(seen[key] || {}).filter(function (k) { return k !== "cur"; }).length >= c.tour.length;
      if (c.kind === "check") return answered[key] != null;
      return true;
    }

    function moduleHtml() {
      var m = C.modules[mi];
      if (m.key === "quiz") return quizHtml();
      if (m.key === "complete") return completeHtml();
      var c = m.cards[ci], key = mi + ":" + ci, last = ci === m.cards.length - 1;
      return '<div class="card p-6"><div class="flex items-center justify-between mb-4"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">Module ' + (mi + 1) + " · " + esc(m.title) + "</div>" +
        '<div class="text-[11.5px] text-ink-400">' + (ci + 1) + " of " + m.cards.length + "</div></div>" + cardHtml(c, key) +
        '<div class="mt-6 flex justify-between"><button type="button" class="btn btn-line" data-back' + (mi === 0 && ci === 0 ? " disabled style=\"visibility:hidden\"" : "") + '>Back</button>' +
        '<button type="button" class="btn btn-primary" data-next' + (canContinue(c, key) ? "" : " disabled title=\"Finish this step first\"") + ">" + (last ? "Continue" : "Next") + "</button></div></div>";
    }

    function quizHtml() {
      if (C.record && C.record.completed && !quiz) return completeHtml();
      if (result && !result.passed) {
        return '<div class="card p-6"><h3 class="text-[16px] font-semibold text-ink-900">Almost there</h3><p class="text-[13.5px] text-ink-700 mt-1">You scored ' + result.score + "%. You need " + result.passScore +
          "% to complete the course. Here's what to look at again:</p>" +
          '<div class="mt-3 space-y-2">' + result.missed.map(function (x) { return '<div class="p-3 rounded-sm border border-line bg-shell"><div class="text-[13px] text-ink-900 font-medium">' + esc(x.q) + '</div><div class="text-[12.5px] text-ink-700 mt-1">' + esc(x.concept) + '</div><div class="text-[12px] text-ok-600 mt-1">Best answer: ' + esc(x.correct) + "</div></div>"; }).join("") + "</div>" +
          '<div class="mt-5 flex gap-2"><button type="button" class="btn btn-line" data-mod="0">Review The Modules</button><button type="button" class="btn btn-primary" data-quiz-start>Try Again</button></div></div>';
      }
      if (!quiz) {
        return '<div class="card p-6"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">Module 7 · Knowledge Check</div><h3 class="text-[16px] font-semibold text-ink-900 mt-2">A few quick questions</h3>' +
          '<p class="text-[13.5px] text-ink-700 mt-2">' + C.quizSize + " practical questions about what you just learned. You need " + C.passScore + "% to complete the course, and you can try again if you need to.</p>" +
          '<button type="button" class="btn btn-primary mt-5" data-quiz-start>Start Knowledge Check</button></div>';
      }
      return '<div class="card p-6"><div class="text-[11px] uppercase tracking-wider text-ink-500 font-semibold mb-3">Knowledge Check</div>' + quiz.questions.map(function (q, qi) {
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
        '<dt class="text-ink-500">Score</dt><dd class="text-ink-900 font-medium">' + esc(r.score != null ? r.score + "%" : "—") + "</dd></dl>" +
        '<p class="text-[12.5px] text-ink-500 mt-5">This is saved to your staff record, and your Althais Training onboarding item is complete.</p>' +
        '<div class="mt-5 flex justify-center gap-2"><button type="button" class="btn btn-line" data-mod="0">Review The Course</button><a class="btn btn-primary" href="#training">Back To Training</a></div></div>';
    }

    function render() {
      el.innerHTML = '<div class="grid gap-4 md:grid-cols-[240px_minmax(0,1fr)] items-start">' + nav() + "<div>" + moduleHtml() + "</div></div>";
    }

    function finishModule() {
      var m = C.modules[mi];
      var p = done(m.key) ? Promise.resolve() : api("POST", "/api/portal/course/althais/progress", { module: m.key }).then(function (r) { C.progress = r.progress; });
      return p.then(function () { mi = Math.min(mi + 1, C.modules.length - 1); ci = 0; render(); window.scrollTo(0, 0); if (opt.onChange) opt.onChange(); });
    }

    el.addEventListener("click", function (e) {
      var t;
      if ((t = e.target.closest("[data-mod]")) && !t.disabled) { mi = Number(t.dataset.mod); ci = 0; result = null; if (C.modules[mi].key !== "quiz") quiz = null; render(); return; }
      if ((t = e.target.closest("[data-tour]"))) { var key = mi + ":" + ci, v = seen[key] = seen[key] || {}; v.cur = Number(t.dataset.tour); v[v.cur] = 1; render(); return; }
      if ((t = e.target.closest("[data-check]"))) { answered[mi + ":" + ci] = Number(t.dataset.check); render(); return; }
      if (e.target.closest("[data-more]")) { var k2 = mi + ":" + ci; shown[k2] = (shown[k2] || 1) + 1; render(); return; }
      if (e.target.closest("[data-back]")) { if (ci > 0) ci--; else if (mi > 0) { mi--; ci = Math.max(0, C.modules[mi].cards.length - 1); } render(); return; }
      if (e.target.closest("[data-next]")) { if (ci < C.modules[mi].cards.length - 1) { ci++; render(); } else finishModule().catch(function (er) { opt.toast(er.message, "error"); }); return; }
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
    el.addEventListener("change", function (e) {
      if (quiz && e.target.type === "radio") quiz.answers[e.target.name] = Number(e.target.value);
    });

    load();
  }

  window.AlthaisCourse = { mount: mount };
})();
