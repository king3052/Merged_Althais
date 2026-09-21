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

  /* ---------- hero word rotator ---------- */
  var rot = $("#hero-rot");
  if (rot && !reduce) {
    var words = ["reimbursement", "revenue", "cash flow", "clarity", "confidence"], wi = 0;
    setInterval(function () {
      if (document.hidden) return;
      rot.classList.add("out");
      setTimeout(function () {
        wi = (wi + 1) % words.length;
        rot.textContent = words[wi];
        rot.classList.remove("out");
      }, 350);
    }, 2600);
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

  /* ---------- Althea AI: scenario tabs + chat ---------- */
  var SCN = [
    {
      share: [["g", "Complaint", "Chest pain, SOB, 2 hr"], ["g", "Assessment", "Rule out ACS"], ["g", "Visit type", "Emergency Dept"], ["g", "Duration", "47 min, documented"], ["o", "Vitals", "BP 148/92, HR 88"], ["o", "Labs", "Troponin pending"]],
      note: "Blue is what Althea used. Everything else in the note was read and left out.",
      msgs: [["u", "Summarize this visit and code it."],
             ["a", 'Chest pain and shortness of breath for two hours, with an ACS rule-out workup and <mark>47 minutes</mark> of provider time. I would code <mark>CPT 99285</mark> with <mark>R07.9</mark> as the primary diagnosis.'],
             ["u", "Any bundling issues?"],
             ["a", '<mark>0 bundling conflicts</mark> against the CMS PTP edit table. It is ready for your review, and nothing is filed until you approve it.']],
      src: ["Today's note", "CPT 99285", "CMS PTP table"]
    },
    {
      share: [["g", "Complaint", "Cough, fatigue, 5 days"], ["g", "Assessment", "Acute bronchitis, improving"], ["g", "Visit type", "Established patient"], ["o", "Vitals", "BP 122/78, HR 76"], ["o", "Plan", "Supportive care"]],
      note: "The vitals were normal and the plan was routine. Althea found both and left them off the claim.",
      msgs: [["u", "What is the code for this follow-up?"],
             ["a", 'Acute bronchitis, improving, in an <mark>established patient</mark>. That supports <mark>CPT 99213</mark> with <mark>J20.9</mark>. Vitals were normal and the plan was routine, so I left them off the claim.'],
             ["u", "Anything to check before it goes?"],
             ["a", '<mark>0 bundling conflicts.</mark> One review, then it is on its way.']],
      src: ["Today's note", "CPT 99213", "ICD-10 J20.9"]
    },
    {
      share: [["g", "Claim", "CHC-00412, Smith, John"], ["g", "Coded as", "99291 critical care"], ["g", "CMS rule", "30 minutes required"], ["o", "Time in note", "not stated"]],
      note: "Althea flags this at low confidence and offers to open the claim, so it is fixed before filing rather than after a denial.",
      msgs: [["u", "Which claims are at risk today?"],
             ["a", '3 claims flagged. <mark>CHC-00412</mark> has low confidence on the <mark>99291</mark> critical care code. The <mark>30-minute</mark> CMS threshold needs to be documented explicitly. Want me to open it?'],
             ["u", "Open it"],
             ["a", "Opening CHC-00412 now."]],
      src: ["Claims overview", "CPT 99291", "CMS time rule"]
    }
  ];
  var scnShare = $("#scn-share"), scnNote = $("#scn-note"), chatBody = $("#chat-body");
  function renderScn(i) {
    var s = SCN[i]; if (!s) return;
    scnShare.innerHTML = s.share.map(function (r) {
      return '<div class="share-row"><span class="d ' + (r[0] === "o" ? "o" : "") + '"></span><span class="k">' + esc(r[1]) + '</span><span class="val ' + (r[0] === "o" ? "dimv" : "") + '">' + esc(r[2]) + '</span></div>';
    }).join("");
    scnNote.textContent = s.note;
    chatBody.innerHTML = s.msgs.map(function (m, idx) {
      var d = reduce ? 0 : idx * 0.28;
      return '<div class="msg ' + (m[0] === "u" ? "u" : "a") + '" style="animation-delay:' + d + 's">' + (m[0] === "u" ? esc(m[1]) : m[1]) + '</div>';
    }).join("") + '<div class="srcs" style="animation-delay:' + (reduce ? 0 : s.msgs.length * 0.28) + 's">' + s.src.map(function (x) { return '<span>' + esc(x) + '</span>'; }).join("") + '</div>';
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

  /* ---------- Ask Althea: launcher + scripted demo chat panel ---------- */
  var ask = document.getElementById("ask"), ap = document.getElementById("ap");
  if (ask && ap) {
    var apBody = $("#ap-body"), apChips = $("#ap-chips"), apForm = $("#ap-form"), apIn = $("#ap-in"), apSend = $(".ap-send", ap);
    var isOpen = false, scrolled = false, inAlthea = false, busy = false, greeted = false, asked = {};

    var KB = {
      what:  { chip: "What does Althais do?", q: "What does Althais do?", a: 'Althais turns a visit into a claim. You dictate or type it, and it writes the <mark>SOAP note</mark>, suggests the <mark>ICD-10 and CPT codes</mark>, checks them against <mark>CMS edits</mark>, and hands you a claim ready for your review.', src: ["How Althais works"] },
      code:  { chip: "How does the coding work?", q: "How does the coding work?", a: 'Every ICD-10 and CPT suggestion comes with a <mark>confidence score</mark>, so you see at a glance what is solid and what deserves a second look. You can accept, reject, or add your own.', src: ["Step two"] },
      check: { chip: "What is the CMS edit check?", q: "What is the CMS edit check?", a: 'Each claim is compared against the <mark>CMS procedure-to-procedure edit table</mark> before it is filed, so bundling errors are caught before a denial, not weeks after the fact. <mark>Nothing is submitted</mark> without a person approving it.', src: ["Step three", "Step four"] },
      who:   { chip: "Who is it for?", q: "Who is Althais for?", a: 'Practices of all kinds: <mark>independent practices</mark>, urgent care, primary care, direct primary care, specialty groups, rural clinics, physician groups and emergency departments.', src: ["Built for practices like"] },
      time:  { chip: "How much time does it save?", q: "How much time does it save?", a: 'The page shows an <mark>illustrative estimate</mark>: a batch of 100 claims is about 20 hours by hand, versus about 1 hr 30 min with Althais, most of it reading each claim before it is filed. It is an example, and your numbers will differ.', src: ["What it saves you"] },
      start: { chip: "How do I get started?", q: "How do I get started?", a: 'The quickest way is to book a demo. We will walk through a visit on <mark>your own workflow</mark> and reply within one business day. Pricing is on the <a href="/pricing">pricing page</a>.', cta: true }
    };
    var ORDER = ["what", "code", "check", "who", "time", "start"];
    var FALLBACK = { a: 'I can only help with questions about Althais. Try one of the questions below, or book a demo to talk it through with the team.', cta: true };

    /* keyword fallback for typed questions when the live model is unavailable */
    function match(t) {
      t = t.toLowerCase();
      if (/demo|start|book|sign ?up|trial|price|pricing|cost|buy|try|contact/.test(t)) return "start";
      if (/time|save|hour|faster|roi|efficien|minute|worth/.test(t)) return "time";
      if (/who|practice|clinic|specialt|urgent|rural|size|kind of/.test(t)) return "who";
      if (/cms|ncci|bundl|edit|denial|conflict|check|approv/.test(t)) return "check";
      if (/cod|icd|cpt|confidence|modifier|score/.test(t)) return "code";
      if (/althais|althea|soap|note|dictat|voice|work|product|software|tool|platform|feature/.test(t)) return "what";
      return null;
    }

    function scrollDown() { apBody.scrollTop = apBody.scrollHeight; }
    function addMsg(kind, html, delay) {
      var d = document.createElement("div");
      d.className = "msg " + kind;
      if (delay) d.style.animationDelay = delay + "s";
      d.innerHTML = kind === "u" ? esc(html) : html;
      apBody.appendChild(d);
      scrollDown();
      return d;
    }
    function addAnswer(ans) {
      addMsg("a", ans.a);
      if (ans.src) {
        var s = document.createElement("div");
        s.className = "srcs";
        s.innerHTML = ans.src.map(function (x) { return "<span>" + esc(x) + "</span>"; }).join("");
        apBody.appendChild(s);
      }
      if (ans.cta) {
        var l = document.createElement("a");
        l.className = "ap-cta"; l.href = "/demo";
        l.innerHTML = 'Book A Demo <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h10M9 4l4 4-4 4"/></svg>';
        apBody.appendChild(l);
      }
      scrollDown();
    }
    function renderChips() {
      apChips.innerHTML = ORDER.filter(function (k) { return !asked[k]; }).map(function (k) {
        return '<button type="button" data-k="' + k + '">' + esc(KB[k].chip) + "</button>";
      }).join("");
    }
    var hist = [];
    function plain(html) { var t = document.createElement("div"); t.innerHTML = html; return t.textContent; }
    function finish(typing, ans) {
      typing.remove();
      addAnswer(ans);
      busy = false; apSend.disabled = false;
      if (isOpen && window.innerWidth > 640) apIn.focus();
    }
    function liveHtml(reply) { return esc(reply).replace(/\[\[(.+?)\]\]/g, "<mark>$1</mark>"); }

    /* chip questions: scripted, instant answers with sources */
    function ask_(key) {
      if (busy) return;
      busy = true; apSend.disabled = true;
      asked[key] = true;
      renderChips();
      addMsg("u", KB[key].q);
      hist.push({ role: "user", content: KB[key].q });
      var typing = document.createElement("div");
      typing.className = "ap-typing"; typing.innerHTML = "<i></i><i></i><i></i>";
      apBody.appendChild(typing); scrollDown();
      setTimeout(function () {
        hist.push({ role: "assistant", content: plain(KB[key].a) });
        finish(typing, KB[key]);
      }, reduce ? 0 : 750);
    }

    /* typed questions: live answer from the server, scripted fallback if it is unavailable */
    function askLive(text) {
      if (busy) return;
      busy = true; apSend.disabled = true;
      addMsg("u", text);
      hist.push({ role: "user", content: text });
      var typing = document.createElement("div");
      typing.className = "ap-typing"; typing.innerHTML = "<i></i><i></i><i></i>";
      apBody.appendChild(typing); scrollDown();
      var ctl = window.AbortController ? new AbortController() : null;
      var timer = ctl ? setTimeout(function () { ctl.abort(); }, 20000) : null;
      fetch("/api/althea-public", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: hist.slice(-6) }),
        signal: ctl ? ctl.signal : undefined
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; });
      }).then(function (r) {
        if (r.ok && r.d && typeof r.d.reply === "string" && r.d.reply) {
          hist.push({ role: "assistant", content: r.d.reply.replace(/\[\[|\]\]/g, "") });
          finish(typing, { a: liveHtml(r.d.reply) });
        } else { throw new Error(r.d && r.d.error || "unavailable"); }
      }).catch(function () {
        var k = match(text), ans = k ? KB[k] : FALLBACK;
        if (k) asked[k] = true;
        renderChips();
        hist.push({ role: "assistant", content: plain(ans.a) });
        finish(typing, ans);
      }).then(function () { if (timer) clearTimeout(timer); });
    }

    function sync() {
      ap.classList.toggle("open", isOpen);
      ap.setAttribute("aria-hidden", String(!isOpen));
      ask.setAttribute("aria-expanded", String(isOpen));
      ask.classList.toggle("show", scrolled && !inAlthea && !isOpen);
    }
    function openPanel() {
      isOpen = true; sync();
      if (!greeted) {
        greeted = true;
        addMsg("a", "Hi, I&rsquo;m <mark>Althea</mark>. Ask me anything about Althais, or pick a question below.");
        renderChips();
      }
      setTimeout(function () { if (window.innerWidth > 640) apIn.focus(); }, 320);
    }
    function closePanel() { isOpen = false; sync(); ask.focus({ preventScroll: true }); }

    ask.addEventListener("click", openPanel);
    $("#ap-x").addEventListener("click", closePanel);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && isOpen) closePanel(); });
    apChips.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-k]");
      if (b) ask_(b.getAttribute("data-k"));
    });
    apForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var t = apIn.value.trim();
      if (!t || busy) return;
      apIn.value = "";
      askLive(t);
    });

    setTimeout(function () { scrolled = true; sync(); }, reduce ? 0 : 900);
    var alt = document.getElementById("althea");
    if (alt && "IntersectionObserver" in window) {
      new IntersectionObserver(function (es) { inAlthea = es[0].isIntersecting; sync(); }, { threshold: 0.3 }).observe(alt);
    }
  }
})();
