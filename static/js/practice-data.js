/* Shared data for the Overview workspace pages (Tasks, AI Inbox, Analytics, Recent Activity).
 *
 *   PracticeData.local()                 the EMR's records for this organization (same browser storage keys the
 *                                        EMR, Revenue and Overview pages read): patients, claims, appointments,
 *                                        visit notes, payer notes and the EMR activity log
 *   PracticeData.tasks                   the practice's shared task list on the server (GET/PUT /api/tasks):
 *                                        { rev, tasks: [], dismissed: {alertId: {...}}, log: [] }
 *                                        .load() / .save(fn, okMsg) / .canEdit(), same conflict handling as Staff
 *   PracticeData.alerts(local, staff)    AI Inbox alerts: fixed rules over the real records, each with a stable id
 *                                        so it can be dismissed or turned into a task
 *   PracticeData.activity(local, staff, tasks)   one feed from the EMR log, Staff history and Tasks history
 */
(function () {
  "use strict";

  var user = window.__ALTHAIS_USER__ || {};
  var NS = (function () {
    var base = (user.organization || user.email || "unknown").trim().toLowerCase();
    return "org_" + base.replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") + "::";
  })();

  /* ---------- helpers ---------- */
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function load(key, fallback) { try { var raw = localStorage.getItem(key); if (!raw) return fallback; var v = JSON.parse(raw); return v == null ? fallback : v; } catch (e) { return fallback; } }
  function todayIso() { var d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
  function isoOffset(days) { var d = new Date(); d.setDate(d.getDate() + days); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
  function money(n) { return "$" + (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function moneyShort(n) { n = Number(n) || 0; return n >= 10000 ? "$" + (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : "$" + Math.round(n).toLocaleString("en-US"); }
  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).length <= 10 ? iso + "T00:00:00" : iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" });
  }
  function relTime(ts) {
    var t = typeof ts === "number" ? ts : new Date(ts).getTime();
    if (isNaN(t)) return "";
    var diff = Math.floor((Date.now() - t) / 1000);
    if (diff < 45) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + " min ago";
    if (diff < 86400) return Math.floor(diff / 3600) + " hr ago";
    if (diff < 7 * 86400) return Math.floor(diff / 86400) + "d ago";
    return fmtDate(new Date(t).toISOString());
  }
  function daysUntil(iso) {
    if (!iso) return null;
    var t = new Date(String(iso).slice(0, 10) + "T00:00:00").getTime(), n = new Date(todayIso() + "T00:00:00").getTime();
    return isNaN(t) ? null : Math.round((t - n) / 86400000);
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function stripTags(html) { var d = document.createElement("div"); d.innerHTML = String(html || ""); return d.textContent || ""; }
  function currentUserName() { return user.full_name || user.email || "Me"; }
  function uid(prefix) { return (prefix || "id") + "_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }

  /* ---------- the EMR's records ---------- */
  function local() {
    return {
      patients: load(NS + "althais.patients.v1", []),
      claims: load(NS + "althais.claims.v1", []).filter(function (c) { return c && !c.isPlaceholder; }),
      appts: load(NS + "althais.appointments.v1", []),
      notes: load(NS + "althais.notes.v1", []),
      activity: load(NS + "althais.activity.v1", []),
      payerNotes: load("althais.payer_notes.v1", [])   /* not namespaced: matches the EMR's own key */
    };
  }

  /* ---------- tasks document (server) ---------- */
  var tdoc = null, tEditable = false, tLoading = null;
  function normalizeTasks(d) {
    d = (d && typeof d === "object") ? d : {};
    d.rev = d.rev || 0;
    d.tasks = Array.isArray(d.tasks) ? d.tasks : [];
    d.dismissed = (d.dismissed && typeof d.dismissed === "object") ? d.dismissed : {};
    d.log = Array.isArray(d.log) ? d.log : [];
    return d;
  }
  var tasks = {
    load: function (force) {
      if (tdoc && !force) return Promise.resolve(tdoc);
      if (tLoading && !force) return tLoading;
      tLoading = fetch("/api/tasks", { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("Could not load tasks (" + r.status + ")."); return r.json(); })
        .then(function (res) { tdoc = normalizeTasks(res.data); tEditable = !!res.can_edit; tLoading = null; return tdoc; })
        .catch(function (e) { tLoading = null; throw e; });
      return tLoading;
    },
    canEdit: function () { return tEditable; },
    /* apply a change and save it; if someone else saved first, re-apply it to their version */
    save: function (fn, okMsg) {
      return tasks.load().then(function (base) {
        function attempt(from, retries) {
          var draft = normalizeTasks(JSON.parse(JSON.stringify(from)));
          fn(draft);
          if (draft.log.length > 300) draft.log = draft.log.slice(-300);
          return fetch("/api/tasks", { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft) })
            .then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }); })
            .then(function (res) {
              if (res.status === 200) { tdoc = normalizeTasks(res.body.data); return tdoc; }
              if (res.status === 409 && retries > 0) return attempt(normalizeTasks(res.body.data), retries - 1);
              throw new Error((res.body && res.body.error) || "Could not save.");
            });
        }
        return attempt(base, 2);
      }).then(function (d) { if (okMsg) toast(okMsg); return d; }, function (e) { toast(e.message || "Could not save.", "error"); throw e; });
    },
    log: function (d, text, href) { d.log.push({ text: text, href: href || "", at: new Date().toISOString(), by: currentUserName() }); }
  };

  /* ---------- AI Inbox alerts ----------
     Fixed rules over the real records. level: High (money or patient safety at stake now), Medium (act this week),
     Low (housekeeping). Each alert carries a stable id, a suggested task and where to go to fix it. */
  var LEVEL_RANK = { High: 0, Medium: 1, Low: 2 };

  function alerts(L, staff) {
    var out = [], today = todayIso();
    function add(a) { out.push(a); }

    L.claims.forEach(function (c) {
      var s = String(c.status || "").toLowerCase(), amt = Number(c.amount) || 0;
      if (s === "denied") add({
        id: "denied:" + (c.claimId || c.mrn + c.cpt), level: "High", category: "Claims",
        title: "Denied claim needs an appeal: " + (c.patient || "Unknown patient"),
        detail: [c.claimId, c.payer, c.cpt, money(amt), c.denialReason].filter(Boolean).join(" · "),
        href: "/revenue/appeals", hrefLabel: "Appeal", value: amt,
        task: "Appeal denied claim " + (c.claimId || "") + " for " + (c.patient || "patient") + " (" + money(amt) + ")"
      });
      var conf = Number(c.conf || 0);
      if (conf && conf < 75 && /submitted|ready to submit|pending review/.test(s)) add({
        id: "risk:" + (c.claimId || c.mrn + c.cpt), level: conf < 60 ? "High" : "Medium", category: "Claims",
        title: "Claim at risk of denial: " + (c.patient || "Unknown patient"),
        detail: (c.claimId || "") + " · " + conf + "% AI confidence · " + (c.payer || "") + " · " + money(amt),
        href: "/emr#claims", hrefLabel: "Review", value: amt,
        task: "Review claim " + (c.claimId || "") + " for " + (c.patient || "patient") + " before it's denied"
      });
      if (s === "pending review") add({
        id: "review:" + (c.claimId || c.mrn + c.cpt), level: "Low", category: "Claims",
        title: "Claim waiting for review: " + (c.patient || "Unknown patient"),
        detail: (c.claimId || "") + " · " + (c.cpt || "") + " · " + money(amt),
        href: "/revenue/claims", hrefLabel: "Open", value: amt,
        task: "Review and submit claim " + (c.claimId || "") + " for " + (c.patient || "patient")
      });
    });

    var notesToday = L.notes.filter(function (n) { return n.date === today; });
    L.appts.filter(function (a) { return a.date === today; }).forEach(function (a) {
      if (notesToday.some(function (n) { return n.mrn === a.mrn; })) return;
      var past = a.time && new Date(today + "T" + a.time) < new Date();
      add({
        id: "docgap:" + today + ":" + a.mrn + ":" + (a.time || ""), level: past ? "Medium" : "Low", category: "Clinical",
        title: (past ? "Visit note missing: " : "Upcoming visit has no note yet: ") + (a.patient || "Unknown patient"),
        detail: (a.time || "") + " · " + (a.type || "Visit") + (a.provider ? " · " + a.provider : ""),
        href: "/emr", hrefLabel: "Chart",
        task: "Finish the visit note for " + (a.patient || "patient") + " (" + (a.time || today) + ")"
      });
    });

    L.patients.forEach(function (p) {
      (p.priorAuths || []).forEach(function (pa) {
        if (!/pending/i.test(pa.status || "")) return;
        add({
          id: "pa:" + p.mrn + ":" + (pa.id || pa.service), level: "Medium", category: "Clinical",
          title: "Prior authorization pending: " + p.name,
          detail: [pa.service, pa.payer, pa.submitted ? "submitted " + fmtDate(pa.submitted) : ""].filter(Boolean).join(" · "),
          href: "/emr", hrefLabel: "Chart",
          task: "Follow up with " + (pa.payer || "the payer") + " on the prior auth for " + p.name + (pa.service ? " (" + pa.service + ")" : "")
        });
      });
      if (!p.insPrimary || p.insPrimary === "—") add({
        id: "noins:" + p.mrn, level: "Low", category: "Clinical",
        title: "No insurance on file: " + p.name,
        detail: "Claims for this patient can't be submitted until coverage is added.",
        href: "/emr", hrefLabel: "Chart",
        task: "Add insurance details for " + p.name
      });
    });

    if (staff && window.StaffStore) {
      var S = window.StaffStore;
      S.complianceItems(staff).forEach(function (i) {
        var lvl = i.status === "Expired" || i.status === "Overdue" ? "High" : (i.status === "Expiring Soon" || i.status === "Missing Requirement" || i.status === "Rejected" ? "Medium" : "Low");
        add({
          id: "staff:" + i.kind + ":" + (i.ref || i.label) + ":" + i.person.id, level: lvl, category: "Staff",
          title: i.label + " " + i.status.toLowerCase() + ": " + i.person.name,
          detail: i.person.role + (i.expires ? " · " + (i.days < 0 ? "since " : "") + fmtDate(i.expires) : "") + " · " + i.action,
          href: i.kind === "training" ? "/staff/training?person=" + encodeURIComponent(i.person.id) : (i.kind === "credential" ? "/staff/credentials?person=" + encodeURIComponent(i.person.id) : "/staff/team?person=" + encodeURIComponent(i.person.id)),
          hrefLabel: "Open",
          task: i.action + ": " + i.label + " for " + i.person.name
        });
      });
      staff.people.filter(function (p) { return S.status(p) === "Needs Review"; }).forEach(function (p) {
        add({
          id: "approve:" + p.id, level: "Low", category: "Staff",
          title: "Ready for onboarding approval: " + p.name,
          detail: p.role + " · every requirement is verified", href: "/staff/onboarding?person=" + encodeURIComponent(p.id), hrefLabel: "Approve",
          task: "Approve " + p.name + "'s onboarding"
        });
      });
    }

    return out.sort(function (a, b) { return LEVEL_RANK[a.level] - LEVEL_RANK[b.level] || (b.value || 0) - (a.value || 0); });
  }

  /* ---------- merged activity feed ---------- */
  function activity(L, staff, tdocIn) {
    var out = [];
    (L.activity || []).forEach(function (ev) {
      var text = ev.text || stripTags(ev.html);
      if (!text) return;
      out.push({ ts: Number(ev.ts) || 0, source: "EMR", kind: ev.kind || "info", text: text, sub: ev.sub || "", by: "", href: "/emr" });
    });
    if (staff) staff.people.forEach(function (p) {
      (p.audit || []).forEach(function (a) {
        var ts = new Date(a.at).getTime();
        if (!isNaN(ts)) out.push({ ts: ts, source: "Staff", kind: "staff", text: p.name + ": " + a.text, sub: p.role, by: a.by || "", href: "/staff/team?person=" + encodeURIComponent(p.id) });
      });
    });
    if (tdocIn) tdocIn.log.forEach(function (l) {
      var ts = new Date(l.at).getTime();
      if (!isNaN(ts)) out.push({ ts: ts, source: "Tasks", kind: "task", text: l.text, sub: "", by: l.by || "", href: l.href || "/overview/tasks" });
    });
    return out.sort(function (a, b) { return b.ts - a.ts; });
  }

  /* ---------- toast ---------- */
  function toast(msg, tone) {
    var el = document.getElementById("pd-toast");
    if (!el) {
      el = document.createElement("div"); el.id = "pd-toast"; el.setAttribute("role", "status");
      el.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:80;padding:9px 14px;border-radius:6px;font-size:12.5px;font-weight:600;box-shadow:0 8px 24px rgba(0,0,0,.18);transition:opacity .2s;opacity:0;pointer-events:none;color:#fff;";
      document.body.appendChild(el);
    }
    el.style.background = tone === "error" ? "#c83838" : "#0f1116";
    el.textContent = msg; el.style.opacity = "1";
    clearTimeout(el._t); el._t = setTimeout(function () { el.style.opacity = "0"; }, 2600);
  }

  window.PracticeData = {
    ns: NS, local: local, tasks: tasks, alerts: alerts, activity: activity, LEVEL_RANK: LEVEL_RANK,
    esc: esc, todayIso: todayIso, isoOffset: isoOffset, money: money, moneyShort: moneyShort, fmtDate: fmtDate, relTime: relTime,
    daysUntil: daysUntil, plural: plural, currentUserName: currentUserName, uid: uid, toast: toast
  };
})();
