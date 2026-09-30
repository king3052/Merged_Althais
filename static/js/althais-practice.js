/* Althais Training > Practice In Althais: a small practice copy of Althais with made-up patients.
 *
 *   AlthaisPractice.render(container, card, { api, esc, done: [taskKeys], onChange(result) })
 *
 * The card (from training_content.practice_for) lists this role's tasks and the demo data they need. Every answer is sent
 * to /api/portal/course/althais/practice and checked on the server, which says what's still wrong. Nothing is graded here.
 */
(function () {
  "use strict";

  var CSS = [
    ".pr-wrap{display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}",
    "@media (min-width:1024px){.pr-wrap{grid-template-columns:260px minmax(0,1fr)}}",
    ".pr-task{display:flex;gap:10px;align-items:flex-start;width:100%;text-align:left;padding:10px 12px;border:1px solid #e4e6eb;border-radius:3px;background:#fff;font-size:13px}",
    ".pr-task.is-cur{border-color:var(--brand);box-shadow:inset 3px 0 var(--brand)}.pr-task[disabled]{opacity:.55;cursor:default}",
    ".pr-dot{width:22px;height:22px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:700;background:#e4e6eb;color:#4b5563}",
    ".pr-dot.ok{background:#0c8a4f;color:#fff}.pr-dot.cur{background:var(--brand);color:var(--brand-on,#fff)}",
    ".pr-app{border:1px solid #d7dae0;border-radius:6px;overflow:hidden;background:#fff;box-shadow:0 6px 24px rgba(15,17,22,.08)}",
    ".pr-bar{display:flex;align-items:center;gap:14px;padding:8px 14px;background:var(--brand);color:#fff;font-size:12px}",
    ".pr-bar b{letter-spacing:.18em;font-size:12.5px}.pr-bar span{opacity:.85}.pr-bar .pr-badge{margin-left:auto;background:rgba(255,255,255,.18);padding:2px 8px;border-radius:999px;font-weight:600;opacity:1}",
    ".pr-body{padding:16px;min-height:360px}",
    ".pr-h{font-size:15px;font-weight:600;color:#0f1116}.pr-sub{font-size:12.5px;color:#6b7280;margin-top:2px}",
    ".pr-in{width:100%;border:1px solid #d7dae0;border-radius:3px;padding:8px 10px;font-size:13.5px;background:#fff;color:#0f1116;outline:none}",
    ".pr-in:focus{border-color:var(--brand)}textarea.pr-in{resize:vertical;min-height:62px;line-height:1.45}",
    ".pr-lbl{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#6b7280;font-weight:600;margin-bottom:4px}",
    ".pr-tbl{width:100%;border-collapse:collapse;font-size:13px}.pr-tbl th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#6b7280;padding:8px 10px;border-bottom:1px solid #e4e6eb;background:#f8f9fb}",
    ".pr-tbl td{padding:9px 10px;border-bottom:1px solid #eceef2;vertical-align:middle}",
    ".pr-btn{font-size:12.5px;padding:7px 12px;border-radius:3px;font-weight:600;background:var(--brand);color:var(--brand-on,#fff);white-space:nowrap}",
    ".pr-btn:disabled{opacity:.5}.pr-btn.line{background:#fff;color:#2c313b;border:1px solid #d7dae0}.pr-btn.sm{font-size:11.5px;padding:4px 9px}",
    ".pr-btn.on-ok{background:#0c8a4f;color:#fff}.pr-btn.on-bad{background:#c83838;color:#fff}",
    ".pr-msg{margin-top:12px;padding:10px 12px;border-radius:3px;font-size:13px;line-height:1.5}.pr-msg.ok{background:#e3f5ec;color:#0a6b3d}.pr-msg.bad{background:#fdecec;color:#a52a2a}",
    ".pr-card{border-radius:10px;padding:14px 16px;color:#fff;background:linear-gradient(135deg,#1b4f9c,#2c6fd1);font-size:12.5px;box-shadow:0 4px 14px rgba(27,79,156,.3)}",
    ".pr-card b{display:block;font-size:15px;letter-spacing:.04em;margin-top:6px}.pr-card .mono{font-family:ui-monospace,monospace;letter-spacing:.08em}",
    ".pr-side{border:1px solid #e4e6eb;border-radius:3px;background:#f8f9fb;padding:12px;font-size:12.5px}",
    ".pr-slot{padding:9px 10px;border:1px solid #d7dae0;border-radius:3px;font-size:12.5px;text-align:left;background:#fff;width:100%}",
    ".pr-slot:hover:not([disabled]){border-color:var(--brand)}.pr-slot[disabled]{background:#f1f2f5;color:#9aa0ab;cursor:not-allowed}.pr-slot.is-pick{border-color:var(--brand);box-shadow:inset 0 0 0 1px var(--brand)}",
    ".pr-opt{display:flex;gap:9px;align-items:flex-start;padding:10px 12px;border:1px solid #d7dae0;border-radius:3px;font-size:13px;cursor:pointer;background:#fff;margin-bottom:6px}",
    ".pr-opt:has(input:checked){border-color:var(--brand);box-shadow:inset 0 0 0 1px var(--brand)}",
    "html[data-theme-resolved='dark'] .pr-task,html[data-theme-resolved='dark'] .pr-app,html[data-theme-resolved='dark'] .pr-in,html[data-theme-resolved='dark'] .pr-slot,",
    "html[data-theme-resolved='dark'] .pr-opt,html[data-theme-resolved='dark'] .pr-btn.line{background:#1a1d24;border-color:#2c313b;color:#d6dae5}",
    "html[data-theme-resolved='dark'] .pr-h{color:#e7eaf2}html[data-theme-resolved='dark'] .pr-side,html[data-theme-resolved='dark'] .pr-tbl th{background:#14181f;border-color:#2c313b}",
    "html[data-theme-resolved='dark'] .pr-tbl td{border-color:#2c313b}html[data-theme-resolved='dark'] .pr-slot[disabled]{background:#14181f;color:#6b7280}"
  ].join("\n");

  function fmtDob(iso) { var p = String(iso || "").split("-"); return p.length === 3 ? p[1] + "/" + p[2] + "/" + p[0] : iso; }

  function render(el, card, ctx) {
    if (!document.getElementById("pr-css")) { var s = document.createElement("style"); s.id = "pr-css"; s.textContent = CSS; document.head.appendChild(s); }
    var esc = ctx.esc, D = card.data, tasks = card.tasks;
    var P = el.__pr = el.__pr || { done: (ctx.done || []).slice(), view: null, msg: {}, a: {}, q: "", filter: "All", busy: false, opened: null, claim: null };
    function isDone(k) { return P.done.indexOf(k) >= 0; }
    function current() { var t = tasks.filter(function (t) { return !isDone(t.key); })[0]; return t ? t.key : null; }
    if (!P.view || (!isDone(P.view) && P.view !== current())) P.view = current() || tasks[tasks.length - 1].key;
    var A = P.a[P.view] = P.a[P.view] || {};

    function taskList() {
      var cur = current();
      return '<div class="space-y-2">' + tasks.map(function (t, i) {
        var ok = isDone(t.key), on = t.key === P.view, open = ok || t.key === cur;
        return '<button type="button" class="pr-task' + (on ? " is-cur" : "") + '" data-pr-task="' + t.key + '"' + (open ? "" : " disabled") + '>' +
          '<span class="pr-dot' + (ok ? " ok" : t.key === cur ? " cur" : "") + '">' + (ok ? "✓" : i + 1) + '</span><span class="min-w-0"><b class="block text-ink-900">' + esc(t.title) + "</b>" +
          (on ? '<span class="block text-[12.5px] text-ink-600 mt-1 leading-relaxed">' + esc(t.instruction) + "</span>" : "") + "</span></button>";
      }).join("") + '<div class="text-[12px] text-ink-500 px-1 pt-1">' + P.done.length + " of " + tasks.length + " tasks done. Made-up patients and data.</div></div>";
    }
    function msg() {
      var m = P.msg[P.view], nxt = current();
      if (!m) return "";
      return '<div class="pr-msg ' + (m.ok ? "ok" : "bad") + '" role="status">' + (m.ok ? "✓ " : "") + esc(m.text) +
        (m.ok && nxt && nxt !== P.view ? ' <button type="button" class="pr-btn sm" style="margin-left:8px" data-pr-task="' + nxt + '">Next Task ›</button>' : "") + "</div>";
    }
    function submitBtn(label) { return '<button type="button" class="pr-btn mt-4" data-pr-submit' + (P.busy || isDone(P.view) ? " disabled" : "") + ">" + (isDone(P.view) ? "Done ✓" : label) + "</button>"; }
    function chart(p) {
      return '<div class="pr-side"><div class="font-semibold text-ink-900">' + esc(p.name) + ' <span class="text-ink-500 font-normal">· ' + esc(p.mrn) + "</span></div>" +
        '<div class="text-ink-500">DOB ' + esc(fmtDob(p.dob)) + " · " + esc(p.sex) + " · PCP " + esc(p.pcp) + "</div>" +
        '<div class="mt-2"><span class="pr-lbl">Allergies</span>' + p.allergies.map(esc).join(", ") + "</div>" +
        '<div class="mt-2"><span class="pr-lbl">Medications</span>' + p.meds.map(esc).join(", ") + "</div>" +
        '<div class="mt-2"><span class="pr-lbl">History</span>' + esc(p.history) + "</div></div>";
    }
    var john = (D.patients || []).filter(function (p) { return p.mrn === "JS10023"; })[0];

    var VIEWS = {
      find_patient: function () {
        var q = P.q.trim().toLowerCase(), rows = q.length < 2 ? [] : D.patients.filter(function (p) { return (p.name + " " + p.mrn).toLowerCase().replace(",", "").indexOf(q.replace(",", "")) >= 0 || p.name.toLowerCase().indexOf(q) >= 0; });
        return '<div class="pr-h">Patients</div><div class="pr-sub">' + esc(D.assignment) + '</div>' +
          '<input class="pr-in mt-3" id="pr-q" placeholder="Search by name or MRN" value="' + esc(P.q) + '" autocomplete="off" aria-label="Search patients">' +
          (q.length < 2 ? '<div class="text-[12.5px] text-ink-500 mt-3">Type at least two letters of the name.</div>' :
            '<table class="pr-tbl mt-3"><thead><tr><th>Name</th><th>Date Of Birth</th><th>MRN</th><th>Insurance</th><th></th></tr></thead><tbody>' +
            (rows.length ? rows.map(function (p) {
              return "<tr><td class='font-semibold'>" + esc(p.name) + "</td><td>" + esc(fmtDob(p.dob)) + "</td><td class='font-mono text-[12px]'>" + esc(p.mrn) + "</td><td>" + esc(p.payer) +
                '</td><td class="text-right"><button type="button" class="pr-btn sm" data-pr-open="' + esc(p.mrn) + '">Open Chart</button></td></tr>';
            }).join("") : '<tr><td colspan="5" class="text-ink-500">No patients match.</td></tr>') + "</tbody></table>") +
          (isDone("find_patient") && john ? '<div class="mt-4">' + chart(john) + "</div>" : "") + msg();
      },
      fix_registration: function () {
        var R = A.v = A.v || JSON.parse(JSON.stringify(D.registration)), c = D.card;
        var f = function (k, l) { return '<div><label class="pr-lbl" for="pr-f-' + k + '">' + l + '</label><input class="pr-in" id="pr-f-' + k + '" data-pr-field="' + k + '" value="' + esc(R[k]) + '"' + (isDone(P.view) ? " disabled" : "") + "></div>"; };
        return '<div class="pr-h">Registration · Smith, John</div><div class="pr-sub">Check every field against the card the patient handed you.</div>' +
          '<div class="grid md:grid-cols-[minmax(0,1fr)_260px] gap-4 mt-3 items-start"><div class="grid sm:grid-cols-2 gap-3">' +
          f("name", "Patient Name (Last, First)") + f("dob", "Date Of Birth") + f("phone", "Phone") + f("payer", "Insurance") + f("member", "Member ID") + "</div>" +
          '<div><div class="pr-card"><div class="opacity-80">' + esc(c.payer) + '</div><b>' + esc(c.name) + '</b><div class="mt-3 opacity-80 text-[11px]">MEMBER ID</div><div class="mono text-[14px]">' + esc(c.member) +
          '</div><div class="flex justify-between mt-2 text-[11px] opacity-85"><span>GROUP ' + esc(c.group) + "</span><span>" + esc(c.valid) + '</span></div></div><div class="text-[11.5px] text-ink-500 mt-2">The patient\'s insurance card</div></div></div>' +
          submitBtn("Save Registration") + msg();
      },
      book_followup: function () {
        var provs = ["Dr. R. Patel", "Dr. A. Chen"];
        return '<div class="pr-h">Scheduler · ' + esc(D.day) + '</div><div class="pr-sub">Patient: Smith, John (born 04/12/1968)</div>' +
          '<div class="grid sm:grid-cols-2 gap-4 mt-3">' + provs.map(function (pv) {
            return '<div><div class="pr-lbl">' + esc(pv) + '</div><div class="space-y-1.5">' + D.slots.filter(function (s) { return s.provider === pv; }).map(function (s) {
              var pick = A.t === s.t && A.provider === s.provider;
              return '<button type="button" class="pr-slot' + (pick ? " is-pick" : "") + '" data-pr-slot="' + esc(s.t) + '|' + esc(s.provider) + '"' + (s.taken || isDone(P.view) ? " disabled" : "") + ">" +
                '<b class="font-mono">' + esc(s.t) + "</b> " + (s.taken ? "Booked: " + esc(s.taken) : '<span class="text-ok-600">Open</span>') + "</button>";
            }).join("") + "</div></div>";
          }).join("") + "</div>" + (A.t ? '<div class="text-[12.5px] text-ink-700 mt-3">Selected: ' + esc(A.t) + " with " + esc(A.provider) + "</div>" : "") + submitBtn("Book Appointment") + msg();
      },
      fix_note: function () {
        var N = A.v = A.v || JSON.parse(JSON.stringify(D.note)), ro = isDone(P.view) ? " disabled" : "";
        var ta = function (k, l, rows) { return '<div><label class="pr-lbl" for="pr-n-' + k + '">' + l + '</label><textarea class="pr-in" rows="' + (rows || 2) + '" id="pr-n-' + k + '" data-pr-field="' + k + '"' + ro + ">" + esc(N[k]) + "</textarea></div>"; };
        return '<div class="pr-h">Virtual SOAP · Step 2: Note</div><div class="pr-sub">AI draft from the visit recording. You\'re responsible for what gets signed.</div>' +
          '<div class="grid md:grid-cols-[minmax(0,1fr)_240px] gap-4 mt-3 items-start"><div class="space-y-3">' + ta("cc", "Chief Complaint", 1) + ta("hpi", "HPI", 3) +
          '<div><span class="pr-lbl">Vitals</span><div class="text-[13px] text-ink-800">' + esc(N.vitals) + "</div></div>" + ta("allergies", "Allergies", 1) + ta("assessment", "Assessment", 2) + ta("plan", "Plan", 2) +
          "</div><div>" + (john ? chart(john) : "") + '<div class="text-[11.5px] text-ink-500 mt-2">His chart, for comparison</div></div></div>' + submitBtn("Save Note") + msg();
      },
      review_codes: function () {
        var d = A.decisions = A.decisions || {};
        return '<div class="pr-h">Virtual SOAP · Step 3: AI Coding</div><div class="pr-sub">Office visit (place of service 11). The note documents chest pain, shortness of breath and a penicillin allergy, and says "rule out acute coronary syndrome".</div>' +
          '<div class="space-y-2 mt-3">' + D.codes.map(function (c) {
            var v = d[c.code];
            return '<div class="flex items-center gap-3 flex-wrap p-3 rounded-sm border border-line"><span class="text-[10.5px] font-bold px-1.5 py-0.5 rounded-sm bg-med-100 text-med-700">' + esc(c.type) + '</span><b class="font-mono">' + esc(c.code) + '</b><span class="text-ink-700 text-[13px]">' + esc(c.label) +
              '</span><span class="ml-auto text-ok-600 font-semibold text-[12.5px]">' + c.conf + '%</span><span class="flex gap-1.5"><button type="button" class="pr-btn sm ' + (v === "accept" ? "on-ok" : "line") + '" data-pr-code="' + esc(c.code) + '|accept"' + (isDone(P.view) ? " disabled" : "") + '>Accept</button>' +
              '<button type="button" class="pr-btn sm ' + (v === "reject" ? "on-bad" : "line") + '" data-pr-code="' + esc(c.code) + '|reject"' + (isDone(P.view) ? " disabled" : "") + ">Reject</button></span></div>";
          }).join("") + "</div>" + submitBtn("Review & Save Codes") + msg();
      },
      fix_vitals: function () {
        var V = A.v = A.v || JSON.parse(JSON.stringify(D.vitals)), ro = isDone(P.view) ? " disabled" : "";
        var f = function (k, l) { return '<div><label class="pr-lbl" for="pr-v-' + k + '">' + l + '</label><input class="pr-in font-mono" id="pr-v-' + k + '" data-pr-field="' + k + '" value="' + esc(V[k]) + '"' + ro + "></div>"; };
        return '<div class="pr-h">Vitals · Smith, John</div><div class="pr-sub">Entered before the provider sees him.</div>' +
          '<div class="grid md:grid-cols-[minmax(0,1fr)_240px] gap-4 mt-3 items-start"><div class="grid grid-cols-2 gap-3">' + f("bp", "Blood Pressure") + f("hr", "Heart Rate") + f("temp", "Temperature (°F)") + f("spo2", "SpO2 (%)") +
          '</div><div class="pr-side font-mono text-[13px]"><div class="pr-lbl" style="font-family:inherit">Device reading</div>BP 148/92<br>HR 88<br>TEMP 98.4<br>SpO2 97%</div></div>' + submitBtn("Save Vitals") + msg();
      },
      find_claim: function () {
        var q = P.q.trim().toLowerCase(), F = P.filter, rows = D.claims.filter(function (c) { return (F === "All" || c.status === F) && (!q || (c.patient + " " + c.payer + " " + c.id + " " + c.cpt).toLowerCase().indexOf(q) >= 0); });
        return '<div class="pr-h">Claims Overview</div><div class="pr-sub">Every claim, with its status.</div>' +
          '<div class="flex gap-2 flex-wrap items-center mt-3">' + ["All", "Submitted", "Denied", "Paid"].map(function (f) { return '<button type="button" class="pr-btn sm ' + (F === f ? "" : "line") + '" data-pr-filter="' + f + '">' + f + "</button>"; }).join("") +
          '<input class="pr-in" style="max-width:260px;margin-left:auto" id="pr-q" placeholder="Filter by patient, payer, code…" value="' + esc(P.q) + '" autocomplete="off" aria-label="Filter claims"></div>' +
          '<table class="pr-tbl mt-3"><thead><tr><th>Claim</th><th>Patient</th><th>Payer</th><th>CPT</th><th>Amount</th><th>Status</th><th></th></tr></thead><tbody>' +
          (rows.length ? rows.map(function (c) {
            return "<tr><td class='font-mono text-[12px]'>" + esc(c.id) + "</td><td class='font-semibold'>" + esc(c.patient) + "</td><td>" + esc(c.payer) + "</td><td class='font-mono'>" + esc(c.cpt) + "</td><td>$" + c.amount.toFixed(2) +
              '</td><td><span class="text-[11px] font-bold px-2 py-0.5 rounded-sm ' + (c.status === "Denied" ? "bg-risk-100 text-risk-600" : c.status === "Paid" ? "bg-ok-100 text-ok-600" : "bg-med-100 text-med-700") + '">' + esc(c.status) +
              '</span></td><td class="text-right"><button type="button" class="pr-btn sm" data-pr-claim="' + esc(c.id) + '">Open</button></td></tr>';
          }).join("") : '<tr><td colspan="7" class="text-ink-500">No claims match.</td></tr>') + "</tbody></table>" + msg();
      },
      denial_step: function () {
        var c = D.claims.filter(function (x) { return x.id === "CL-2026-003"; })[0];
        return '<div class="pr-h">Claim ' + esc(c.id) + " · " + esc(c.patient) + '</div><div class="pr-sub">' + esc(c.payer) + " · CPT " + esc(c.cpt) + " · $" + c.amount.toFixed(2) + "</div>" +
          '<div class="pr-msg bad" style="margin-top:12px"><b>Denied.</b> ' + esc(c.denial) + "</div>" +
          '<div class="pr-lbl mt-4">What should happen next?</div>' + D.denialActions.map(function (o) {
            return '<label class="pr-opt"><input type="radio" name="pr-act" value="' + esc(o[0]) + '"' + (A.action === o[0] ? " checked" : "") + (isDone(P.view) ? " disabled" : "") + "><span>" + esc(o[1]) + "</span></label>";
          }).join("") + submitBtn("Save Next Step") + msg();
      },
      flag_claim: function () {
        var c = D.flagClaim, L = A.lines = A.lines || {};
        return '<div class="pr-h">Claim ' + esc(c.id) + " · Ready To Submit</div>" +
          '<div class="grid sm:grid-cols-4 gap-3 mt-3 text-[12.5px]"><div><span class="pr-lbl">Patient</span>' + esc(c.patient) + '</div><div><span class="pr-lbl">Payer</span>' + esc(c.payer) +
          '</div><div><span class="pr-lbl">Member ID</span><span class="font-mono">' + esc(c.member) + '</span></div><div><span class="pr-lbl">Place Of Service</span>' + esc(c.pos) + "</div></div>" +
          '<div class="mt-3"><span class="pr-lbl">Box 21 · Diagnoses</span>' + c.dx.map(function (d) { return '<span class="inline-block mr-4 text-[13px]"><b>' + esc(d[0]) + ".</b> <span class='font-mono'>" + esc(d[1]) + "</span> " + esc(d[2]) + "</span>"; }).join("") + "</div>" +
          '<table class="pr-tbl mt-3"><thead><tr><th>#</th><th>CPT</th><th>Description</th><th>Dx Pointer</th><th>Charge</th><th>Problem?</th></tr></thead><tbody>' + c.lines.map(function (l, i) {
            return "<tr><td>" + (i + 1) + "</td><td class='font-mono font-semibold'>" + esc(l.cpt) + "</td><td>" + esc(l.label) + "</td><td class='font-mono'>" + esc(l.pointer) + "</td><td>$" + l.charge.toFixed(2) +
              '</td><td><select class="pr-in" style="min-width:210px" data-pr-line="' + esc(l.key) + '" aria-label="Problem on line ' + (i + 1) + '"' + (isDone(P.view) ? " disabled" : "") + '><option value="">Choose…</option>' +
              D.lineIssues.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (L[l.key] === o[0] ? " selected" : "") + ">" + esc(o[1]) + "</option>"; }).join("") + "</select></td></tr>";
          }).join("") + "</tbody></table>" + submitBtn("Save Review") + msg();
      },
      find_item: function () {
        return '<div class="pr-h">To-Do</div><div class="pr-sub">Only what needs a person.</div><table class="pr-tbl mt-3"><thead><tr><th>Staff Member</th><th>To-Do</th><th></th></tr></thead><tbody>' +
          D.todo.map(function (t) { return "<tr><td class='font-semibold'>" + esc(t.name) + "</td><td>" + esc(t.text) + '</td><td class="text-right"><button type="button" class="pr-btn sm" data-pr-item="' + esc(t.key) + '">Open</button></td></tr>'; }).join("") +
          "</tbody></table>" + msg();
      },
      decide_doc: function () {
        var d = D.docReview, ro = isDone(P.view) ? " disabled" : "";
        return '<div class="pr-h">Review · ' + esc(d.document) + '</div><div class="pr-sub">Uploaded by ' + esc(d.staff) + " (" + esc(d.role) + ")</div>" +
          '<div class="grid sm:grid-cols-2 gap-4 mt-3"><div class="pr-side"><div class="pr-lbl">Staff record</div><div class="text-[14px] font-semibold text-ink-900">' + esc(d.staff) + "</div>" + esc(d.role) +
          '</div><div class="pr-side"><div class="pr-lbl">What the ID says</div><div class="text-[14px] font-semibold text-ink-900">' + esc(d.nameOnId) + "</div>Date of birth " + esc(fmtDob(d.dob)) + " · Expires " + esc(fmtDob(d.expires)) + "</div></div>" +
          '<div class="pr-lbl mt-4">Your decision</div>' + [["approve", "Approve"], ["request_correction", "Request Correction"], ["reject", "Reject"]].map(function (o) {
            return '<label class="pr-opt"><input type="radio" name="pr-dec" value="' + o[0] + '"' + (A.action === o[0] ? " checked" : "") + ro + "><span>" + o[1] + "</span></label>";
          }).join("") + '<label class="pr-lbl mt-3" for="pr-reason">Message to Emma (she sees this)</label><textarea class="pr-in" id="pr-reason" data-pr-field="reason" rows="2"' + ro + ">" + esc(A.reason || "") + "</textarea>" +
          submitBtn("Send Decision") + msg();
      },
      access_request: function () {
        return '<div class="pr-h">Message from Dana (Front Desk)</div><div class="pr-side mt-3 text-[13.5px]">"Can I get Claims access just for this week? Billing is behind and I could help."</div>' +
          '<div class="pr-lbl mt-4">What do you do?</div>' + D.accessChoices.map(function (o) {
            return '<label class="pr-opt"><input type="radio" name="pr-acc" value="' + esc(o[0]) + '"' + (A.choice === o[0] ? " checked" : "") + (isDone(P.view) ? " disabled" : "") + "><span>" + esc(o[1]) + "</span></label>";
          }).join("") + submitBtn("Reply") + msg();
      }
    };

    var all = tasks.every(function (t) { return isDone(t.key); });
    el.innerHTML = '<div class="pr-wrap"><div>' + taskList() + "</div>" +
      '<div class="pr-app" aria-label="Practice copy of Althais"><div class="pr-bar"><b>ALTHAIS</b><span>Practice Mode</span><span class="pr-badge">' + (all ? "All tasks done ✓" : "Made-up data") + "</span></div>" +
      '<div class="pr-body">' + (VIEWS[P.view] ? VIEWS[P.view]() : "") + "</div></div></div>";

    if (!el.__prWired) {
      el.__prWired = true;
      function send(answer) {
        var key = P.view; P.busy = true; render(el, card, ctx);
        ctx.api("POST", "/api/portal/course/althais/practice", { task: key, answer: answer }).then(function (r) {
          P.busy = false; P.msg[key] = { ok: r.ok, text: r.message }; P.done = r.done || P.done;
          render(el, card, ctx); if (ctx.onChange) ctx.onChange(r);
        }).catch(function (e) { P.busy = false; P.msg[key] = { ok: false, text: e.message }; render(el, card, ctx); });
      }
      el.addEventListener("input", function (e) {
        var cur = el.__pr, A2 = cur.a[cur.view] || {};
        if (e.target.id === "pr-q") { cur.q = e.target.value; var pos = e.target.selectionStart; render(el, card, ctx); var q = el.querySelector("#pr-q"); if (q) { q.focus(); q.setSelectionRange(pos, pos); } return; }
        if (e.target.dataset.prField) { (A2.v = A2.v || {}); if (e.target.dataset.prField === "reason") A2.reason = e.target.value; else A2.v[e.target.dataset.prField] = e.target.value; }
      });
      el.addEventListener("change", function (e) {
        var cur = el.__pr, A2 = cur.a[cur.view] = cur.a[cur.view] || {};
        if (e.target.dataset.prLine) { (A2.lines = A2.lines || {})[e.target.dataset.prLine] = e.target.value; }
        if (e.target.name === "pr-act") A2.action = e.target.value;
        if (e.target.name === "pr-dec") A2.action = e.target.value;
        if (e.target.name === "pr-acc") A2.choice = e.target.value;
      });
      el.addEventListener("click", function (e) {
        var t, cur = el.__pr, A2 = cur.a[cur.view] = cur.a[cur.view] || {};
        if ((t = e.target.closest("[data-pr-task]")) && !t.disabled) { cur.view = t.dataset.prTask; cur.q = ""; return render(el, card, ctx); }
        if ((t = e.target.closest("[data-pr-open]"))) return send({ mrn: t.dataset.prOpen });
        if ((t = e.target.closest("[data-pr-claim]"))) return send({ claim: t.dataset.prClaim });
        if ((t = e.target.closest("[data-pr-item]"))) return send({ item: t.dataset.prItem });
        if ((t = e.target.closest("[data-pr-filter]"))) { cur.filter = t.dataset.prFilter; return render(el, card, ctx); }
        if ((t = e.target.closest("[data-pr-slot]")) && !t.disabled) { var sp = t.dataset.prSlot.split("|"); A2.t = sp[0]; A2.provider = sp[1]; return render(el, card, ctx); }
        if ((t = e.target.closest("[data-pr-code]")) && !t.disabled) { var cp = t.dataset.prCode.split("|"); (A2.decisions = A2.decisions || {})[cp[0]] = cp[1]; return render(el, card, ctx); }
        if ((t = e.target.closest("[data-pr-submit]")) && !t.disabled) {
          var k = cur.view;
          if (k === "fix_registration" || k === "fix_vitals") return send(A2.v);
          if (k === "fix_note") return send({ hpi: A2.v.hpi, allergies: A2.v.allergies });
          if (k === "book_followup") { if (!A2.t) { cur.msg[k] = { ok: false, text: "Pick a time slot first." }; return render(el, card, ctx); } return send({ t: A2.t, provider: A2.provider }); }
          if (k === "review_codes") return send({ decisions: A2.decisions || {} });
          if (k === "denial_step") return send({ action: A2.action });
          if (k === "flag_claim") return send({ lines: A2.lines || {} });
          if (k === "decide_doc") return send({ action: A2.action, reason: A2.reason || "" });
          if (k === "access_request") return send({ choice: A2.choice });
        }
      });
      el.addEventListener("keydown", function (e) { if (e.key === "Enter" && e.target.id === "pr-q") e.preventDefault(); });
    }
  }

  window.AlthaisPractice = { render: render };
})();
