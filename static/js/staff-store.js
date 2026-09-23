/* Staff workspace data, shared by every Staff page (Team, Onboarding, Clinic Onboarding, Credentials,
 * Compliance, Training, Roles) and the Overview's staff alerts.
 *
 * Everything lives in one document per organization on the server (GET/PUT /api/staff in auth.py), so the
 * whole team sees the same records and nothing is lost on reload:
 *   { rev, people: [], credentials: [], trainings: [], roles: [], clinic: {} }
 *
 *   StaffStore.load()          -> Promise<doc>
 *   StaffStore.update(fn)      -> Promise<doc>   fn(draft) mutates a copy; if someone else saved first, it is
 *                                                re-applied to their version and saved again
 *   StaffStore.canEdit()       -> only admins can change staff records (enforced by the server too)
 *
 * The rules below (what each role must complete, when a credential counts as expiring, what makes someone
 * non-compliant) are the single source every page reads, so the numbers always agree.
 */
(function () {
  "use strict";

  /* ---------- reference data ---------- */
  var ROLES_LIST = ["Physician", "Nurse", "Medical Assistant", "Biller", "Practice Manager", "Volunteer", "Student", "Administrator"];
  var EMPLOYMENT_TYPES = ["Full-Time", "Part-Time", "Volunteer", "Contractor"];

  var REQUIREMENT_LABELS = {
    personal: "Personal Information", role_info: "Employment / Volunteer Role", hipaa: "HIPAA Training",
    bls: "BLS / CPR Certification", license: "Professional License", immunizations: "Immunization Records",
    background: "Background Check", gov_id: "Government-Issued ID", policies: "Signed Clinic Policies",
    emergency_contact: "Emergency Contact", location: "Assigned Location", access: "System Access & Permissions"
  };
  var ROLE_REQUIREMENTS = {
    "Physician":         ["personal", "role_info", "license", "bls", "hipaa", "background", "immunizations", "gov_id", "policies", "emergency_contact", "location", "access"],
    "Nurse":             ["personal", "role_info", "license", "bls", "hipaa", "background", "immunizations", "gov_id", "policies", "emergency_contact", "location", "access"],
    "Medical Assistant": ["personal", "role_info", "bls", "hipaa", "background", "immunizations", "gov_id", "policies", "emergency_contact", "location", "access"],
    "Biller":            ["personal", "role_info", "hipaa", "background", "gov_id", "policies", "emergency_contact", "access"],
    "Practice Manager":  ["personal", "role_info", "hipaa", "background", "gov_id", "policies", "emergency_contact", "access"],
    "Volunteer":         ["personal", "role_info", "hipaa", "background", "immunizations", "gov_id", "policies", "emergency_contact"],
    "Student":           ["personal", "role_info", "hipaa", "background", "gov_id", "policies", "emergency_contact"],
    "Administrator":     ["personal", "role_info", "hipaa", "background", "gov_id", "policies", "emergency_contact", "access"]
  };

  var CREDENTIAL_TYPES = [
    { key: "medical_license", label: "Medical License" },
    { key: "nursing_license", label: "Nursing License" },
    { key: "bls", label: "BLS / CPR" },
    { key: "dea", label: "DEA Registration" },
    { key: "background", label: "Background Check" },
    { key: "immunizations", label: "Immunizations" },
    { key: "other", label: "Other" }
  ];
  /* onboarding requirements that are really a credential with an expiration date */
  function credentialTypeForRequirement(reqKey, role) {
    if (reqKey === "license") return role === "Physician" ? "medical_license" : (role === "Nurse" ? "nursing_license" : "other");
    if (reqKey === "bls" || reqKey === "background" || reqKey === "immunizations") return reqKey;
    return null;
  }

  var TRAINING_CATEGORIES = ["HIPAA", "Clinic Policy", "Safety", "Role-Specific"];
  var TRAINING_CATALOG = [
    { course: "HIPAA Privacy & Security", category: "HIPAA" },
    { course: "Clinic Policies & Code of Conduct", category: "Clinic Policy" },
    { course: "Workplace Harassment Prevention", category: "Clinic Policy" },
    { course: "Bloodborne Pathogens (OSHA)", category: "Safety" },
    { course: "Infection Control", category: "Safety" },
    { course: "Fire & Emergency Preparedness", category: "Safety" },
    { course: "Fraud, Waste & Abuse", category: "Role-Specific" },
    { course: "Medical Coding Updates", category: "Role-Specific" },
    { course: "Althais / EHR Training", category: "Role-Specific" }
  ];

  var PERMISSION_AREAS = [
    { key: "patients", label: "Patient Records" }, { key: "notes", label: "Clinical Notes" }, { key: "coding", label: "Coding" },
    { key: "claims", label: "Claims" }, { key: "revenue", label: "Revenue" }, { key: "staff", label: "Staff Records" },
    { key: "compliance", label: "Compliance" }, { key: "settings", label: "Settings" }
  ];
  var DEFAULT_ROLES = [
    { name: "Physician",         perms: { patients: 1, notes: 1, coding: 1, claims: 1, revenue: 0, staff: 0, compliance: 0, settings: 0 } },
    { name: "Nurse",             perms: { patients: 1, notes: 1, coding: 0, claims: 0, revenue: 0, staff: 0, compliance: 0, settings: 0 } },
    { name: "Medical Assistant", perms: { patients: 1, notes: 1, coding: 0, claims: 0, revenue: 0, staff: 0, compliance: 0, settings: 0 } },
    { name: "Biller",            perms: { patients: 1, notes: 0, coding: 1, claims: 1, revenue: 1, staff: 0, compliance: 0, settings: 0 } },
    { name: "Practice Manager",  perms: { patients: 1, notes: 0, coding: 0, claims: 1, revenue: 1, staff: 1, compliance: 1, settings: 1 } },
    { name: "Volunteer",         perms: { patients: 0, notes: 0, coding: 0, claims: 0, revenue: 0, staff: 0, compliance: 0, settings: 0 } },
    { name: "Student",           perms: { patients: 1, notes: 1, coding: 0, claims: 0, revenue: 0, staff: 0, compliance: 0, settings: 0 } },
    { name: "Administrator",     perms: { patients: 1, notes: 1, coding: 1, claims: 1, revenue: 1, staff: 1, compliance: 1, settings: 1 } }
  ];

  var EXPIRING_DAYS = 30;       /* a credential inside this window is "expiring soon" */
  var TRAINING_DUE_DAYS = 14;   /* an assignment inside this window is "due soon" */

  /* ---------- small helpers ---------- */
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function uid(prefix) { return (prefix || "id") + "_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function todayIso() { var d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
  function daysUntil(iso) {
    if (!iso) return null;
    var t = new Date(iso + "T00:00:00").getTime(), n = new Date(todayIso() + "T00:00:00").getTime();
    return isNaN(t) ? null : Math.round((t - n) / 86400000);
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).length <= 10 ? iso + "T00:00:00" : iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" });
  }
  function initials(name) { return String(name || "").split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join("").toUpperCase() || "?"; }
  function currentUserName() { var u = window.__ALTHAIS_USER__ || {}; return u.full_name || u.email || "Admin"; }
  function credentialLabel(key) { for (var i = 0; i < CREDENTIAL_TYPES.length; i++) if (CREDENTIAL_TYPES[i].key === key) return CREDENTIAL_TYPES[i].label; return key || "Other"; }

  /* ---------- the document ---------- */
  var doc = null, editable = false, loading = null;

  function normalize(d) {
    d = (d && typeof d === "object") ? d : {};
    d.rev = d.rev || 0;
    d.people = Array.isArray(d.people) ? d.people : [];
    d.credentials = Array.isArray(d.credentials) ? d.credentials : [];
    d.trainings = Array.isArray(d.trainings) ? d.trainings : [];
    d.roles = Array.isArray(d.roles) && d.roles.length ? d.roles : JSON.parse(JSON.stringify(DEFAULT_ROLES));
    d.clinic = (d.clinic && typeof d.clinic === "object") ? d.clinic : {};
    d.clinic.locations = Array.isArray(d.clinic.locations) ? d.clinic.locations : [];
    d.clinic.payers = Array.isArray(d.clinic.payers) ? d.clinic.payers : [];
    d.clinic.steps = (d.clinic.steps && typeof d.clinic.steps === "object") ? d.clinic.steps : {};
    d.people.forEach(function (p) { p.requirements = Array.isArray(p.requirements) ? p.requirements : []; p.audit = Array.isArray(p.audit) ? p.audit : []; });
    /* labels are Title Case now; records saved before that still carry the old spelling */
    d.clinic.payers.forEach(function (p) { if (p.status === "Not started") p.status = "Not Started"; });
    var OLD = { "Clinic policy": "Clinic Policy", "Role-specific": "Role-Specific", "Full-time": "Full-Time", "Part-time": "Part-Time" };
    d.trainings.forEach(function (t) { if (OLD[t.category]) t.category = OLD[t.category]; });
    d.people.forEach(function (p) { if (OLD[p.employment]) p.employment = OLD[p.employment]; });
    return d;
  }

  function load(force) {
    if (doc && !force) return Promise.resolve(doc);
    if (loading && !force) return loading;
    loading = fetch("/api/staff", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("Could not load staff records (" + r.status + ")."); return r.json(); })
      .then(function (res) { doc = normalize(res.data); editable = !!res.can_edit; loading = null; return doc; })
      .catch(function (e) { loading = null; throw e; });
    return loading;
  }

  function put(draft) {
    return fetch("/api/staff", { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft) })
      .then(function (r) { return r.json().then(function (body) { return { status: r.status, body: body }; }); });
  }

  /* Apply a change and save it. If another admin saved in between, re-apply the same change to their
     version so neither edit is lost. */
  function update(fn) {
    if (!editable) return Promise.reject(new Error("Only admins can change staff records."));
    return load().then(function (base) {
      function attempt(from, retriesLeft) {
        var draft = normalize(JSON.parse(JSON.stringify(from)));
        fn(draft);
        return put(draft).then(function (res) {
          if (res.status === 200) { doc = normalize(res.body.data); return doc; }
          if (res.status === 409 && retriesLeft > 0) return attempt(normalize(res.body.data), retriesLeft - 1);
          throw new Error((res.body && res.body.error) || "Could not save staff records.");
        });
      }
      return attempt(base, 2);
    });
  }

  /* ---------- people ---------- */
  function requirementsFor(role, existing) {
    var keep = {};
    (existing || []).forEach(function (r) { keep[r.key] = r; });
    return (ROLE_REQUIREMENTS[role] || ROLE_REQUIREMENTS["Administrator"]).map(function (key) {
      return keep[key] || { key: key, done: false };
    });
  }
  function progress(p) {
    var total = p.requirements.length, done = p.requirements.filter(function (r) { return r.done; }).length;
    return total ? Math.round((done / total) * 100) : 0;
  }
  /* onboarding pipeline status; people who finished onboarding are "Active" or "Inactive" */
  function status(p) {
    if (p.status === "active") return "Active";
    if (p.status === "inactive") return "Inactive";
    var pct = progress(p);
    if (pct === 100) return "Needs Review";
    if (pct === 0) return "Invited";
    return "In Progress";
  }
  function audit(p, text) { p.audit.push({ text: text, at: new Date().toISOString(), by: currentUserName() }); }
  function person(d, id) { for (var i = 0; i < d.people.length; i++) if (d.people[i].id === id) return d.people[i]; return null; }

  /* ---------- credentials and training ---------- */
  function credentialStatus(c) {
    if (c.status === "rejected") return "Rejected";
    var days = daysUntil(c.expires);
    if (days != null && days < 0) return "Expired";
    if (c.status !== "verified") return "Pending Verification";
    if (days != null && days <= EXPIRING_DAYS) return "Expiring Soon";
    return "Verified";
  }
  function trainingStatus(t) {
    if (t.completed) return "Completed";
    var days = daysUntil(t.due);
    if (days != null && days < 0) return "Overdue";
    if (days != null && days <= TRAINING_DUE_DAYS) return "Due Soon";
    return "Assigned";
  }
  var PILL = {
    "Invited": "background:#eceef2;color:#4a505c", "In Progress": "background:#e6efff;color:#0a47b0", "Needs Review": "background:#fff0d6;color:#b86a00",
    "Active": "background:#d8f5e3;color:#0c8a4f", "Inactive": "background:#eceef2;color:#4a505c",
    "Verified": "background:#d8f5e3;color:#0c8a4f", "Pending Verification": "background:#e6efff;color:#0a47b0", "Expiring Soon": "background:#fff0d6;color:#b86a00",
    "Expired": "background:#ffe1e1;color:#c83838", "Rejected": "background:#ffe1e1;color:#c83838",
    "Completed": "background:#d8f5e3;color:#0c8a4f", "Assigned": "background:#eceef2;color:#4a505c", "Due Soon": "background:#fff0d6;color:#b86a00", "Overdue": "background:#ffe1e1;color:#c83838",
    "Compliant": "background:#d8f5e3;color:#0c8a4f", "Action Needed": "background:#ffe1e1;color:#c83838", "Missing Requirement": "background:#fff0d6;color:#b86a00",
    "Not Started": "background:#eceef2;color:#4a505c", "Submitted": "background:#e6efff;color:#0a47b0", "Approved": "background:#d8f5e3;color:#0c8a4f", "Denied": "background:#ffe1e1;color:#c83838"
  };
  function pill(label) { return '<span class="pill" style="' + (PILL[label] || PILL["Assigned"]) + '">' + esc(label) + "</span>"; }

  /* ---------- compliance: everything about ACTIVE staff that needs attention ----------
     severity 0 = act now (expired / overdue), 1 = act soon (expiring, due soon, missing), 2 = waiting on an admin */
  function complianceItems(d) {
    var items = [];
    d.people.filter(function (p) { return p.status === "active"; }).forEach(function (p) {
      p.requirements.filter(function (r) { return !r.done; }).forEach(function (r) {
        items.push({ person: p, kind: "requirement", label: REQUIREMENT_LABELS[r.key] || r.key, status: "Missing Requirement", expires: "", days: null, severity: 1, action: "Complete and verify" });
      });
    });
    d.credentials.forEach(function (c) {
      var p = person(d, c.personId);
      if (!p || p.status !== "active") return;
      var st = credentialStatus(c), days = daysUntil(c.expires);
      if (st === "Expired") items.push({ person: p, kind: "credential", ref: c.id, label: credentialLabel(c.type), status: st, expires: c.expires, days: days, severity: 0, action: "Renew and upload new document" });
      else if (st === "Expiring Soon") items.push({ person: p, kind: "credential", ref: c.id, label: credentialLabel(c.type), status: st, expires: c.expires, days: days, severity: 1, action: "Start renewal" });
      else if (st === "Pending Verification") items.push({ person: p, kind: "credential", ref: c.id, label: credentialLabel(c.type), status: st, expires: c.expires, days: days, severity: 2, action: "Verify document" });
      else if (st === "Rejected") items.push({ person: p, kind: "credential", ref: c.id, label: credentialLabel(c.type), status: st, expires: c.expires, days: days, severity: 1, action: "Request a corrected document" });
    });
    d.trainings.forEach(function (t) {
      var p = person(d, t.personId);
      if (!p || p.status !== "active") return;
      var st = trainingStatus(t), days = daysUntil(t.due);
      if (st === "Overdue") items.push({ person: p, kind: "training", ref: t.id, label: t.course, status: st, expires: t.due, days: days, severity: 0, action: "Complete training" });
      else if (st === "Due Soon") items.push({ person: p, kind: "training", ref: t.id, label: t.course, status: st, expires: t.due, days: days, severity: 1, action: "Complete before due date" });
    });
    return items.sort(function (a, b) { return a.severity - b.severity || (a.days == null ? 1e9 : a.days) - (b.days == null ? 1e9 : b.days); });
  }
  function personCompliance(d, p) {
    return complianceItems(d).some(function (i) { return i.person.id === p.id && i.severity <= 1; }) ? "Action Needed" : "Compliant";
  }
  function locations(d) {
    var seen = {}, out = [];
    d.clinic.locations.forEach(function (l) { if (l.name && !seen[l.name]) { seen[l.name] = 1; out.push(l.name); } });
    d.people.forEach(function (p) { if (p.location && !seen[p.location]) { seen[p.location] = 1; out.push(p.location); } });
    return out;
  }

  /* ---------- UI helpers shared by the pages ---------- */
  function toast(msg, tone) {
    var el = document.getElementById("staff-toast");
    if (!el) {
      el = document.createElement("div"); el.id = "staff-toast";
      el.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:80;padding:9px 14px;border-radius:6px;font-size:12.5px;font-weight:600;box-shadow:0 8px 24px rgba(0,0,0,.18);transition:opacity .2s;opacity:0;pointer-events:none;";
      document.body.appendChild(el);
    }
    el.style.background = tone === "error" ? "#c83838" : "#0f1116"; el.style.color = "#fff";
    el.textContent = msg; el.style.opacity = "1";
    clearTimeout(el._t); el._t = setTimeout(function () { el.style.opacity = "0"; }, 2600);
  }
  /* run a save, report failures, and hand back the fresh document */
  function save(fn, okMsg) {
    return update(fn).then(function (d) { if (okMsg) toast(okMsg); return d; }, function (e) { toast(e.message || "Could not save.", "error"); throw e; });
  }
  function mailto(p, subject, body) {
    if (!p || !p.email) return null;
    return "mailto:" + encodeURIComponent(p.email) + "?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(body);
  }
  function downloadCsv(filename, header, rows) {
    var lines = [header].concat(rows).map(function (r) { return r.map(function (v) { return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"'; }).join(","); });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = filename; a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }
  function options(list, selected, placeholder) {
    return (placeholder ? '<option value="">' + esc(placeholder) + "</option>" : "") + list.map(function (o) {
      var v = typeof o === "string" ? o : o.value, l = typeof o === "string" ? o : o.label;
      return '<option value="' + esc(v) + '"' + (v === selected ? " selected" : "") + ">" + esc(l) + "</option>";
    }).join("");
  }
  /* shown on every Staff page when the signed-in account can't edit */
  function readOnlyBanner(containerId) {
    if (editable) return;
    var el = document.getElementById(containerId);
    if (el) el.innerHTML = '<div class="mb-4 px-3 py-2 rounded-sm border border-line bg-panel text-[12px] text-ink-600">You can view staff records. Only admins can make changes.</div>';
    document.querySelectorAll("[data-admin-only]").forEach(function (b) { b.style.display = "none"; });
  }

  window.StaffStore = {
    ROLES_LIST: ROLES_LIST, EMPLOYMENT_TYPES: EMPLOYMENT_TYPES, REQUIREMENT_LABELS: REQUIREMENT_LABELS, ROLE_REQUIREMENTS: ROLE_REQUIREMENTS,
    CREDENTIAL_TYPES: CREDENTIAL_TYPES, TRAINING_CATEGORIES: TRAINING_CATEGORIES, TRAINING_CATALOG: TRAINING_CATALOG,
    PERMISSION_AREAS: PERMISSION_AREAS, DEFAULT_ROLES: DEFAULT_ROLES, EXPIRING_DAYS: EXPIRING_DAYS,
    load: load, update: update, save: save, canEdit: function () { return editable; }, doc: function () { return doc; },
    requirementsFor: requirementsFor, progress: progress, status: status, audit: audit, person: person,
    credentialTypeForRequirement: credentialTypeForRequirement, credentialLabel: credentialLabel, credentialStatus: credentialStatus, trainingStatus: trainingStatus,
    complianceItems: complianceItems, personCompliance: personCompliance, locations: locations,
    esc: esc, uid: uid, todayIso: todayIso, daysUntil: daysUntil, fmtDate: fmtDate, initials: initials, currentUserName: currentUserName,
    pill: pill, toast: toast, mailto: mailto, downloadCsv: downloadCsv, options: options, readOnlyBanner: readOnlyBanner
  };
})();
