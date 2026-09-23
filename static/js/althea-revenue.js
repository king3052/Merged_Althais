/* Althea's revenue-cycle answers: finding claims, denials, payer intelligence and payments.
 *
 * Shared by both Althea engines (static/js/althea.js on every workspace page, and the EMR's own copy in
 * dashboard.html) so the two can never disagree. Every number is computed the same way the Revenue pages
 * compute it (revenue_claims / _denials / _payer_intelligence / _payments.html), from the organization's
 * own saved claims, never written freely by the model.
 *
 *   AltheaRevenue.handles(intent)                 -> true for the intents below
 *   AltheaRevenue.answer(intent, params, ctx)     -> { spoken, html }
 *     ctx: { claims, payerNotes, payerRules, spokenName }   (any of them optional)
 *   AltheaRevenue.SECTIONS                        -> section name -> Revenue page, for "open denials" etc.
 */
(function () {
  "use strict";

  var INTENTS = { find_claims: 1, denials_summary: 1, payer_intelligence: 1, payments_summary: 1 };

  var SECTIONS = {
    denials: "/revenue/denials", appeals: "/revenue/appeals", payments: "/revenue/payments",
    coding: "/revenue/coding", payer_intelligence: "/revenue/payer-intelligence", payers: "/revenue/payer-intelligence"
  };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function norm(s) { return String(s || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }
  function lc(s) { return String(s || "").toLowerCase(); }
  function money(n) { return "$" + (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function moneySpoken(n) { return "$" + Math.round(Number(n) || 0).toLocaleString("en-US"); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function pct(part, whole) { return whole ? Math.round((part / whole) * 100) : 0; }
  function sum(list) { return list.reduce(function (s, c) { return s + (Number(c.amount) || 0); }, 0); }
  function ageOf(c) {
    if (!c.ts) return "";
    var days = Math.floor((Date.now() - new Date(c.ts).getTime()) / 86400000);
    return isNaN(days) ? "" : (days <= 0 ? "today" : days + "d");
  }
  function joinAnd(items) { return items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1]; }

  /* "UHC", "united", "blue cross", "BCBS" -> the payer names claims are stored under */
  var PAYER_ALIASES = { uhc: "unitedhealthcare", united: "unitedhealthcare", unitedhealth: "unitedhealthcare", bcbs: "bluecrossblueshield", bluecross: "bluecrossblueshield", blueshield: "bluecrossblueshield", bluecrossblueshield: "bluecrossblueshield" };
  function payerMatches(payer, spoken) {
    var a = norm(payer), b = norm(spoken);
    b = PAYER_ALIASES[b] || b;
    return !!(a && b) && (a.indexOf(b) !== -1 || b.indexOf(a) !== -1);
  }
  /* say the payer's real name ("UnitedHealthcare") rather than what was spoken ("UHC") when the matches agree */
  function payerLabel(rows, spoken) {
    var names = {};
    rows.forEach(function (c) { if (c.payer) names[c.payer] = 1; });
    var k = Object.keys(names);
    return k.length === 1 ? k[0] : spoken;
  }
  /* "John Smith" matches "Smith, John": every spoken word appears in the stored name */
  function patientMatches(name, spoken) {
    var have = lc(name).split(/[^a-z]+/).filter(Boolean);
    var want = lc(spoken).split(/[^a-z]+/).filter(function (w) { return w.length > 1; });
    return want.length > 0 && want.every(function (w) { return have.some(function (h) { return h === w || h.indexOf(w) === 0; }); });
  }
  /* a claim ID, CPT or ICD-10 code */
  function codeMatches(c, q) {
    var n = norm(q);
    return !!n && [c.claimId, c.cpt, c.icd].some(function (v) { return norm(v).indexOf(n) !== -1; });
  }

  /* Status words -> the same groupings the Claims page's filter chips use */
  var STATUS = {
    denied:    { label: "denied",            test: function (s) { return s === "denied"; } },
    appealed:  { label: "appealed",          test: function (s) { return s === "appealed"; } },
    paid:      { label: "paid",              test: function (s) { return s === "paid"; } },
    pending:   { label: "pending",           test: function (s) { return /submitted|ready to submit|pending review/.test(s); } },
    submitted: { label: "submitted",         test: function (s) { return s === "submitted"; } },
    ready:     { label: "ready-to-submit",   test: function (s) { return s === "ready to submit"; } },
    review:    { label: "in-review",         test: function (s) { return s === "pending review"; } },
    draft:     { label: "draft",             test: function (s) { return s === "draft"; } },
    unpaid:    { label: "unpaid",            test: function (s) { return s !== "paid" && s !== "draft"; } }
  };
  var STATUS_ALIASES = { denial: "denied", denials: "denied", appeal: "appealed", appeals: "appealed", outstanding: "unpaid", open: "unpaid", drafts: "draft", "ready to submit": "ready", "pending review": "review", "in review": "review" };
  function statusFilter(word) {
    var w = lc(word).trim();
    return STATUS[STATUS_ALIASES[w] || w] || null;
  }

  function realClaims(ctx) { return (ctx.claims || []).filter(function (c) { return c && !c.isPlaceholder; }); }
  function link(href, label) { return '<div class="alt-rev-link"><a href="' + href + '">' + esc(label) + " →</a></div>"; }
  function claimRow(c, extra) {
    return '<div class="alt-rev-row"><b>' + esc(c.patient || "—") + '</b> <span class="mono alt-rev-muted">' + esc(c.claimId || "") + "</span>" +
      '<div class="alt-rev-muted">' + esc(c.status || "—") + " · " + esc(c.payer || "—") + " · " + esc(c.cpt || "—") + " · <b>" + money(c.amount) + "</b>" + (extra ? " · " + extra : "") + "</div></div>";
  }

  /* ---------- find_claims: "show me denied Aetna claims", "claims for John Smith", "find claim CL-2026-003" ---------- */
  function findClaims(params, ctx) {
    var sn = ctx.spokenName || String;
    var st = params.status ? statusFilter(params.status) : null;
    var rows = realClaims(ctx).filter(function (c) {
      if (st && !st.test(lc(c.status))) return false;
      if (params.payer && !payerMatches(c.payer, params.payer)) return false;
      if (params.patient_name && !patientMatches(c.patient, params.patient_name)) return false;
      if (params.query && !codeMatches(c, params.query)) return false;
      return true;
    }).sort(function (a, b) { return (Number(b.amount) || 0) - (Number(a.amount) || 0); });

    var what = (st ? st.label + " " : "") + "claim";
    var scope = (params.payer ? " with " + payerLabel(rows, params.payer) : "") + (params.patient_name ? " for " + params.patient_name : "") + (params.query ? " matching " + params.query : "");
    if (!rows.length) {
      var none = "No " + what + "s" + scope + " found.";
      return { spoken: none, html: esc(none) + link("/revenue/claims", "Open Claims") };
    }
    var total = sum(rows);
    var spoken = "Found " + plural(rows.length, what, what + "s") + scope + ", totaling " + moneySpoken(total) + ".";
    if (rows.length <= 3) {
      spoken += " " + rows.map(function (c) {
        var who = params.patient_name ? (c.claimId || "One") : sn(c.patient);   /* already said whose claims these are */
        return who + ", " + lc(c.status || "") + ", " + moneySpoken(c.amount) + (params.payer ? "" : " to " + (c.payer || "the payer"));
      }).join(". ") + ".";
    } else {
      spoken += " The largest are " + joinAnd(rows.slice(0, 3).map(function (c) { return sn(c.patient) + " at " + moneySpoken(c.amount); })) + ".";
    }
    var shown = rows.slice(0, 8);
    var html = '<div class="alt-rev-head">' + plural(rows.length, what, what + "s") + esc(scope) + " · <b>" + money(total) + "</b></div>" +
      shown.map(function (c) { return claimRow(c, c.denialReason ? esc(c.denialReason) : ""); }).join("") +
      (rows.length > shown.length ? '<div class="alt-rev-muted">+ ' + (rows.length - shown.length) + " more</div>" : "") +
      link("/revenue/claims", "Open Claims");
    return { spoken: spoken, html: html };
  }

  /* ---------- denials_summary: same rows as the Denials page (Denied + Appealed) ---------- */
  function denialsSummary(params, ctx) {
    var sn = ctx.spokenName || String;
    var rows = realClaims(ctx).filter(function (c) {
      var s = lc(c.status);
      return (s === "denied" || s === "appealed") && (!params.payer || payerMatches(c.payer, params.payer));
    });
    var scope = params.payer ? " with " + payerLabel(rows, params.payer) : "";
    if (!rows.length) {
      var none = "No denied claims" + scope + " right now.";
      return { spoken: none, html: esc(none) + link("/revenue/denials", "Open Denials") };
    }
    var needs = rows.filter(function (c) { return lc(c.status) === "denied"; }).sort(function (a, b) { return (Number(b.amount) || 0) - (Number(a.amount) || 0); });
    var appealed = rows.length - needs.length;
    var total = sum(rows), needsAmt = sum(needs);

    var tally = function (key) {
      var m = {};
      rows.forEach(function (c) { var k = c[key]; if (k) m[k] = (m[k] || 0) + 1; });
      return Object.keys(m).map(function (k) { return { name: k, n: m[k] }; }).sort(function (a, b) { return b.n - a.n; });
    };
    var byPayer = tally("payer"), byReason = tally("denialReason");

    var spoken = "There " + (rows.length === 1 ? "is 1 denied claim" : "are " + rows.length + " denied claims") + scope + ", totaling " + moneySpoken(total) + ". " +
      (needs.length ? plural(needs.length, "still needs", "still need") + " an appeal, worth " + moneySpoken(needsAmt) : "All of them have been appealed") +
      (needs.length && appealed ? ", and " + appealed + " " + (appealed === 1 ? "is" : "are") + " already appealed" : "") + ".";
    if (!params.payer && byPayer.length > 1) spoken += " Most come from " + byPayer[0].name + ", with " + byPayer[0].n + ".";
    if (byReason.length) spoken += " The top reason is " + lc(byReason[0].name) + ".";

    var html = '<div class="alt-rev-head">' + plural(rows.length, "denial", "denials") + esc(scope) + " · <b>" + money(total) + "</b></div>" +
      '<div class="alt-rev-muted">Needs appeal: <b>' + needs.length + "</b> (" + money(needsAmt) + ") · Appealed: <b>" + appealed + "</b></div>" +
      (!params.payer && byPayer.length ? '<div class="alt-rev-muted">By payer: ' + byPayer.map(function (p) { return esc(p.name) + " " + p.n; }).join(" · ") + "</div>" : "") +
      (byReason.length ? '<div class="alt-rev-muted">Reasons: ' + byReason.slice(0, 3).map(function (r) { return esc(r.name) + " (" + r.n + ")"; }).join(" · ") + "</div>" : "") +
      needs.slice(0, 5).map(function (c) { return claimRow(c, [c.denialReason ? esc(c.denialReason) : "", ageOf(c)].filter(Boolean).join(" · ")); }).join("") +
      link("/revenue/denials", "Open Denials") + (needs.length ? link("/revenue/appeals", "Work appeals") : "");
    return { spoken: spoken, html: html };
  }

  /* ---------- payer_intelligence: per-payer stats as the Payer Intelligence page computes them, plus rules and notes ---------- */
  function payerStats(claims) {
    var by = {};
    claims.forEach(function (c) {
      var p = c.payer || "—", s = lc(c.status), amt = Number(c.amount) || 0;
      var e = by[p] || (by[p] = { name: p, total: 0, paid: 0, denied: 0, billed: 0, collected: 0 });
      e.total++; e.billed += amt;
      if (s === "paid") { e.paid++; e.collected += amt; }
      if (s === "denied" || s === "appealed") e.denied++;
    });
    return Object.keys(by).map(function (k) { var e = by[k]; e.approval = pct(e.paid, e.total); e.denialRate = e.total ? (e.denied / e.total) * 100 : 0; return e; });
  }

  function payerIntelligence(params, ctx) {
    var claims = realClaims(ctx), notes = ctx.payerNotes || [], rules = ctx.payerRules || [];

    if (params.payer) {
      var stats = payerStats(claims.filter(function (c) { return payerMatches(c.payer, params.payer); }));
      var myNotes = notes.filter(function (n) { return payerMatches(n.payer, params.payer); });
      var myRules = rules.filter(function (r) {
        if (!payerMatches(r.payer, params.payer)) return false;
        return !params.query || norm(r.cpt).indexOf(norm(params.query)) !== -1 || lc(r.rule).indexOf(lc(params.query)) !== -1;
      });
      if (!stats.length && !myNotes.length && !myRules.length) {
        var none = "I don't have any claims, rules or policy notes for " + params.payer + " yet.";
        return { spoken: none, html: esc(none) + link("/revenue/payer-intelligence", "Open Payer Intelligence") };
      }
      var spoken = "", html = "";
      stats.forEach(function (p) {
        spoken += p.name + ": " + plural(p.total, "claim", "claims") + ", " + p.approval + " percent approved, " + p.denialRate.toFixed(0) + " percent denied, " + moneySpoken(p.billed) + " billed and " + moneySpoken(p.collected) + " collected. ";
        html += '<div class="alt-rev-head">' + esc(p.name) + "</div>" +
          '<div class="alt-rev-muted">Claims <b>' + p.total + "</b> · Approval <b>" + p.approval + "%</b> · Denial <b>" + p.denialRate.toFixed(1) + "%</b></div>" +
          '<div class="alt-rev-muted">Billed <b>' + money(p.billed) + "</b> · Collected <b>" + money(p.collected) + "</b></div>";
      });
      if (!stats.length) { spoken += "No claims with " + params.payer + " yet. "; html += '<div class="alt-rev-head">' + esc(params.payer) + '</div><div class="alt-rev-muted">No claims yet.</div>'; }
      if (myRules.length) {
        var high = myRules.filter(function (r) { return /high/i.test(r.impact || ""); }).length;
        spoken += params.query
          ? "For " + params.query + ": " + myRules[0].rule + (myRules.length > 1 ? " Plus " + plural(myRules.length - 1, "more rule", "more rules") + "." : "")
          : plural(myRules.length, "payer rule", "payer rules") + " on file" + (high ? ", " + high + " high impact" : "") + ".";
        html += '<div class="alt-rev-sub">Payer rules' + (params.query ? " · " + esc(params.query) : "") + "</div>" +
          myRules.slice(0, 4).map(function (r) { return '<div class="alt-rev-row"><span class="mono">' + esc(r.cpt || "—") + "</span> " + esc(r.rule || "") + ' <span class="alt-rev-muted">(' + esc(r.impact || "") + ")</span></div>"; }).join("");
      } else if (params.query) {
        spoken += "No rule on file for " + params.query + ". ";
      }
      if (myNotes.length) {
        if (!params.query) spoken += " Latest policy note: " + myNotes[0].text;
        html += '<div class="alt-rev-sub">Policy notes</div>' + myNotes.slice(0, 3).map(function (n) { return '<div class="alt-rev-row">' + esc(n.text) + ' <span class="alt-rev-muted">' + esc(n.date || "") + "</span></div>"; }).join("");
      }
      return { spoken: spoken.trim(), html: html + link("/revenue/payer-intelligence", "Open Payer Intelligence") };
    }

    var all = payerStats(claims).sort(function (a, b) { return b.total - a.total; });
    if (!all.length) {
      var empty = "No payer data yet. Once claims are submitted I can compare payers.";
      return { spoken: empty, html: esc(empty) + link("/revenue/payer-intelligence", "Open Payer Intelligence") };
    }
    var totalClaims = all.reduce(function (s, p) { return s + p.total; }, 0), totalPaid = all.reduce(function (s, p) { return s + p.paid; }, 0);
    var worst = all.slice().sort(function (a, b) { return b.denialRate - a.denialRate; })[0];
    var best = all.slice().sort(function (a, b) { return b.approval - a.approval; })[0];
    var sp = plural(all.length, "payer", "payers") + " on file, with an average approval rate of " + pct(totalPaid, totalClaims) + " percent.";
    if (all.length > 1) {
      if (worst.denialRate > 0) sp += " " + worst.name + " has the highest denial rate, at " + worst.denialRate.toFixed(0) + " percent.";
      if (best !== worst) sp += " " + best.name + " approves the most, at " + best.approval + " percent.";
    }
    if (notes.length) sp += " Latest policy note, for " + notes[0].payer + ": " + notes[0].text;
    var h = '<div class="alt-rev-head">' + plural(all.length, "payer", "payers") + " · avg approval <b>" + pct(totalPaid, totalClaims) + "%</b></div>" +
      all.slice(0, 8).map(function (p) {
        return '<div class="alt-rev-row"><b>' + esc(p.name) + '</b><div class="alt-rev-muted">' + p.total + " claims · " + p.approval + "% approved · " + p.denialRate.toFixed(1) + "% denied · " + money(p.billed) + "</div></div>";
      }).join("") +
      (notes.length ? '<div class="alt-rev-sub">Recent policy notes</div>' + notes.slice(0, 3).map(function (n) { return '<div class="alt-rev-row"><b>' + esc(n.payer) + "</b> " + esc(n.text) + "</div>"; }).join("") : "") +
      link("/revenue/payer-intelligence", "Open Payer Intelligence");
    return { spoken: sp, html: h };
  }

  /* ---------- payments_summary: same split as the Payments page (Paid vs everything not paid or draft) ---------- */
  function paymentsSummary(params, ctx) {
    var claims = realClaims(ctx).filter(function (c) { return !params.payer || payerMatches(c.payer, params.payer); });
    var scope = params.payer ? " from " + payerLabel(claims, params.payer) : "";
    var paid = claims.filter(function (c) { return lc(c.status) === "paid"; });
    var pending = claims.filter(function (c) { var s = lc(c.status); return s !== "paid" && s !== "draft"; });
    if (!paid.length && !pending.length) {
      var none = "No payments or outstanding claims" + scope + " yet.";
      return { spoken: none, html: esc(none) + link("/revenue/payments", "Open Payments") };
    }
    var collected = sum(paid), outstanding = sum(pending), avg = paid.length ? collected / paid.length : 0;
    var by = {};
    paid.forEach(function (c) { var p = c.payer || "Unknown"; by[p] = (by[p] || 0) + (Number(c.amount) || 0); });
    var top = Object.keys(by).map(function (k) { return { name: k, total: by[k] }; }).sort(function (a, b) { return b.total - a.total; });

    var spoken = "Collected " + moneySpoken(collected) + scope + " across " + plural(paid.length, "payment", "payments") +
      (paid.length ? ", averaging " + moneySpoken(avg) : "") + ". " + moneySpoken(outstanding) + " is still outstanding across " + plural(pending.length, "claim", "claims") + ".";
    if (!params.payer && top.length > 1) spoken += " " + top[0].name + " has paid the most, " + moneySpoken(top[0].total) + ".";

    var html = '<div class="alt-rev-head">Collected <b>' + money(collected) + "</b>" + esc(scope) + "</div>" +
      '<div class="alt-rev-muted">' + plural(paid.length, "payment", "payments") + " · avg " + money(avg) + "</div>" +
      '<div class="alt-rev-muted">Outstanding <b>' + money(outstanding) + "</b> across " + plural(pending.length, "claim", "claims") + "</div>" +
      (!params.payer && top.length ? '<div class="alt-rev-sub">By payer</div>' + top.slice(0, 5).map(function (p) { return '<div class="alt-rev-row">' + esc(p.name) + " · <b>" + money(p.total) + "</b></div>"; }).join("") : "") +
      link("/revenue/payments", "Open Payments");
    return { spoken: spoken, html: html };
  }

  window.AltheaRevenue = {
    SECTIONS: SECTIONS,
    handles: function (intent) { return !!INTENTS[intent]; },
    answer: function (intent, params, ctx) {
      params = params || {}; ctx = ctx || {};
      if (intent === "find_claims") return findClaims(params, ctx);
      if (intent === "denials_summary") return denialsSummary(params, ctx);
      if (intent === "payer_intelligence") return payerIntelligence(params, ctx);
      if (intent === "payments_summary") return paymentsSummary(params, ctx);
      return null;
    }
  };
})();
