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
 *   PracticeData.worklist(local, staff, tasks)   "Work Today": every item waiting on someone, with an
 *                                        owner and next action; isMine(item, myIdentity(staff)) picks the user's
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
    d.worklist = (d.worklist && typeof d.worklist === "object") ? d.worklist : {};
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

  /* ---------- "Work Today" worklist ----------
     Everything that needs a person to act, from the real records, each with an owner (a role, and a named person
     when the record names one) and a next action. Reassign / done / snooze live in the shared tasks document under
     `worklist`, so the whole team sees the same state. */
  var OWNER_ROLES = ["Provider", "Medical Assistant", "Front Desk", "Biller", "Practice Manager"];
  var KIND_LABEL = { note: "Notes", coding: "Coding", claim: "Claims", denial: "Denials", followup: "Follow-ups", task: "Tasks", staff: "Staff" };

  function worklist(L, staff, td) {
    var items = [], today = todayIso(), now = new Date();
    var weekAhead = isoOffset(7), weekAgo = isoOffset(-7);
    function add(o) { items.push(o); }
    function ageDays(iso) { var d = daysUntil(iso); return d == null ? 0 : -d; }
    function patientName(mrn) { for (var i = 0; i < L.patients.length; i++) if (L.patients[i].mrn === mrn) return L.patients[i].name; return ""; }
    function chart(mrn) { return mrn ? "/emr?patient=" + encodeURIComponent(mrn) : "/emr"; }

    /* notes waiting on a signature */
    L.notes.forEach(function (n) {
      if (!n || /^signed$/i.test(n.status || "Signed")) return;
      var age = ageDays(n.date);
      add({ id: "note:" + (n.id || n.mrn + n.date), kind: "note", score: 70 + Math.min(age, 20) * 2,
        title: "Sign visit note: " + (patientName(n.mrn) || n.mrn), detail: [n.type, n.date ? fmtDate(n.date) : "", n.desc].filter(Boolean).join(" · "),
        mrn: n.mrn, ownerRole: "Provider", ownerPerson: n.provider || "", action: "Review & Sign", href: chart(n.mrn), since: n.date });
    });
    /* today's visits that already happened but have no note */
    var notedToday = {};
    L.notes.forEach(function (n) { if (n.date === today) notedToday[n.mrn] = 1; });
    L.appts.forEach(function (a) {
      if (a.date !== today || notedToday[a.mrn] || /cancel|no-?show/i.test(a.status || "")) return;
      if (!a.time || new Date(today + "T" + a.time) > now) return;
      add({ id: "visitnote:" + today + ":" + a.mrn + ":" + a.time, kind: "note", score: 72,
        title: "Write today's visit note: " + (a.patient || a.mrn), detail: (a.time || "") + " · " + (a.type || "Visit"),
        mrn: a.mrn, ownerRole: "Provider", ownerPerson: a.provider || "", action: "Write Note", href: chart(a.mrn), since: today });
    });

    /* claims: coding questions, denial risk, denials */
    L.claims.forEach(function (c) {
      var s = String(c.status || "").toLowerCase(), amt = Number(c.amount) || 0, conf = Number(c.conf || 0), who = c.patient || "Unknown patient";
      var cid = c.claimId || (c.mrn + ":" + c.cpt), age = ageDays(String(c.ts || "").slice(0, 10));
      if (s === "denied") {
        add({ id: "denial:" + cid, kind: "denial", score: 88 + Math.min(amt / 50, 10) + Math.min(age, 30) / 3,
          title: "Appeal denied claim: " + who, detail: [c.claimId, c.payer, money(amt), c.denialReason].filter(Boolean).join(" · "),
          mrn: c.mrn, ownerRole: "Biller", ownerPerson: "", action: "Start Appeal", href: "/revenue/appeals", since: String(c.ts || "").slice(0, 10), value: amt });
        return;
      }
      var missing = !c.cpt || !c.icd;
      if ((s === "pending review" || s === "draft") && (missing || (conf && conf < 85))) {
        add({ id: "coding:" + cid, kind: "coding", score: 60 + (missing ? 10 : 0) + Math.min(amt / 100, 8),
          title: (missing ? "Missing codes on claim: " : "Confirm codes on claim: ") + who,
          detail: [c.claimId, missing ? "No " + [!c.cpt ? "CPT" : "", !c.icd ? "ICD-10" : ""].filter(Boolean).join(" or ") + " code" : conf + "% AI confidence", c.cpt, c.icd, money(amt)].filter(Boolean).join(" · "),
          mrn: c.mrn, ownerRole: "Biller", ownerPerson: "", action: "Resolve Coding", href: "/emr#claims", since: String(c.ts || "").slice(0, 10), value: amt });
      } else if (conf && conf < 75 && /submitted|ready to submit/.test(s)) {
        add({ id: "risk:" + cid, kind: "claim", score: (conf < 60 ? 80 : 58) + Math.min(amt / 100, 8),
          title: "Claim at risk of denial: " + who, detail: [c.claimId, conf + "% AI confidence", c.payer, money(amt)].filter(Boolean).join(" · "),
          mrn: c.mrn, ownerRole: "Biller", ownerPerson: "", action: "Review Claim", href: "/emr#claims", since: String(c.ts || "").slice(0, 10), value: amt });
      }
    });

    /* patient follow-ups */
    var upcomingByMrn = {};
    L.appts.forEach(function (a) { if (a.date >= today && a.date <= weekAhead && !/cancel/i.test(a.status || "")) upcomingByMrn[a.mrn] = upcomingByMrn[a.mrn] || a; });
    L.patients.forEach(function (p) {
      (p.labs || []).forEach(function (l) {
        var status = l.status || "Resulted";
        if (status === "Resulted" && /high|low|critical|abnormal/i.test(l.flag || "") && ageDays(l.date) <= 30) {
          var crit = /critical/i.test(l.flag);
          add({ id: "lab:" + p.mrn + ":" + (l.id || l.test + l.date), kind: "followup", score: crit ? 92 : 55,
            title: (crit ? "Critical result: " : "Abnormal result to review: ") + p.name, detail: [l.test, l.result, l.flag, l.date ? fmtDate(l.date) : ""].filter(Boolean).join(" · "),
            mrn: p.mrn, ownerRole: "Provider", ownerPerson: l.orderedBy || p.pcp || "", action: "Review Result", href: chart(p.mrn), since: l.date });
        } else if (status === "Ordered" && ageDays(l.date) >= 7) {
          add({ id: "labcollect:" + p.mrn + ":" + (l.id || l.test + l.date), kind: "followup", score: 40 + Math.min(ageDays(l.date), 20),
            title: "Lab not collected yet: " + p.name, detail: [l.test, "ordered " + fmtDate(l.date)].join(" · "),
            mrn: p.mrn, ownerRole: "Medical Assistant", ownerPerson: "", action: "Arrange Collection", href: chart(p.mrn), since: l.date });
        }
      });
      (p.priorAuths || []).forEach(function (pa) {
        if (!/pending/i.test(pa.status || "")) return;
        var age = ageDays(pa.date);
        add({ id: "pa:" + p.mrn + ":" + (pa.id || pa.service), kind: "followup", score: 50 + Math.min(age, 30),
          title: "Prior auth still pending: " + p.name, detail: [pa.service, pa.payer, pa.date ? "submitted " + fmtDate(pa.date) : ""].filter(Boolean).join(" · "),
          mrn: p.mrn, ownerRole: "Front Desk", ownerPerson: "", action: "Call " + (pa.payer || "Payer"), href: chart(p.mrn), since: pa.date });
      });
      (p.referrals || []).forEach(function (r) {
        if (!/pending/i.test(r.status || "") || ageDays(r.date) < 7) return;
        add({ id: "ref:" + p.mrn + ":" + (r.id || r.specialty), kind: "followup", score: 35 + Math.min(ageDays(r.date), 20),
          title: "Referral not scheduled: " + p.name, detail: [r.specialty, r.provider, "sent " + fmtDate(r.date)].filter(Boolean).join(" · "),
          mrn: p.mrn, ownerRole: "Front Desk", ownerPerson: "", action: "Follow Up", href: chart(p.mrn), since: r.date });
      });
      if ((!p.insPrimary || p.insPrimary === "—") && upcomingByMrn[p.mrn]) {
        var ap = upcomingByMrn[p.mrn];
        add({ id: "ins:" + p.mrn + ":" + ap.date, kind: "followup", score: ap.date === today ? 85 : 62,
          title: "No insurance before visit: " + p.name, detail: "Visit " + (ap.date === today ? "today" : fmtDate(ap.date)) + (ap.time ? " at " + ap.time : ""),
          mrn: p.mrn, ownerRole: "Front Desk", ownerPerson: "", action: "Get Insurance", href: chart(p.mrn), since: today });
      }
    });
    L.appts.forEach(function (a) {
      if (!/no-?show/i.test(a.status || "") || a.date < weekAgo || a.date > today) return;
      add({ id: "noshow:" + (a.id || a.mrn + a.date), kind: "followup", score: 48,
        title: "Reschedule no-show: " + (a.patient || a.mrn), detail: [a.type, fmtDate(a.date), a.time, a.provider].filter(Boolean).join(" · "),
        mrn: a.mrn, ownerRole: "Front Desk", ownerPerson: "", action: "Reschedule", href: "/emr/schedule?date=" + a.date, since: a.date });
    });

    /* open tasks that are due */
    if (td) td.tasks.forEach(function (t) {
      if (t.status === "done" || !t.due || t.due > today) return;
      var late = -daysUntil(t.due);
      add({ id: "task:" + t.id, kind: "task", score: ({ High: 75, Medium: 55, Low: 35 })[t.priority] + Math.min(late, 10) * 2,
        title: t.title, detail: (late > 0 ? late + "d overdue" : "Due today") + (t.notes ? " · " + t.notes : ""),
        mrn: "", ownerRole: "", ownerPerson: t.assignee || "", action: "Open Task", href: t.link || "/overview/tasks", since: t.due, taskId: t.id });
    });

    /* staff compliance that is already a problem */
    if (staff && window.StaffStore) window.StaffStore.complianceItems(staff).forEach(function (i) {
      if (i.severity > 0 && i.status !== "Expiring Soon") return;
      add({ id: "staff:" + i.kind + ":" + (i.ref || i.label) + ":" + i.person.id, kind: "staff", score: i.severity === 0 ? 78 : 50,
        title: i.label + " " + i.status.toLowerCase() + ": " + i.person.name, detail: i.person.role + (i.expires ? " · " + fmtDate(i.expires) : ""),
        mrn: "", ownerRole: "Practice Manager", ownerPerson: "", action: i.action,
        href: i.kind === "training" ? "/staff/training?person=" + encodeURIComponent(i.person.id) : "/staff/credentials?person=" + encodeURIComponent(i.person.id), since: i.expires });
    });

    /* apply the team's reassignments / done / snooze */
    var state = (td && td.worklist) || {};
    items.forEach(function (it) {
      var o = state[it.id] || {};
      if (o.ownerRole != null) it.ownerRole = o.ownerRole;
      if (o.ownerPerson != null) it.ownerPerson = o.ownerPerson;
      it.reassigned = o.ownerRole != null || o.ownerPerson != null;
      it.done = !!o.doneAt; it.doneAt = o.doneAt || ""; it.doneBy = o.doneBy || "";
      it.snoozed = !!(o.snoozeUntil && o.snoozeUntil > today); it.snoozeUntil = o.snoozeUntil || "";
      it.level = it.score >= 75 ? "High" : it.score >= 50 ? "Medium" : "Low";
      it.kindLabel = KIND_LABEL[it.kind];
    });
    return items.sort(function (a, b) { return b.score - a.score; });
  }

  /* which items are "mine": named for me, or owned by a role I hold (login role, provider link, Staff roster role) */
  function myIdentity(staff) {
    var names = [user.full_name, user.provider_name, user.email].filter(Boolean).map(function (s) { return s.toLowerCase(); });
    var roles = {};
    var r = String(user.role || "admin").toLowerCase();
    if (r === "admin") roles["Practice Manager"] = 1;
    if (r === "biller") roles["Biller"] = 1;
    if (r === "provider") roles["Provider"] = 1;
    if (staff) staff.people.forEach(function (p) {
      if (!p.email || !user.email || p.email.toLowerCase() !== user.email.toLowerCase()) return;
      names.push(p.name.toLowerCase());
      var map = { "Physician": "Provider", "Nurse": "Medical Assistant", "Medical Assistant": "Medical Assistant", "Biller": "Biller", "Practice Manager": "Practice Manager", "Administrator": "Practice Manager" };
      if (map[p.role]) roles[map[p.role]] = 1;
    });
    return { names: names, roles: roles, providerName: (user.provider_name || "").toLowerCase() };
  }
  function isMine(it, me) {
    var person = (it.ownerPerson || "").toLowerCase();
    if (person) return me.names.indexOf(person) !== -1;
    return !!me.roles[it.ownerRole];
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

  /* ---------- each tool's own settings (Settings > EMR / Scribe / Coding / Insurance / Team) ---------- */
  var TOOL_DEFAULTS = {
    emr: { default_appt_time: "09:00", default_appt_type: "Office Visit", my_schedule_first: true, hide_cancelled: false },
    scribe: { default_visit_type: "Office Visit", record_language: "en-US", show_vitals: true, open_history: false, confirm_sign: true, sign_credentials: "" },
    coding: { min_confidence: "0", auto_check: false, show_reasons: true, default_encounter: "office" },
    insurance: { timely_filing_days: "90", denial_followup_days: "14", clearinghouse: "Availity", scrub_before_submit: true, appeal_signature: "" },
    team: { credential_warn_days: "30", training_due_days: "14" }
  };
  /* Resolves to the clinic's settings for one tool, with the defaults filled in. Never rejects. */
  function toolSettings(tool) {
    var base = Object.assign({}, TOOL_DEFAULTS[tool] || {});
    return fetch("/api/org/settings/" + tool + "_prefs", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { return Object.assign(base, (j && j.data) || {}); })
      .catch(function () { return base; });
  }

  window.PracticeData = {
    TOOL_DEFAULTS: TOOL_DEFAULTS, toolSettings: toolSettings,
    ns: NS, local: local, tasks: tasks, alerts: alerts, activity: activity, LEVEL_RANK: LEVEL_RANK,
    worklist: worklist, myIdentity: myIdentity, isMine: isMine, OWNER_ROLES: OWNER_ROLES, KIND_LABEL: KIND_LABEL,
    esc: esc, todayIso: todayIso, isoOffset: isoOffset, money: money, moneyShort: moneyShort, fmtDate: fmtDate, relTime: relTime,
    daysUntil: daysUntil, plural: plural, currentUserName: currentUserName, uid: uid, toast: toast
  };
})();
