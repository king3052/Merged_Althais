(function () {
  "use strict";

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  /* ---------- reveal on scroll ---------- */
  var rv = $$(".rv");
  if ("IntersectionObserver" in window) {
    var rvObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); rvObs.unobserve(e.target); } });
    }, { threshold: 0.08, rootMargin: "0px 0px -6% 0px" });
    rv.forEach(function (el) { rvObs.observe(el); });
  } else {
    rv.forEach(function (el) { el.classList.add("in"); });
  }

  /* ---------- how it works: sticky step tracker ---------- */
  var cards = $$(".step-card");
  var copies = $$(".step-copy");
  var tl = $$(".timeline li");
  function setStep(i) {
    copies.forEach(function (c) { c.classList.toggle("on", +c.getAttribute("data-step") === i); });
    tl.forEach(function (li, idx) { li.classList.toggle("done", idx < i); li.classList.toggle("on", idx === i); });
  }
  if ("IntersectionObserver" in window && cards.length) {
    var stepObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) setStep(+e.target.getAttribute("data-step")); });
    }, { rootMargin: "-42% 0px -42% 0px", threshold: 0 });
    cards.forEach(function (c) { stepObs.observe(c); });
  }
  tl.forEach(function (li, idx) {
    li.style.cursor = "pointer";
    li.addEventListener("click", function () {
      var c = cards[idx]; if (c) c.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    });
  });

  /* ---------- typewriter (step one) ---------- */
  var tw = $("#tw"), twOut = $("#tw-out");
  if (tw && twOut) {
    var full = tw.getAttribute("data-text") || "";
    var started = false;
    function typeIt() {
      if (started) return; started = true;
      if (reduce) { twOut.textContent = full; return; }
      var i = 0;
      (function tick() {
        twOut.textContent = full.slice(0, ++i);
        if (i < full.length) setTimeout(tick, 22);
      })();
    }
    if ("IntersectionObserver" in window) {
      var twObs = new IntersectionObserver(function (es) {
        if (es[0].isIntersecting) { typeIt(); twObs.disconnect(); }
      }, { threshold: 0.4 });
      twObs.observe(tw);
    } else { typeIt(); }
  }

  /* ---------- "what slips through" chips + chart ---------- */
  var VT = {
    office: { name: "office-visit", n: 21, bars: [["Undercoded E/M level", 8], ["Missing modifier", 5], ["Documentation gap", 4], ["NCCI bundling", 3], ["Timely filing", 1]] },
    ed:     { name: "emergency", n: 27, bars: [["High complexity not supported", 9], ["Duration not documented", 7], ["NCCI bundling", 6], ["Missing modifier", 3], ["Documentation gap", 2]] },
    urgent: { name: "urgent-care", n: 18, bars: [["Undercoded E/M level", 6], ["Missing modifier", 4], ["Documentation gap", 4], ["NCCI bundling", 3], ["Timely filing", 1]] },
    tele:   { name: "telehealth", n: 15, bars: [["Place of service / modifier", 6], ["Documentation gap", 4], ["Undercoded E/M level", 3], ["NCCI bundling", 1], ["Timely filing", 1]] },
    proc:   { name: "procedure", n: 31, bars: [["NCCI bundling", 12], ["Missing modifier", 9], ["Documentation gap", 5], ["Medical necessity", 3], ["Timely filing", 2]] },
    crit:   { name: "critical-care", n: 34, bars: [["Time threshold not documented", 14], ["NCCI bundling", 8], ["Missing modifier", 6], ["Documentation gap", 4], ["Medical necessity", 2]] }
  };
  var bars = $("#bd-bars"), bdN = $("#bd-n"), bdLead = $("#bd-lead");
  function renderBoard(key) {
    var d = VT[key]; if (!d || !bars) return;
    bdN.textContent = d.n + "%";
    bdLead.textContent = "of " + d.name + " claims in this example carried a fixable issue.";
    bars.innerHTML = d.bars.map(function (b) {
      return '<div class="bar-row"><span>' + esc(b[0]) + '</span><span class="track"><span class="fill" data-w="' + (b[1] / 16 * 100).toFixed(1) + '"></span></span><span class="v">' + b[1] + '%</span></div>';
    }).join("");
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      $$(".fill", bars).forEach(function (f) { f.style.width = f.getAttribute("data-w") + "%"; });
    }); });
  }
  renderBoard("office");
  $$("#vt-chips .chip-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      $$("#vt-chips .chip-btn").forEach(function (x) { x.classList.toggle("on", x === b); });
      renderBoard(b.getAttribute("data-vt"));
    });
  });

  /* ---------- in action: scenario tabs ---------- */
  var SCN = [
    {
      share: [["g", "Complaint", "Chest pain, SOB, 2 hr"], ["g", "Assessment", "Rule out ACS"], ["g", "Visit type", "Emergency Dept"], ["g", "Duration", "47 min, documented"], ["o", "Vitals", "BP 148/92, HR 88"], ["o", "Labs", "Troponin pending"]],
      note: "Blue is what the code actually rests on. Everything else in the note was read and left out.",
      who: ["ED", "Emergency visit, demo patient", "Ready for review"], title: "Chest pain, ED visit",
      body: ['Documented <mark>chest pain with shortness of breath</mark> and an ACS rule-out workup, with <mark>47 minutes</mark> of provider time.',
             'The setting and the level of decision-making support <mark>CPT 99285</mark>, with <mark>R07.9</mark> as the primary diagnosis.',
             'NCCI check: <mark>0 bundling conflicts</mark>. Nothing is filed until you approve it.']
    },
    {
      share: [["g", "Complaint", "Cough, fatigue, 5 days"], ["g", "Assessment", "Acute bronchitis, improving"], ["g", "Visit type", "Established patient"], ["o", "Vitals", "BP 122/78, HR 76"], ["o", "Plan", "Supportive care"]],
      note: "The vitals were normal and the plan was routine. Both were found and left off the claim.",
      who: ["FU", "Follow-up visit, demo patient", "Ready for review"], title: "Follow-up, established patient",
      body: ['Documented <mark>acute bronchitis, improving</mark> after five days of cough and mild fatigue in an <mark>established patient</mark>.',
             'That supports <mark>CPT 99213</mark>, with <mark>J20.9</mark> as the diagnosis.',
             'NCCI check: <mark>0 bundling conflicts</mark>. One review, then it is on its way.']
    },
    {
      share: [["g", "Claim", "CHC-00412, Smith, John"], ["g", "Coded as", "99291 critical care"], ["g", "CMS rule", "30 minutes required"], ["o", "Time in note", "not stated"]],
      note: "Althea flags this at low confidence and offers to open the claim, so it is fixed before filing rather than after a denial.",
      who: ["!", "Needs one more line", "Flagged by Althea"], title: "Critical care, missing the time",
      body: ['This claim was coded <mark>99291 critical care</mark>, but the note does not <mark>document the 30 minutes</mark> CMS requires for that code.',
             'Adding the time, or choosing the code the note does support, clears it before it is filed.',
             'Without that line, this is the kind of claim that comes back as a denial two weeks later.']
    }
  ];
  var scnShare = $("#scn-share"), scnNote = $("#scn-note"), scnPaper = $("#scn-paper");
  function renderScn(i) {
    var s = SCN[i]; if (!s) return;
    scnShare.innerHTML = s.share.map(function (r) {
      return '<div class="share-row"><span class="d ' + (r[0] === "o" ? "o" : "") + '"></span><span class="k">' + esc(r[1]) + '</span><span class="val ' + (r[0] === "o" ? "dimv" : "") + '">' + esc(r[2]) + '</span></div>';
    }).join("");
    scnNote.textContent = s.note;
    scnPaper.innerHTML = '<div class="who"><span class="avatar">' + esc(s.who[0]) + '</span><div><b>' + esc(s.who[1]) + '</b><span>' + esc(s.who[2]) + '</span></div></div>' +
      '<h4>' + esc(s.title) + '</h4>' + s.body.map(function (p, idx) { return '<p' + (idx === s.body.length - 1 ? ' class="sign"' : '') + '>' + p + '</p>'; }).join("");
  }
  renderScn(0);
  $$(".scn-tab").forEach(function (t, idx) {
    t.addEventListener("click", function () {
      $$(".scn-tab").forEach(function (x) { x.classList.toggle("on", x === t); x.setAttribute("aria-selected", String(x === t)); });
      renderScn(idx);
    });
  });

  /* ---------- testimonials ---------- */
  var Q = [
    ["The coding engine caught a bundling error I would have missed, the kind that comes back as a denial two weeks later when you’ve already moved on.", "Dr. James Tarin", "Tarin Health DPC · Pecos, TX", "JT"],
    ["NCCI real-time checking is something I’ve been asking vendors to build for years. Most practices don’t find out about bundling conflicts until after the rejection.", "Bryan Cox", "Director, Revenue Cycle · MCH Odessa, TX", "BC"],
    ["Small clinics running lean can’t afford a billing department. Althais is built for exactly that setting, and the Althea voice layer is something I haven’t seen anywhere else.", "Dr. Ashish Gupta, MD MBA", "Deputy CMO · Hamilton Health Box", "AG"]
  ];
  var qt = $("#q-text"), qby = $("#q-by"), qav = $("#q-av"), qn = $("#q-name"), qr = $("#q-role");
  $$(".qdot").forEach(function (d) {
    d.addEventListener("click", function () {
      var i = +d.getAttribute("data-q");
      $$(".qdot").forEach(function (x) { x.classList.toggle("on", x === d); });
      qt.style.opacity = 0; qby.style.opacity = 0;
      setTimeout(function () {
        qt.textContent = Q[i][0]; qn.textContent = Q[i][1]; qr.textContent = Q[i][2]; qav.textContent = Q[i][3];
        qt.style.opacity = 1; qby.style.opacity = 1;
      }, reduce ? 0 : 220);
    });
  });

  /* ---------- savings comparison bars ---------- */
  var cmp = $("#cmp");
  if (cmp) {
    var rows = $$(".cmp-row", cmp), maxH = 0;
    rows.forEach(function (r) { maxH = Math.max(maxH, +r.getAttribute("data-h")); });
    var drawCmp = function () {
      rows.forEach(function (r) {
        var h = +r.getAttribute("data-h"), a = +r.getAttribute("data-a");
        var b = $(".b", r), f = $("i", r);
        b.style.width = (h / maxH * 100).toFixed(1) + "%";
        f.style.width = (a / h * 100).toFixed(1) + "%";
      });
    };
    if (reduce || !("IntersectionObserver" in window)) { drawCmp(); }
    else {
      var cObs = new IntersectionObserver(function (es) {
        if (es[0].isIntersecting) { drawCmp(); cObs.disconnect(); }
      }, { threshold: 0.25 });
      cObs.observe(cmp);
    }
  }
})();
