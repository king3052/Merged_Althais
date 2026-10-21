/* v4 working preview, part 1: shared forms, Overview, Schedule, Patients, Patient chart, Documents. */
(function () {
  var app = document.getElementById('app'), E = U.esc, M = U.money, I = U.I, S = D.S;
  var V = window.V = { pages: {} };
  var q = V.q = new URLSearchParams(location.search);
  V.app = app;
  V.link = function (text, href, attrs) { return '<a class="tc" href="' + href + '"' + (attrs || '') + '>' + E(text) + '</a>'; };
  V.fmtT = function (t) { if (!t) return ''; var p = t.split(':'), h = +p[0]; return (h % 12 || 12) + ':' + p[1] + ' ' + (h < 12 ? 'AM' : 'PM'); };
  V.chartHref = function (mrn) { return '/v4/emr/chart?mrn=' + encodeURIComponent(mrn); };
  V.claimHref = function (id) { return '/v4/revenue/claims?claim=' + encodeURIComponent(id); };
  V.encHref = function (id) { return '/v4/emr/encounter?enc=' + encodeURIComponent(id); };
  V.ptOptions = function () { return S.patients.filter(function (p) { return p.status !== 'Inactive'; }).sort(function (a, b) { return a.name.localeCompare(b.name); }).map(function (p) { return [p.mrn, p.name + '  (' + p.mrn + ')']; }); };
  V.provOptions = function () { return D.providers().map(function (s) { return [s.id, s.name + (s.cred ? ' ' + s.cred : '')]; }); };
  V.myProvider = function () { var me = D.staff(S.clinic.userStaff); return me && me.provider ? me : D.providers()[0] || null; };
  V.splitName = function (n) { n = String(n || '').trim(); if (/,/.test(n)) return n.replace(/\s*,\s*/, ', '); var p = n.split(/\s+/); return p.length > 1 ? p[p.length - 1] + ', ' + p.slice(0, -1).join(' ') : n; };
  V.lines = function (t) { return String(t || '').split(/\n|;/).map(function (x) { return x.trim(); }).filter(Boolean); };
  V.ptStatus = function (p) { return !p.payer || p.payer === 'Self-pay' ? ['Self-pay', 'mut'] : !p.member ? ['Coverage check', 'amb'] : p.balance > 0 ? ['Balance due', 'amb'] : p.status === 'New' ? ['New', 'blue'] : ['Active', 'grn']; };
  V.redraw = null;
  V.refresh = function () { if (V.redraw) V.redraw(); };

  /* ---------- shared forms ---------- */
  U.on(document.body, '#ptalthea', 'click', function () { U.askAlthea('New patient ', /schedule/.test(location.pathname) ? 'e.g. New patient Maria Rivera, DOB 4/18/1962, Aetna PPO W2840, tomorrow at 10 for knee pain' : 'e.g. New patient Maria Rivera, DOB 4/18/1962, female, Aetna PPO W2840'); });
  V.patientForm = function (p, after, preset) {
    var isNew = !p; p = p || Object.assign({ sex: 'F', payer: 'Medicare B', status: 'New' }, preset || {});
    if (isNew && !D.providers().length) U.toast('Tip: add a provider in Staff › Team so patients can have a PCP.');
    U.form(isNew ? 'New patient' : 'Edit patient', [
      { k: 'name', label: 'Name (Last, First)', req: 1, span: 2, ph: 'Garcia, Daniel' }, { k: 'dob', label: 'Date of birth', type: 'date', req: 1 }, { k: 'sex', label: 'Sex', type: 'select', options: ['F', 'M', 'X'] },
      { k: 'phone', label: 'Phone', ph: '(512) 555-0100' }, { k: 'email', label: 'Email', type: 'email' }, { k: 'addr', label: 'Address', span: 2, ph: 'Street, City, ST ZIP' },
      { k: 'payer', label: 'Primary insurance', type: 'select', options: p.payer && D.PAYERS.indexOf(p.payer) < 0 ? [p.payer].concat(D.PAYERS) : D.PAYERS }, { k: 'member', label: 'Member ID' }, { k: 'pcp', label: 'PCP', type: 'select', options: V.provOptions(), blank: 'None' }, { k: 'balance', label: 'Patient balance', type: 'number', step: '0.01' },
      { k: 'allergies', label: 'Allergies', span: 2, ph: 'Sulfa (rash); NKDA' }, { k: 'problems', label: 'Problem list', span: 2, ph: 'E11.9 Type 2 diabetes; I10 Hypertension' },
      { k: 'meds', label: 'Current medications', type: 'textarea', rows: 2, span: 4, ph: 'One per line' },
    ], p, function (v) {
      if (v.dob > D.today()) return 'Date of birth is in the future.';
      v.name = V.splitName(v.name); v.balance = Number(v.balance) || 0;
      if (isNew) { v.mrn = D.nextMrn(v.name); v.created = D.today(); v.status = 'New'; S.patients.push(v); D.log('Patient added: ' + v.name); }
      else { Object.assign(p, v); p.edited = true; D.log('Patient updated: ' + p.name); }
      D.save(); if (after) after(isNew ? v : p); else V.refresh();
      U.toast(isNew ? v.name + ' added, MRN ' + v.mrn + '.' : 'Saved.');
    }, { wide: 1, save: isNew ? 'Add patient' : 'Save' });
  };
  V.apptForm = function (a, after, preset) {
    if (!S.patients.length) { U.toast('Add a patient first.'); return V.patientForm(null, function () { V.apptForm(a, after, preset); }); }
    if (!D.providers().length) { U.toast('Add a provider first (Staff › Team).'); return V.staffForm(null, function () { V.apptForm(a, after, preset); }, true); }
    var isNew = !a; a = a || Object.assign({ date: V.day || D.today(), time: '09:00', prov: (V.myProvider() || {}).id, visit: 'Scheduled' }, preset || {});
    U.form(isNew ? 'New appointment' : 'Edit appointment', [
      { k: 'mrn', label: 'Patient', type: 'select', options: V.ptOptions(), req: 1, span: 2, blank: 'Choose a patient' }, { k: 'date', label: 'Date', type: 'date', req: 1 }, { k: 'time', label: 'Time', type: 'time', req: 1, step: 900 },
      { k: 'prov', label: 'Provider', type: 'select', options: V.provOptions(), req: 1 }, { k: 'type', label: 'Visit type', type: 'select', options: ['Office visit', 'Telehealth', 'New patient', 'Annual wellness', 'Procedure'] },
      { k: 'reason', label: 'Reason for visit', span: 2, req: 1, ph: 'Diabetes follow-up' },
    ], a, function (v) {
      var clash = S.appts.filter(function (x) { return x !== a && x.date === v.date && x.time === v.time && x.prov === v.prov && x.visit !== 'Cancelled'; })[0];
      if (clash) return D.provName(v.prov) + ' already has ' + D.ptName(clash.mrn) + ' at ' + V.fmtT(v.time) + '.';
      if (isNew) { v.id = D.id('A'); v.visit = 'Scheduled'; S.appts.push(v); D.log('Appointment booked: ' + D.ptName(v.mrn) + ', ' + D.us(v.date) + ' ' + V.fmtT(v.time)); }
      else Object.assign(a, v);
      D.save(); if (after) after(isNew ? v : a); else V.refresh(); U.toast(isNew ? 'Appointment booked.' : 'Saved.');
    }, { wide: 1, save: isNew ? 'Book' : 'Save' });
  };
  V.staffForm = function (s, after, provider, invite) {
    var isNew = !s; s = s || { access: provider ? 'Provider' : 'Clinical', provider: !!provider, loc: (S.locations[0] || {}).name || 'Main', status: invite ? 'Invited' : 'Active', hired: D.today() };
    U.form(isNew ? (invite ? 'Invite member' : provider ? 'Add provider' : 'Add staff') : 'Edit ' + s.name, [
      { k: 'name', label: 'Name (Last, First)', req: 1, span: 2 }, { k: 'cred', label: 'Credentials', ph: 'MD, DO, PA-C, RN, MA' }, { k: 'spec', label: 'Specialty / dept.', ph: 'Family medicine' },
      { k: 'email', label: 'Email', type: 'email', req: !!invite }, { k: 'phone', label: 'Phone' }, { k: 'loc', label: 'Location', ph: 'Main' }, { k: 'access', label: 'Access', type: 'select', options: ['Admin', 'Provider', 'Clinical', 'Billing', 'Front desk'] },
      { k: 'npi', label: 'Individual NPI', ph: '10 digits' }, { k: 'hired', label: 'Hired', type: 'date' }, { k: 'hours', label: 'Schedule this week', span: 2, ph: 'Mon to Thu 7:30 AM to 4:30 PM' }, { k: 'assigned', label: 'Assigned to', span: 2, ph: 'Test, D; Wu, E' }, { k: 'provider', label: 'Rendering provider (signs notes, bills claims)', type: 'check', span: 2 },
    ], s, function (v) {
      if (v.npi && !/^\d{10}$/.test(v.npi)) return 'NPI must be 10 digits.';
      v.name = V.splitName(v.name);
      if (isNew) {
        v.id = D.id('S'); v.status = invite ? 'Invited' : 'Active'; v.creds = []; v.reminders = []; v.training = [];
        if (invite) v.steps = [['Offer letter and I-9', false], ['HIPAA and OSHA training', false], ['Background check', false], ['License or certification upload', false], ['EMR access and role', false]];
        S.staff.push(v); D.log((invite ? 'Invited ' : 'Added ') + v.name);
        if (!S.clinic.userStaff) { S.clinic.userStaff = v.id; S.clinic.user = v.name.split(', ').reverse().join(' ') + (v.cred ? ', ' + v.cred : ''); }   // the first person added is you
      } else Object.assign(s, v);
      D.save(); if (after) after(isNew ? v : s); else V.refresh(); U.toast(isNew ? (invite ? 'Invite recorded for ' + v.email + '.' : v.name + ' added.') : 'Saved.');
    }, { wide: 1, save: isNew ? (invite ? 'Invite' : 'Add') : 'Save' });
  };
  V.pickPatient = function (title, cb) {
    if (!S.patients.length) { U.toast('No patients yet. Add one first.'); return V.patientForm(null, function (p) { cb(p.mrn); }); }
    var w = U.modal(title, '<div class="pick" style="padding:0"><input id="pk" placeholder="Search name, MRN or DOB"><div id="pkl" style="max-height:320px;overflow:auto"></div></div>', [['New Patient', '', function () { V.patientForm(null, function (p) { cb(p.mrn); }); }], ['Cancel', '', function () {}]]);
    var draw = function () {
      var s = w.querySelector('#pk').value.toLowerCase(), l = S.patients.filter(function (p) { return (p.name + p.mrn + (p.dob || '')).toLowerCase().indexOf(s) >= 0; }).slice(0, 40);
      w.querySelector('#pkl').innerHTML = '<table class="g tight"><tbody>' + (l.map(function (p) { return '<tr class="click" data-pk="' + p.mrn + '"><td>' + E(p.name) + '</td><td class="mono">' + p.mrn + '</td><td>' + D.us(p.dob) + '</td><td>' + E(p.payer || '') + '</td></tr>'; }).join('') || U.empty(4, 'No match')) + '</tbody></table>';
    };
    draw(); w.querySelector('#pk').oninput = draw;
    w.addEventListener('click', function (e) { var r = e.target.closest('[data-pk]'); if (r) { w.remove(); cb(r.dataset.pk); } });
  };
  // open today's unsigned visit for this patient, or start one
  V.startVisit = function (mrn, apptId) {
    var p = D.pt(mrn); if (!p) return;
    var booked = apptId && S.appts.filter(function (a) { return a.id === apptId; })[0];
    if (booked && booked.encId && D.enc(booked.encId)) { location.href = V.encHref(booked.encId); return; }   // this appointment already has its visit
    var e = S.encs.filter(function (x) { return x.mrn === mrn && x.status === 'Draft' && (apptId ? x.apptId === apptId : x.date === D.today()); })[0];
    if (!e) {
      var appt = apptId ? S.appts.filter(function (a) { return a.id === apptId; })[0] : S.appts.filter(function (a) { return a.mrn === mrn && a.date === D.today() && a.visit !== 'Cancelled'; })[0];
      var prov = appt ? appt.prov : (V.myProvider() || {}).id;
      if (!prov) { U.toast('Add a provider first (Staff › Team).'); return V.staffForm(null, function () { V.startVisit(mrn, apptId); }, true); }
      e = { id: D.id('E'), mrn: mrn, apptId: appt ? appt.id : '', date: appt ? appt.date : D.today(), prov: prov, visitType: appt && appt.type === 'Telehealth' ? 'Telehealth' : 'Office visit', reason: appt ? appt.reason : '',
        status: 'Draft', s: appt && appt.reason ? 'Chief complaint: ' + appt.reason + '.\n' : '', o: '', a: '', p: '', vitals: {}, transcript: [], orders: [], codes: [], addenda: [], ignored: {}, created: new Date().toISOString() };
      S.encs.push(e);
      if (appt) { appt.encId = e.id; if (appt.visit === 'Scheduled' || appt.visit === 'Checked in') appt.visit = 'In room'; }
      D.log('Visit started: ' + p.name); D.save();
    }
    location.href = V.encHref(e.id);
  };
  V.sendMessage = function (mrn) {
    var p = D.pt(mrn);
    U.form('Message ' + (p ? p.name : 'patient'), [{ k: 'via', label: 'Send by', type: 'select', options: ['Portal', 'Text', 'Email'] }, { k: 'to', label: 'To', def: p ? (p.phone || p.email || '') : '' }, { k: 'text', label: 'Message', type: 'textarea', req: 1, span: 4 }], {}, function (v) {
      S.messages.unshift({ id: D.id('MSG'), mrn: mrn, via: v.via, to: v.to, text: v.text, at: new Date().toISOString() });
      S.outgoing.unshift({ id: D.id('O'), mrn: mrn, doc: 'Message: ' + v.text.slice(0, 40), to: p ? p.name : '', method: v.via, sent: D.today(), status: 'Sent' });
      D.log('Message sent to ' + (p ? p.name : 'patient') + ' by ' + v.via.toLowerCase()); D.save(); V.refresh(); U.toast('Message recorded. (This preview does not deliver messages.)');
    }, { save: 'Send' });
  };
  V.banner = function (mrn, end) {
    var p = D.pt(mrn); if (!p) return '';
    var allergies = V.lines(p.allergies).filter(function (x) { return !/^nkda$/i.test(x); });
    return '<div class="pb-ban"><span class="pb-av">' + U.initials2(p.name.split(', ').reverse().join(' ')) + '</span><div><div><span class="pb-name">' + E(p.name) + '</span> <span class="pb-meta">' + p.sex + ', ' + D.age(p.dob) + ', DOB ' + D.us(p.dob) + ', MRN ' + p.mrn + '</span></div>' +
      '<div class="pb-meta">' + E(p.payer || 'No insurance') + (p.eligChecked ? ', eligibility checked ' + D.us(p.eligChecked, 1) : ', eligibility not checked') + '  |  PCP ' + E(D.provName(p.pcp) || 'none') + '</div></div>' +
      (allergies.length ? '<span class="flag red">Allergy: ' + E(allergies.join(', ')) + '</span>' : '<span class="flag" style="color:var(--t2);border-color:var(--b)">No known allergies</span>') +
      (!p.member && p.payer !== 'Self-pay' ? '<span class="flag amb">Member ID missing</span>' : '') + '<div class="pb-end">' + (end || '') + '</div></div>';
  };

  /* ================= Overview ================= */
  V.pages.overview = function () {
    function draw() {
      var t = D.totals(), today = D.today(), appts = S.appts.filter(function (a) { return a.date === today && a.visit !== 'Cancelled'; }).sort(function (a, b) { return a.time.localeCompare(b.time); });
      var seen = appts.filter(function (a) { return a.visit === 'Checked out' || a.visit === 'In room'; }).length, drafts = S.encs.filter(function (e) { return e.status === 'Draft'; });
      var tasks = [];
      if (!D.providers().length) tasks.push(['Setup', '', 'Add your providers', 'Needed to schedule and sign notes', null, 'Add provider', '/v4/staff/team?add=provider']);
      if (!S.patients.length) tasks.push(['Setup', '', 'Add patients', 'One at a time or import a CSV', null, 'Add patients', '/v4/emr/patients']);
      if (!S.patients.some(function (p) { return p.sample; }) && S.claims.length < 3) tasks.push(['Try it', 'blue', 'Sample clinic', 'A full clinic day: patients, schedule, a visit and claims in every stage', null, 'Load sample', '/v4/overview?sample=1']);
      if (!S.clinic.npi) tasks.push(['Setup', '', 'Practice details', 'Group NPI and billing address go on every claim', null, 'Open settings', '/v4/settings']);
      D.stage('edits').forEach(function (c) { var x = c.edits[0] || {}; tasks.push(['Claim edit', x.lvl || 'amb', D.ptName(c.mrn), (x.label || 'Edit') + ' (' + (x.id || '') + ')', D.charge(c), 'Resolve', V.claimHref(c.id)]); });
      drafts.forEach(function (e) { tasks.push(['Sign note', 'blue', D.ptName(e.mrn), 'Visit ' + D.us(e.date, 1) + ', ' + (e.reason || 'note in progress'), null, 'Open', V.encHref(e.id)]); });
      S.encs.forEach(function (e) { (e.addenda || []).forEach(function (a) { if (!a.signed) tasks.push(['Sign addendum', 'amb', D.ptName(e.mrn), a.text.slice(0, 60), null, 'Review', V.encHref(e.id)]); }); });
      D.stage('denied').filter(function (c) { return ['Appealed', 'Resubmitted'].indexOf(c.dstatus) < 0; }).forEach(function (c) { tasks.push(['Appeal due', 'amb', D.ptName(c.mrn), c.carc + ', due ' + D.us(c.appealBy, 1), D.charge(c), 'Open appeal', '/v4/revenue/denials?claim=' + c.id]); });
      S.staff.forEach(function (s) { (s.creds || []).forEach(function (cr) { var d = cr.exp ? D.daysBetween(today, cr.exp) : 999; if (d <= 30) tasks.push(['Credential', d < 0 ? 'red' : 'amb', null, s.name + ': ' + cr.name + (d < 0 ? ' expired ' : ' expires ') + D.us(cr.exp, 1), null, 'Remind', '/v4/staff/team?staff=' + s.id]); }); });
      appts.forEach(function (a) { var p = D.pt(a.mrn); if (p && p.payer !== 'Self-pay' && !p.member) tasks.push(['Coverage', 'amb', p.name, 'Member ID missing, seen today ' + V.fmtT(a.time), null, 'Fix', V.chartHref(p.mrn)]); });
      var days14 = []; for (var i = 13; i >= 0; i--) days14.push(D.addDays(today, -i));
      var era = days14.map(function (d) { return D.sum(S.claims.filter(function (c) { return c.stage === 'paid' && c.paidOn === d; }), function (c) { return c.paid; }); }), max = Math.max.apply(null, era.concat([1]));
      var month = today.slice(0, 7), paidM = S.claims.filter(function (c) { return c.stage === 'paid' && (c.paidOn || '').slice(0, 7) === month; }), byPayer = {};
      paidM.forEach(function (c) { var p = D.payer(c); (byPayer[p] = byPayer[p] || []).push(c); });
      var rulesNow = D.RULES.filter(function (r) { return Math.abs(D.daysBetween(today, r.eff)) <= 45; });
      var alerts = []; S.staff.forEach(function (s) { (s.creds || []).forEach(function (cr) { var d = cr.exp ? D.daysBetween(today, cr.exp) : 999; if (d <= 60) alerts.push([s.name, cr.name + (d < 0 ? ' expired ' : ' expires ') + D.us(cr.exp, 1), d < 0 ? 'red' : 'amb']); }); if (s.status === 'Invited') alerts.push([s.name, 'Onboarding ' + (s.steps || []).filter(function (x) { return x[1]; }).length + ' of ' + (s.steps || []).length + ' steps complete', '']); });
      app.innerHTML = U.header('overview') +
        U.toolbar('Overview', [['Start Visit', 'mic', { pri: 1, id: 'start' }], ['New Patient', 'plus', { id: 'newpt' , menu: [['New Patient', 'newpt2', 'user'], ['Add With Althea', 'ptalthea', '<svg class="ico" width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="currentColor"/></svg>']] }], ['Submit Claims', '', { href: '/v4/revenue/claims?q=ready' }], '|', ['Print', 'print', { id: 'print' }]], D.longDate(today)) +
        U.strip([['Patients today', seen + ' of ' + appts.length, (appts.length - seen) + ' remaining'], ['Unsigned notes', drafts.length, ''], ['Edits To Resolve', t.edits.n, M(t.edits.amt) + ' held', t.edits.n ? 'red' : ''],
          ['Ready To Submit', t.ready.n, M(t.ready.amt)], ['Collected MTD', M(t.collected), t.paidN + ' claims paid'], ['Denial rate, 90 days', t.r90 ? U.pct(t.d90 / t.r90) : '0.0%', t.d90 + ' of ' + t.r90]]) +
        '<div class="main" style="grid-template-columns:1.08fr 1.08fr .74fr"><div class="col">' +
          U.pane('Today’s schedule', appts.length + ' appointment' + (appts.length === 1 ? '' : 's'), V.link('Open schedule', '/v4/emr/schedule'), U.grid(['Time', 'Patient', 'Reason', 'Provider', 'Status'],
            appts.map(function (a) { return { attrs: ' data-href="/v4/emr/schedule?appt=' + a.id + '"', cells: [V.fmtT(a.time), E(D.ptName(a.mrn)), E(a.reason || ''), E(D.provName(a.prov)), V.apptStatus(a)] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'No appointments today.') + '</tbody>'), { cls: 'grow', style: 'flex:1.25' }) +
          U.pane('Claims pipeline, ' + new Date().toLocaleDateString('en-US', { month: 'long' }), t.all + ' claims', V.link('Open claims', '/v4/revenue/claims'),
            U.grid(['Stage', ['Claims', 'num'], ['Amount', 'num'], ['Share', 'num']], [['edits', 'Edits To Resolve', 'red'], ['ready', 'Ready To Submit', 'blue'], ['submitted', 'With Payer', ''], ['paid', 'Paid', 'grn'], ['denied', 'Denied', 'red']].map(function (s) {
              var x = t[s[0]], tot = D.sum(S.claims, D.charge) || 1;
              return { attrs: ' data-href="/v4/revenue/claims?q=' + s[0] + '"', cells: [s[2] === 'red' ? U.st(s[1], 'red') : '<span class="s-' + (s[2] || 'none') + '">' + s[1] + '</span>', x.n, M(x.amt), U.pct(x.amt / tot, 0)] };
            }), { click: 1 }), { footer: '<span>First-pass rate <b>' + (t.r90 ? U.pct(t.firstPass) : '-') + '</b></span><span>Avg days to pay <b>' + (t.avgPay == null ? '-' : t.avgPay.toFixed(1)) + '</b></span>' }) +
        '</div><div class="col">' +
          U.pane('My tasks', tasks.length + ' open', '', U.grid(['Type', 'Patient', 'Detail', ['Amount', 'num'], ''], tasks.map(function (k) {
            return { cells: [k[1] === 'red' || k[1] === 'amb' ? U.st(k[0], k[1]) : '<span class="tc s-' + (k[1] || 'none') + '">' + k[0] + '</span>', k[2] ? '<span class="tc">' + E(k[2]) + '</span>' : '<span class="s-mut">-</span>', '<span class="wrap">' + E(k[3]).replace(/^([^(]+) \(([A-Z0-9-]+)\)$/, '<span class="tc">$1</span> ($2)') + '</span>', k[4] == null ? '' : M(k[4]), V.link(k[5], k[6])] };
          })).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'Nothing open.') + '</tbody>'), {}) +
          U.pane('Collections, last 14 days', M(D.sum(era)), V.link('Paid claims', '/v4/revenue/claims?q=paid'),
            '<div class="bars">' + era.map(function (v, i) { return '<i title="' + D.us(days14[i]) + ': ' + M(v) + '" class="' + (i === 13 ? 'last' : '') + '" style="height:' + Math.max(1, v / max * 100) + '%"></i>'; }).join('') + '</div>' +
            '<div class="bars-x"><span>' + D.us(days14[0]).slice(0, 5) + '</span><span>' + D.us(days14[7]).slice(0, 5) + '</span><span>' + D.us(today).slice(0, 5) + '</span></div>', {}) +
          U.pane('Collections by payer', 'this month', '', U.grid(['Payer', ['Claims paid', 'num'], ['Collected', 'num'], ['Avg days to pay', 'num']], Object.keys(byPayer).map(function (p) {
            var l = byPayer[p]; return { cells: [E(p), l.length, M(D.sum(l, function (c) { return c.paid; })), (D.sum(l, function (c) { return c.submittedOn ? D.daysBetween(c.submittedOn, c.paidOn) : 0; }) / l.length).toFixed(1)] };
          })).replace('<tbody></tbody>', '<tbody>' + U.empty(4, 'No payments posted this month.') + '</tbody>'), { cls: 'grow' }) +
        '</div><div class="col">' +
          U.pane('Payer rule changes', 'affecting today', V.link('All rules', '/v4/revenue/payer-rules'), rulesNow.map(function (r) {
            var n = appts.filter(function (a) { var p = D.pt(a.mrn); return p && (p.payer || '').indexOf(r.payer) === 0; }).length;
            return '<div class="dp-sec"><div class="dp-h">' + E(r.payer) + '<span class="end mut">eff. ' + D.us(r.eff, 1) + '</span></div><div>' + E(r.req) + '</div>' + (n ? '<div class="mut">' + n + ' patient' + (n === 1 ? '' : 's') + ' today</div>' : '') + '</div>';
          }).join('') || '<div class="blank">No changes in this window.</div>', {}) +
          U.pane('Staff alerts', alerts.length, '', alerts.map(function (a) { return '<div class="dp-sec"><div class="b">' + E(a[0]) + '</div><div class="s-' + (a[2] || 'none') + '">' + E(a[1]) + '</div></div>'; }).join('') || '<div class="blank">No credentials expiring in 60 days.</div>', {}) +
          U.pane('Recent activity', '', '', '<div class="dp-sec kv">' + (S.log.slice(0, 12).map(function (l) { return '<div><span class="mono">' + new Date(l.at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) + '</span><b style="font-weight:400;text-align:right">' + E(l.text) + '</b></div>'; }).join('') || '<div><span>No activity yet</span></div>') + '</div>', { cls: 'grow' }) +
        '</div></div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    // live: redraw every 20 seconds and when you come back to the tab (not while a dialog is open or you're typing)
    var live = function () { if (V.redraw !== draw || document.hidden || document.querySelector('.mdl-wrap') || /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName)) return; draw(); };
    setInterval(live, 20000); document.addEventListener('visibilitychange', live); window.addEventListener('focus', live);
    if (q.get('sample')) { var how = q.get('sample'); history.replaceState(null, '', '/v4/overview'); if (how === 'reset') V.resetSample(); else V.loadSample(); }
    U.on(app, 'tr[data-href]', 'click', function (tr, e) { if (!e.target.closest('a')) location.href = tr.dataset.href; });
    U.on(app, '#start', 'click', function () { V.pickPatient('Start visit for', function (mrn) { V.startVisit(mrn); }); });
    U.on(app, '#newpt', 'click', function () { V.patientForm(); });
    U.on(app, '#newpt2', 'click', function () { V.patientForm(); });
  };
  V.apptStatus = function (a) {
    var e = a.encId && D.enc(a.encId);
    if (a.visit === 'Checked out') return e && e.status === 'Signed' ? '<span class="s-grn">Signed</span>' : U.st('Unsigned', 'amb');
    if (a.visit === 'In room') return a.encId && V.recording === a.encId ? '<span class="rec"><span class="dot"></span>Recording</span>' : E(a.room || 'In room') + (e ? ' <span class="s-mut">· drafting</span>' : '');
    if (a.visit === 'No show' || a.visit === 'Cancelled') return '<span class="s-red">' + a.visit + '</span>';
    return a.visit === 'Checked in' ? 'Checked in' : '<span class="s-mut">Scheduled</span>';
  };

  /* ================= Schedule ================= */
  V.pages.schedule = function () {
    var Q = 'all', view = 'day', sel = q.get('appt') || '';
    V.day = q.get('date') || (sel && (S.appts.filter(function (a) { return a.id === sel; })[0] || {}).date) || D.today();
    var dayAppts = function () { return S.appts.filter(function (a) { return a.date === V.day; }).sort(function (a, b) { return a.time.localeCompare(b.time); }); };
    var match = function (a) { return Q === 'all' || (Q.indexOf('p:') === 0 && a.prov === Q.slice(2)) || (Q.indexOf('v:') === 0 && a.visit === Q.slice(2)) || (Q === 'unsigned' && a.encId && (D.enc(a.encId) || {}).status === 'Draft') || (Q === 'signed' && a.encId && (D.enc(a.encId) || {}).status === 'Signed'); };
    function right() {
      var a = S.appts.filter(function (x) { return x.id === sel; })[0];
      if (!a) return U.pane('Selected', '', '', '<div class="blank">Select an appointment to check the patient in, room them, start the visit or check them out.</div>');
      var p = D.pt(a.mrn) || {}, e = a.encId && D.enc(a.encId), last = S.encs.filter(function (x) { return x.mrn === a.mrn && x.status === 'Signed' && x.id !== a.encId; }).sort(function (x, y) { return y.date.localeCompare(x.date); })[0];
      var bal = p.balance > 0, acts = [];
      if (a.visit === 'Scheduled') acts.push(['Check in', 'checkin']);
      if (a.visit === 'Checked in' || a.visit === 'Scheduled') acts.push(['Room patient', 'room']);
      if (a.visit !== 'Cancelled' && a.visit !== 'No show') acts.push([e ? 'Open visit note' : 'Start Virtual SOAP', 'visit']);
      if (a.visit === 'In room') acts.push(['Check out', 'checkout']);
      if (a.visit === 'Scheduled') { acts.push(['No show', 'noshow']); acts.push(['Cancel', 'cancel']); }
      acts.push(['Edit', 'edit']);
      return '<section class="pane"><div class="ph">Selected: ' + E(p.name || '') + '</div><div class="pbody">' +
        '<div class="dp-sec"><div class="dp-h">Visit</div>' + U.kv([['When', D.us(a.date) + ', ' + V.fmtT(a.time)], ['Provider', E(D.provName(a.prov))], ['Status', V.apptStatus(a)], ['Room', E(a.room || '-')], ['Virtual SOAP', e ? (e.status === 'Signed' ? '<span class="s-grn">Signed</span>' : 'Draft') : 'Not started']]) +
          '<div style="display:flex;gap:5px;flex-wrap:wrap;margin-top:8px">' + acts.map(function (x) { return '<button class="mini' + (x[1] === 'visit' ? ' pri' : '') + '" data-act="' + x[1] + '">' + x[0] + '</button>'; }).join('') + '</div></div>' +
        '<div class="dp-sec"><div class="dp-h">Coverage</div>' + U.kv([['Primary', E(p.payer || 'None')], ['Member ID', p.member ? '<span class="mono">' + E(p.member) + '</span>' : U.st('Missing', 'amb')], ['Eligibility', p.eligChecked ? 'Checked ' + D.us(p.eligChecked) : U.st('Not checked', 'amb')]]) +
          '<div style="margin-top:6px"><button class="mini" data-act="elig">Mark eligibility checked</button> <button class="mini" data-act="editpt">Edit patient</button></div></div>' +
        '<div class="dp-sec"><div class="dp-h">Before checkout</div><div class="ok-list">' + (bal ? '<div class="w">' + U.warn + 'Balance due ' + M(p.balance) + '</div>' : '<div>No balance due</div>') + (e && e.status === 'Signed' ? '<div>Note signed</div>' : '<div class="w">' + U.warn + 'Note not signed</div>') + '</div>' +
          (bal ? '<div style="margin-top:6px"><button class="mini" data-act="collect">Record payment</button></div>' : '') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Reason for visit</div>' + E(a.reason || '-') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Last visit ' + (last ? D.us(last.date) : '') + '</div>' + (last ? U.kv([['Provider', E(D.provName(last.prov))], ['Assessment', E((last.a || '-').slice(0, 60))]]) : '<span class="mut">No earlier signed visit</span>') + '</div>' +
        '</div><div class="dp-f"><a class="btn" style="display:inline-flex;align-items:center" href="' + V.chartHref(a.mrn) + '">Open chart</a><button class="btn pri" data-act="visit">Open encounter</button></div></section>';
    }
    function draw() {
      var all = dayAppts(), rows = all.filter(match), provs = [];
      all.forEach(function (a) { if (provs.indexOf(a.prov) < 0) provs.push(a.prov); }); D.providers().forEach(function (p) { if (provs.indexOf(p.id) < 0) provs.push(p.id); });
      var cnt = function (f) { return all.filter(f).length; }, drafts = S.encs.filter(function (e) { return e.status === 'Draft'; });
      var tmr = D.addDays(V.day, 1), tom = S.appts.filter(function (a) { return a.date === tmr && a.visit !== 'Cancelled'; }).sort(function (a, b) { return a.time.localeCompare(b.time); });
      var dayClaims = S.claims.filter(function (c) { return c.dos === V.day; });
      var mid;
      if (view === 'week') {
        var start = D.addDays(V.day, -((new Date(V.day + 'T12:00:00').getDay() + 6) % 7)), wk = S.appts.filter(function (a) { return a.date >= start && a.date <= D.addDays(start, 6); }).sort(function (a, b) { return (a.date + a.time).localeCompare(b.date + b.time); });
        mid = '<section class="pane"><div class="ph">Week of ' + D.us(start) + ' <span class="sub">' + wk.length + ' appointments</span><span class="end"><button class="mini" data-nav="-7">‹</button> <button class="mini" data-nav="0">This week</button> <button class="mini" data-nav="7">›</button> <button class="mini" data-view="day">Day</button></span></div><div class="pbody">' +
          U.grid(['Date', 'Time', 'Patient', 'Reason', 'Provider', 'Status'], wk.map(function (a) { return { attrs: ' data-appt="' + a.id + '" data-date="' + a.date + '"', cells: [D.us(a.date), V.fmtT(a.time), E(D.ptName(a.mrn)), E(a.reason || ''), E(D.provName(a.prov)), V.apptStatus(a)] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No appointments this week.') + '</tbody>') +
          '</div><div class="pf"><span class="end">Click an appointment to open that day</span></div></section>';
      } else mid = '<section class="pane"><div class="ph">' + D.longDate(V.day) + ' <span class="sub">' + rows.length + ' appointment' + (rows.length === 1 ? '' : 's') + '</span><span class="end"><button class="mini" data-nav="-1">‹</button> <button class="mini" data-nav="0">Today</button> <button class="mini" data-nav="1">›</button> <input type="date" class="mini" id="goday" value="' + V.day + '"> <button class="mini" data-view="week">Week</button></span></div><div class="pbody">' +
        U.grid(['Time', 'Patient', 'MRN', 'Reason', 'Payer', 'Provider', 'Visit', 'Note', 'Coding'], rows.map(function (a) {
          var p = D.pt(a.mrn) || {}, e = a.encId && D.enc(a.encId), cl = e && S.claims.filter(function (c) { return c.encId === e.id; })[0];
          var acc = e ? e.codes.filter(function (c) { return c.state === 'acc'; }).map(function (c) { return c.code; }).join(', ') : '';
          return { sel: a.id === sel, attrs: ' data-appt="' + a.id + '"', cells: [V.fmtT(a.time), V.link(p.name || '?', V.chartHref(a.mrn)), '<span class="mono">' + a.mrn + '</span>', E(a.reason || ''), E(p.payer || ''), E(D.provName(a.prov)),
            a.visit === 'In room' ? E(a.room || 'In room') : a.visit === 'Scheduled' ? '<span class="s-mut">Scheduled</span>' : a.visit === 'No show' || a.visit === 'Cancelled' ? '<span class="s-red">' + a.visit + '</span>' : a.visit,
            e ? (e.status === 'Signed' ? '<span class="s-grn">Signed</span>' : a.visit === 'Checked out' ? '<span class="s-amb">Unsigned</span>' : 'Drafting') : '',
            cl ? (cl.stage === 'edits' ? U.st(acc || 'Edit', (cl.edits[0] || {}).lvl || 'amb') : E(acc)) : acc ? '<span class="s-mut">' + E(acc) + '</span>' : (!p.member && p.payer !== 'Self-pay' ? U.st('Member ID', 'amb') : '')] };
        }), { cb: 1, click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(10, S.patients.length ? 'No appointments. Use New appointment to book one.' : 'No patients yet. Add one with New patient.') + '</tbody>') +
        U.sect('Unsigned notes', drafts.length + ', claims held until signed', U.grid(['DOS', 'Patient', 'Provider', 'Note', ['Est. charge', 'num']], drafts.map(function (e) {
          var est = D.sum(e.codes.filter(function (c) { return c.state === 'acc' && c.type === 'CPT'; }), function (c) { return D.fee(c.code); });
          return { attrs: ' data-enc="' + e.id + '"', cells: [D.us(e.date, 1), E(D.ptName(e.mrn)), E(D.provName(e.prov)), V.recording === e.id ? 'Recording in progress' : 'Virtual SOAP draft', M(est)] };
        }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'None.') + '</tbody>')) +
        U.sect('Next day, ' + D.us(tmr).slice(0, 5), tom.length + ' appointments', U.grid(['Time', 'Patient', 'Reason', 'Payer', 'Eligibility'], tom.map(function (a) {
          var p = D.pt(a.mrn) || {}; return { attrs: ' data-appt="' + a.id + '" data-date="' + a.date + '"', cells: [V.fmtT(a.time), E(p.name || ''), E(a.reason || ''), E(p.payer || ''), p.eligChecked ? '<span class="s-grn">Checked ' + D.us(p.eligChecked, 1) + '</span>' : !p.member && p.payer !== 'Self-pay' ? U.st('Member ID missing', 'amb') : U.st('Not checked', 'amb')] };
        }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'Nothing booked.') + '</tbody>')) +
        '</div><div class="pf"><span>' + (sel ? 1 : 0) + ' selected</span><span class="end">Showing ' + rows.length + ' of ' + all.length + '</span></div></section>';
      app.innerHTML = U.header('schedule') +
        U.toolbar('Schedule', [['Start Virtual SOAP', 'mic', { pri: 1, id: 'start' }], ['New Appointment', 'plus', { id: 'newappt', menu: [['New Appointment', 'newappt2', 'plus'], ['Book With Althea', 'bookalthea', '<svg class="ico" width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="currentColor"/></svg>']] }], ['New Patient', 'user', { id: 'newpt' , menu: [['New Patient', 'newpt2', 'user'], ['Book With Althea', 'ptalthea', '<svg class="ico" width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="currentColor"/></svg>']] }], '|', ['Scheduler', 'cal', { id: 'week' }], ['Fax / Msg', 'fax', { id: 'faxmsg' }], ['Print', 'print', { id: 'print' }], ['Import CSV', 'upload', { id: 'importcsv' }]]) +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr) 330px">' +
          U.pane('Providers', '', '', U.queue([
            { h: 'Day, ' + D.us(V.day).slice(0, 5), items: [{ key: 'all', label: 'All providers', n: all.length, color: 'blue' }].concat(provs.map(function (id) { return { key: 'p:' + id, label: D.provName(id), n: cnt(function (a) { return a.prov === id; }), color: 'lblue' }; })) },
            { h: 'Status', items: [['Checked out', 'gray'], ['In room', 'red'], ['Checked in', 'blue'], ['Scheduled', 'gray'], ['No show', 'amb'], ['Cancelled', 'gray']].map(function (s) { return { key: 'v:' + s[0], label: s[0], n: cnt(function (a) { return a.visit === s[0]; }), color: s[1] }; }) },
            { h: 'Notes', items: [{ key: 'unsigned', label: 'Unsigned', n: cnt(function (a) { return a.encId && (D.enc(a.encId) || {}).status === 'Draft'; }), color: 'amb' }, { key: 'signed', label: 'Signed', n: cnt(function (a) { return a.encId && (D.enc(a.encId) || {}).status === 'Signed'; }), color: 'grn' }] },
          ], Q), { after: U.sum([['Seen', cnt(function (a) { return a.visit === 'Checked out' || a.visit === 'In room'; }) + ' of ' + cnt(function (a) { return a.visit !== 'Cancelled'; })], ['Charges', M(D.sum(dayClaims, D.charge))], ['Held by edits', M(D.sum(dayClaims.filter(function (c) { return c.stage === 'edits'; }), D.charge))]]) }) +
          mid + right() + '</div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    var A = function () { return S.appts.filter(function (x) { return x.id === sel; })[0]; };
    U.on(app, '[data-q]', 'click', function (el) { Q = el.dataset.q; draw(); });
    U.on(app, 'tr[data-appt]', 'click', function (tr, e) { if (e.target.closest('a')) return; sel = tr.dataset.appt; if (tr.dataset.date) { V.day = tr.dataset.date; view = 'day'; } draw(); });
    U.on(app, 'tr[data-enc]', 'click', function (tr) { location.href = V.encHref(tr.dataset.enc); });
    U.on(app, '[data-nav]', 'click', function (b) { var n = +b.dataset.nav; V.day = n === 0 ? D.today() : D.addDays(V.day, n); draw(); });
    U.on(app, '#goday', 'change', function (i) { if (i.value) { V.day = i.value; draw(); } });
    U.on(app, '[data-view]', 'click', function (b) { view = b.dataset.view; draw(); });
    U.on(app, '#week', 'click', function () { view = view === 'week' ? 'day' : 'week'; draw(); });
    U.on(app, '#newappt', 'click', function () { V.apptForm(null, function (a) { sel = a.id; V.day = a.date; draw(); }); });
    U.on(app, '#newappt2', 'click', function () { V.apptForm(null, function (a) { sel = a.id; V.day = a.date; draw(); }); });
    U.on(app, '#bookalthea', 'click', function () { U.askAlthea('Book ', 'e.g. Book Maria Rivera tomorrow at 2:30 for a diabetes follow-up'); });
    U.on(app, '#newpt', 'click', function () { V.patientForm(null, function (p) { V.apptForm(null, function (a) { sel = a.id; V.day = a.date; draw(); }, { mrn: p.mrn }); }); });
    U.on(app, '#newpt2', 'click', function () { V.patientForm(null, function (p) { V.apptForm(null, function (a) { sel = a.id; V.day = a.date; draw(); }, { mrn: p.mrn }); }); });
    U.on(app, '#start', 'click', function () { var a = A(); if (a) V.startVisit(a.mrn, a.id); else V.pickPatient('Start Virtual SOAP for', function (mrn) { V.startVisit(mrn); }); });
    U.on(app, '#faxmsg', 'click', function () { var a = A(); if (a) V.sendMessage(a.mrn); else V.pickPatient('Message which patient?', V.sendMessage); });
    U.on(app, '[data-act]', 'click', function (b) {
      var a = A(); if (!a) return; var act = b.dataset.act, p = D.pt(a.mrn);
      if (act === 'checkin') { a.visit = 'Checked in'; a.arrived = D.time(); D.log(p.name + ' checked in'); }
      else if (act === 'room') return U.ask('Room Patient', 'Room for ' + p.name, a.room || 'Exam 1', function (r) { a.room = r; a.visit = 'In room'; D.log(p.name + ' roomed, ' + r); D.save(); draw(); }, { ok: 'Room Patient' });
      else if (act === 'visit') return V.startVisit(a.mrn, a.id);
      else if (act === 'checkout') { a.visit = 'Checked out'; D.log(p.name + ' checked out'); }
      else if (act === 'noshow') { a.visit = 'No show'; D.log(p.name + ' no show'); }
      else if (act === 'cancel') return U.confirm('Cancel Appointment', '<p class="cf">Cancel ' + E(p.name) + '’s appointment at ' + E(a.time || '') + '?</p>', 'Cancel Appointment', function () { a.visit = 'Cancelled'; D.log('Appointment cancelled: ' + p.name); D.save(); draw(); }, { danger: 1, cancel: 'Keep' });
      else if (act === 'edit') return V.apptForm(a, function () { draw(); });
      else if (act === 'editpt') return V.patientForm(p, function () { draw(); });
      else if (act === 'elig') { if (!p.payer || p.payer === 'Self-pay' || !p.member) return U.toast('Add the payer and member ID first.'); p.eligChecked = D.today(); D.log('Eligibility marked checked: ' + p.name); U.toast('Eligibility marked checked.'); }
      else if (act === 'collect') return U.ask('Collect Payment', 'Amount received from ' + p.name + ' (balance ' + M(p.balance) + ')', p.balance.toFixed(2), function (v) { var amt = parseFloat(v); if (!(amt > 0)) return U.toast('Enter an amount above $0.'); p.balance = Math.max(0, +(p.balance - amt).toFixed(2)); D.log('Patient payment ' + M(amt) + ': ' + p.name); D.save(); draw(); }, { ok: 'Record Payment', type: 'number', step: '0.01' });
      D.save(); draw();
    });
  };

  /* ================= Patients ================= */
  V.pages.patients = function () {
    var Q = q.get('list') || 'all', filt = '', sel = {}, F = { payer: '', pcp: '', status: q.get('list') ? '' : 'Active' };
    var weekAgo = D.addDays(D.today(), -7), month = D.today().slice(0, 7);
    var lastVisit = function (p) { var e = S.encs.filter(function (x) { return x.mrn === p.mrn; }).map(function (x) { return x.date; }).sort().pop(); return e || ''; };
    var nextVisit = function (p) { return S.appts.filter(function (a) { return a.mrn === p.mrn && a.date >= D.today() && ['Scheduled', 'Checked in'].indexOf(a.visit) >= 0; }).map(function (a) { return a.date; }).sort()[0] || ''; };
    var L = {
      all: function (p) { return p.status !== 'Inactive'; }, week: function (p) { return lastVisit(p) >= weekAgo; }, today: function (p) { return S.appts.some(function (a) { return a.mrn === p.mrn && a.date === D.today() && a.visit !== 'Cancelled'; }); },
      new: function (p) { return (p.created || '').slice(0, 7) === month; }, balance: function (p) { return p.balance > 0; }, coverage: function (p) { return !p.payer || (p.payer !== 'Self-pay' && !p.member); },
      elig: function (p) { return p.payer && p.payer !== 'Self-pay' && !p.eligChecked; }, inactive: function (p) { return p.status === 'Inactive'; },
    };
    var f = function (p) { var st = V.ptStatus(p)[0], inact = p.status === 'Inactive'; return (L[Q] ? (Q === 'inactive' ? L[Q](p) : inact ? false : L[Q](p)) : p.pcp === Q.slice(2)) && (!filt || (p.name + p.mrn + D.us(p.dob) + (p.phone || '')).toLowerCase().indexOf(filt) >= 0)
      && (!F.payer || p.payer === F.payer) && (!F.pcp || p.pcp === F.pcp) && (!F.status || Q === 'inactive' || (F.status === 'Active' ? !inact : st === F.status)); };
    function draw() {
      var rows = S.patients.filter(f).sort(function (a, b) { return a.name.localeCompare(b.name); }), n = function (k) { return S.patients.filter(L[k]).length; };
      var nsel = Object.keys(sel).filter(function (k) { return sel[k]; }).length;
      app.innerHTML = U.header('patients') +
        U.toolbar('Patients', [['New Patient', 'user', { pri: 1, id: 'newpt' , menu: [['New Patient', 'newpt2', 'user'], ['Add With Althea', 'ptalthea', '<svg class="ico" width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="currentColor"/></svg>']] }], ['Import CSV', 'upload', { id: 'import' }], ['Run eligibility', 'refresh', { id: 'elig' }], '|', ['Export', 'download', { id: 'export' }], ['Print', 'print', { id: 'print' }]]) +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr)">' +
          U.pane('Patient lists', '', '', U.queue([
            { items: [{ key: 'all', label: 'All active', n: n('all'), color: 'blue' }, { key: 'week', label: 'Seen this week', n: n('week') }, { key: 'today', label: 'Scheduled today', n: n('today') }, { key: 'new', label: 'New this month', n: n('new') }] },
            { h: 'Needs attention', items: [{ key: 'balance', label: 'Balance due', n: n('balance'), color: 'amb' }, { key: 'coverage', label: 'Coverage issues', n: n('coverage'), color: 'red' }, { key: 'elig', label: 'Eligibility not checked', n: n('elig'), color: 'amb' }, { key: 'inactive', label: 'Inactive', n: n('inactive') }] },
            { h: 'By PCP', items: D.providers().map(function (s) { return { key: 'p:' + s.id, label: D.short(s), n: S.patients.filter(function (p) { return p.pcp === s.id; }).length, color: null }; }) },
          ], Q), { after: U.sum([['Active patients', n('all')], ['Outstanding A/R', M(D.sum(S.patients, function (p) { return p.balance; }))], ['Eligibility checked', n('all') ? Math.round(S.patients.filter(function (p) { return p.eligChecked; }).length / n('all') * 100) + '%' : '-']]) }) +
          '<section class="pane"><div class="ph">' + ({ all: 'All active patients' }[Q] || 'Filtered list') + ' <span class="sub">' + rows.length + '</span></div>' +
            '<div class="fl"><input class="chipf" id="pq" placeholder="Search name, MRN, or DOB" value="' + E(filt) + '">' + U.chip('Payer', 'payer', U.uniq(S.patients.map(function (p) { return p.payer; })), F.payer) + U.chip('PCP', 'pcp', D.providers().map(function (s) { return [s.id, D.short(s)]; }), F.pcp) + U.chip('Status', 'status', ['Active', 'New', 'Balance due', 'Coverage check', 'Self-pay'], F.status) + '<a class="end" href="#" id="clr">Clear filters</a></div>' +
            '<div class="pbody">' + U.grid(['Patient', 'MRN', 'DOB', 'Age', 'Sex', 'Primary ins.', 'PCP', 'Last visit', 'Next visit', 'Status', ['Balance', 'num']], rows.map(function (p) {
              var st = V.ptStatus(p); if (p.status === 'Inactive') st = ['Inactive', 'mut'];
              return { checked: sel[p.mrn], attrs: ' data-mrn="' + p.mrn + '"', cells: [V.link(p.name, V.chartHref(p.mrn)), '<span class="mono">' + p.mrn + '</span>', D.us(p.dob), D.age(p.dob), p.sex, E(p.payer || ''), E(D.provName(p.pcp)), D.us(lastVisit(p), 1), D.us(nextVisit(p), 1), '<span class="s-' + st[1] + '">' + st[0] + '</span>', M(p.balance)] };
            }), { cb: 1, selChecks: false }).replace('<tbody></tbody>', '<tbody>' + U.empty(12, S.patients.length ? 'No patients match.' : 'No patients yet. Use New patient or Import CSV.') + '</tbody>') +
            '</div><div class="pf"><span>' + nsel + ' selected' + (nsel ? ' · <a href="#" id="deact">Mark inactive</a> · <a href="#" id="react">Mark active</a>' : '') + '</span><span class="end">Showing ' + rows.length + ' of ' + S.patients.length + '</span></div></section></div>';
      U.fit(); U.search();
      var inp = document.getElementById('pq'); inp.oninput = function () { filt = inp.value.toLowerCase(); var pos = inp.selectionStart; draw(); var n2 = document.getElementById('pq'); n2.focus(); n2.setSelectionRange(pos, pos); };
    }
    V.redraw = draw; draw();
    U.on(app, '[data-q]', 'click', function (el) { Q = el.dataset.q; draw(); });
    U.on(app, 'tr[data-mrn] input[type=checkbox]', 'change', function (cb) { sel[cb.closest('tr').dataset.mrn] = cb.checked; draw(); });
    U.on(app, '[data-edit]', 'click', function (b) { V.patientForm(D.pt(b.dataset.edit)); });
    U.on(app, '#clr', 'click', function (a, e) { e.preventDefault(); Q = 'all'; filt = ''; F = { payer: '', pcp: '', status: 'Active' }; draw(); });
    U.on(app, '[data-f]', 'change', function (s) { F[s.dataset.f] = s.value; draw(); });
    U.on(app, 'tr[data-mrn]', 'dblclick', function (tr) { location.href = V.chartHref(tr.dataset.mrn); });
    U.on(app, '#newpt', 'click', function () { V.patientForm(); });
    U.on(app, '#newpt2', 'click', function () { V.patientForm(); });
    U.on(app, '#deact', 'click', function (a, e) { e.preventDefault(); Object.keys(sel).forEach(function (k) { if (sel[k]) D.pt(k).status = 'Inactive'; }); sel = {}; D.save(); draw(); });
    U.on(app, '#react', 'click', function (a, e) { e.preventDefault(); Object.keys(sel).forEach(function (k) { if (sel[k]) D.pt(k).status = 'Active'; }); sel = {}; D.save(); draw(); });
    U.on(app, '#elig', 'click', function () {
      var l = S.patients.filter(function (p) { return p.status !== 'Inactive' && p.payer && p.payer !== 'Self-pay'; }), ok = l.filter(function (p) { return p.member; });
      ok.forEach(function (p) { p.eligChecked = D.today(); }); D.log('Eligibility run: ' + ok.length + ' patients'); D.save(); draw();
      U.toast(ok.length + ' marked checked, ' + (l.length - ok.length) + ' missing a member ID.');
    });
    U.on(app, '#export', 'click', function () {
      U.download('patients-' + D.today() + '.csv', U.csv([['Name', 'MRN', 'DOB', 'Sex', 'Phone', 'Email', 'Address', 'Payer', 'Member ID', 'PCP', 'Balance', 'Status']].concat(S.patients.map(function (p) { return [p.name, p.mrn, p.dob, p.sex, p.phone, p.email, p.addr, p.payer, p.member, D.provName(p.pcp), p.balance, p.status || 'Active']; }))));
    });
    U.on(app, '#import', 'click', function () { V.importPatients(draw); });
  };

  // import patients from another clinic's or EMR's CSV export (EMR toolbars and Patients); matches common column names
  U.on(app, '#importcsv', 'click', function () { V.importPatients(V.refresh); });   // the EMR toolbars' Import CSV
  V.importPatients = function (after) {
      U.modal('Import patients from CSV', '<p style="margin:0 0 8px">The first row must be headers. Recognized columns: <span class="mono">name</span> (or <span class="mono">last</span> and <span class="mono">first</span>), <span class="mono">dob</span>, <span class="mono">sex</span>, <span class="mono">phone</span>, <span class="mono">email</span>, <span class="mono">address</span>, <span class="mono">payer</span>, <span class="mono">member</span>.</p><p class="mut" style="margin:0">Dates as YYYY-MM-DD or MM/DD/YYYY.</p>',
        [['Download template', '', function () { U.download('patients-template.csv', 'last,first,dob,sex,phone,email,address,payer,member\nGarcia,Daniel,1984-03-27,M,(512) 555-0101,,,BCBS TX,XQB812004\n'); return false; }], ['Cancel', '', function () {}], ['Choose file', 'pri', function () {
          U.pickFile('.csv,text/csv', false, function (files) {
            U.readFile(files[0], true).then(function (txt) {
              var rows = txt.split(/\r?\n/).filter(function (l) { return l.trim(); }).map(function (l) { var out = [], cur = '', qd = false; for (var i = 0; i < l.length; i++) { var ch = l[i]; if (ch === '"') { if (qd && l[i + 1] === '"') { cur += '"'; i++; } else qd = !qd; } else if (ch === ',' && !qd) { out.push(cur); cur = ''; } else cur += ch; } out.push(cur); return out.map(function (x) { return x.trim(); }); });
              var h = (rows.shift() || []).map(function (x) { return x.toLowerCase().replace(/[^a-z]/g, ''); }), col = function (r, names) { for (var i = 0; i < names.length; i++) { var k = h.indexOf(names[i]); if (k >= 0 && r[k]) return r[k]; } return ''; };
              var add = 0, skip = 0;
              rows.forEach(function (r) {
                var name = col(r, ['name', 'patient', 'patientname', 'fullname', 'patientfullname']) || (col(r, ['last', 'lastname', 'surname', 'patientlastname', 'familyname']) ? col(r, ['last', 'lastname', 'surname', 'patientlastname', 'familyname']) + ', ' + col(r, ['first', 'firstname', 'givenname', 'patientfirstname']) : '');
                var dob = col(r, ['dob', 'dateofbirth', 'birthdate', 'birthday', 'patientdob']).trim(), x;
                if ((x = dob.match(/^(\d{1,2})[\/-](\d{1,2})[\/-](\d{2}|\d{4})$/))) { var yy = x[3].length === 2 ? (+x[3] > +String(new Date().getFullYear()).slice(2) ? '19' : '20') + x[3] : x[3]; dob = yy + '-' + ('0' + x[1]).slice(-2) + '-' + ('0' + x[2]).slice(-2); }
                else if ((x = dob.match(/^(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})/))) dob = x[1] + '-' + ('0' + x[2]).slice(-2) + '-' + ('0' + x[3]).slice(-2);
                if (!name || !/^\d{4}-\d{2}-\d{2}$/.test(dob)) { skip++; return; }
                name = V.splitName(name);
                if (S.patients.some(function (p) { return p.name.toLowerCase() === name.toLowerCase() && p.dob === dob; })) { skip++; return; }
                var payer = col(r, ['payer', 'insurance', 'primaryinsurance', 'insurancename', 'primarypayer', 'carrier', 'plan']); var match = D.PAYERS.filter(function (pp) { return pp.toLowerCase().indexOf(payer.toLowerCase()) === 0 && payer; })[0];
                S.patients.push({ mrn: D.nextMrn(name), name: name, dob: dob, sex: (col(r, ['sex', 'gender', 'patientsex'])[0] || 'X').toUpperCase(), phone: col(r, ['phone', 'phonenumber']), email: col(r, ['email']), addr: col(r, ['address', 'addr']), payer: match || payer || 'Self-pay', member: col(r, ['member', 'memberid', 'insuranceid', 'subscriberid', 'policynumber', 'policyid', 'memberno', 'idnumber']), pcp: '', balance: 0, status: 'New', created: D.today() });
                add++;
              });
              D.log('Imported ' + add + ' patients from CSV'); D.save(); (after || V.refresh)(); U.toast(add + ' imported' + (skip ? ', ' + skip + ' skipped (missing name or DOB, or already on file)' : '') + '.');
            });
          });
        }]]);
  };

  /* ================= Patient chart ================= */
  V.pages.chart = function () {
    var mrn = q.get('mrn');
    if (!D.pt(mrn)) {
      app.innerHTML = U.header('chart') + U.toolbar('Patient chart', [['New Patient', 'user', { pri: 1, id: 'newpt' , menu: [['New Patient', 'newpt2', 'user'], ['Add With Althea', 'ptalthea', '<svg class="ico" width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="currentColor"/></svg>']] }]], '') + '<div class="main" style="grid-template-columns:minmax(0,1fr)">' + U.pane('Choose a patient', '', '', '<div class="pick"><input id="pk" placeholder="Search name, MRN or DOB" autofocus><div id="pkl"></div></div>') + '</div>';
      U.fit(); U.search();
      var dr = function () { var s = (document.getElementById('pk').value || '').toLowerCase(); document.getElementById('pkl').innerHTML = U.grid(['Patient', 'MRN', 'DOB', 'Payer'], S.patients.filter(function (p) { return (p.name + p.mrn + p.dob).toLowerCase().indexOf(s) >= 0; }).map(function (p) { return { attrs: ' data-href="' + V.chartHref(p.mrn) + '"', cells: [E(p.name), p.mrn, D.us(p.dob), E(p.payer || '')] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(4, S.patients.length ? 'No match' : 'No patients yet.') + '</tbody>'); };
      dr(); document.getElementById('pk').oninput = dr;
      U.on(app, 'tr[data-href]', 'click', function (tr) { location.href = tr.dataset.href; });
      U.on(app, '#newpt', 'click', function () { V.patientForm(null, function (p) { location.href = V.chartHref(p.mrn); }); });
      U.on(app, '#newpt2', 'click', function () { V.patientForm(null, function (p) { location.href = V.chartHref(p.mrn); }); });
      return;
    }
    if (V.seen) V.seen('patient', mrn);
    function draw() {
      var p = D.pt(mrn), encs = S.encs.filter(function (e) { return e.mrn === mrn; }).sort(function (a, b) { return b.date.localeCompare(a.date); });
      var claims = S.claims.filter(function (c) { return c.mrn === mrn; }), orders = [], vit = encs.filter(function (e) { return e.vitals && (e.vitals.bp || e.vitals.hr || e.vitals.wt); });
      encs.forEach(function (e) { (e.orders || []).forEach(function (o) { orders.push([e, o]); }); });
      var docs = S.docs.filter(function (d) { return d.mrn === mrn; }), up = S.appts.filter(function (a) { return a.mrn === mrn && a.date >= D.today() && ['Scheduled', 'Checked in'].indexOf(a.visit) >= 0; }).sort(function (a, b) { return (a.date + a.time).localeCompare(b.date + b.time); });
      var sec = function (id, title, sub, cols, rows, empty, end) { return '<div id="' + id + '">' + U.sect(title, sub, U.grid(cols, rows, { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(cols.length, empty) + '</tbody>'), end) + '</div>'; };
      app.innerHTML = U.header('chart') + V.banner(mrn, '<button class="btn" id="msg">Message</button><button class="btn" id="switch">Switch patient</button><button class="btn pri" id="visit" style="display:inline-flex;align-items:center;gap:5px">' + I.mic + 'Start Visit</button>') +
        U.toolbar('Patient chart', [['New note', 'pencil', { id: 'note' }], ['Orders', 'clip', { id: 'orders' }], ['Upload document', 'upload', { id: 'upload' }], '|', ['Print', 'print', { id: 'print' }], ['Import CSV', 'upload', { id: 'importcsv' }], ['Export record', 'download', { id: 'export' }]]) +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr) 330px">' +
          U.pane('Chart', '', '', U.queue([
            { h: 'Patient record', items: [{ key: 'notes', label: 'Clinical notes', n: encs.length, color: 'blue' }, { key: 'demo', label: 'Demographics', color: null }, { key: 'ins', label: 'Insurance', n: p.payer ? 1 : 0, color: null }] },
            { h: 'Clinical', items: [{ key: 'orders', label: 'Orders', n: orders.length, color: null }, { key: 'meds', label: 'Medications', n: V.lines(p.meds).length, color: null }, { key: 'vitals', label: 'Vitals', n: vit.length, color: null }, { key: 'problems', label: 'Problems', n: V.lines(p.problems).length, color: null }, { key: 'allergies', label: 'Allergies', n: V.lines(p.allergies).length, color: null }] },
            { h: 'Billing', items: [{ key: 'claims', label: 'Claims', n: claims.length, color: null }] },
            { h: 'Other', items: [{ key: 'docs', label: 'Documents', n: docs.length, color: null }, { key: 'msgs', label: 'Messages', n: S.messages.filter(function (m) { return m.mrn === mrn; }).length, color: null }] },
          ], 'notes')) +
          '<section class="pane"><div class="pbody">' +
            sec('notes', 'Clinical notes', encs.length, ['Date', 'Author', 'Type', 'Summary', 'Status', 'CPT'], encs.map(function (e) { return { attrs: ' data-href="' + V.encHref(e.id) + '"', cells: [D.us(e.date), E(D.provName(e.prov)), E(e.visitType), E((e.a || e.reason || e.s || '').split('\n')[0].slice(0, 60)), '<span class="' + (e.status === 'Signed' ? 's-grn' : 's-amb') + '">' + e.status + '</span>', e.codes.filter(function (c) { return c.state === 'acc' && c.type === 'CPT'; }).map(function (c) { return c.code; }).join(', ')] }; }), 'No visits yet. Start one with Start Visit.', '<a href="#" id="note2">New note</a>') +
            sec('claims', 'Claims', claims.length + ', ' + M(D.sum(claims, function (c) { return c.paid || 0; })) + ' paid', ['Claim #', 'DOS', 'CPT', 'Dx', 'Payer', 'Status', ['Charges', 'num'], ['Paid', 'num']], claims.map(function (c) { return { attrs: ' data-href="' + V.claimHref(c.id) + '"', cells: [V.link(c.id, V.claimHref(c.id)), D.us(c.dos, 1), c.lines.map(function (l) { return l.code; }).join(', '), c.dx.join(', '), E(D.payer(c)), V.claimStatus(c), M(D.charge(c)), M(c.paid || 0)] }; }), 'No claims yet.') +
            sec('orders', 'Orders', orders.length, ['Date', 'Type', 'Order', 'Detail', 'Dx', 'Status'], orders.map(function (x) { return { attrs: ' data-href="' + V.encHref(x[0].id) + '"', cells: [D.us(x[0].date, 1), E(x[1].type), E(x[1].name), E(x[1].detail || ''), E(x[1].dx || ''), '<span class="' + (x[1].status === 'Signed' ? 's-grn' : 's-amb') + '">' + x[1].status + '</span>'] }; }), 'No orders yet.') +
            sec('meds', 'Medications', V.lines(p.meds).length + ' active', ['Medication'], V.lines(p.meds).map(function (m) { return { cells: [E(m)] }; }), 'None on file.', '<a href="#" id="editmeds">Edit</a>') +
            sec('vitals', 'Vitals trend', '', ['Date', 'BP', 'HR', 'Temp', 'Weight'], vit.map(function (e) { var v = e.vitals; var hi = /(\d+)\/(\d+)/.exec(v.bp || ''); return { cells: [D.us(e.date, 1), '<span class="' + (hi && (+hi[1] >= 140 || +hi[2] >= 90) ? 's-amb' : '') + '">' + E(v.bp || '') + '</span>', E(v.hr || ''), E(v.temp || ''), E(v.wt || '')] }; }), 'No vitals recorded.') +
          '</div></section>' +
          U.pane('Summary', '', '<a href="#" id="editpt">Edit</a>', '<div class="dp-sec" id="demo"><div class="dp-h">Demographics</div>' + U.kv([['Phone', E(p.phone || '-')], ['Email', E(p.email || '-')], ['Address', E(p.addr || '-')], ['Created', D.us(p.created)]]) + '</div>' +
            '<div class="dp-sec" id="ins"><div class="dp-h">Insurance</div>' + U.kv([['Primary', E(p.payer || 'None')], ['Member ID', p.member ? '<span class="mono">' + E(p.member) + '</span>' : U.st('Missing', 'amb')], ['Eligibility', p.eligChecked ? 'Checked ' + D.us(p.eligChecked) : U.st('Not checked', 'amb')], ['Balance', M(p.balance)]]) + '</div>' +
            '<div class="dp-sec" id="problems"><div class="dp-h">Problems</div><div class="lst">' + (V.lines(p.problems).map(function (x) { return '<div>' + E(x) + '</div>'; }).join('') || '<span class="mut">None on file</span>') + '</div></div>' +
            '<div class="dp-sec" id="allergies"><div class="dp-h">Allergies</div><div class="s-red">' + (V.lines(p.allergies).map(E).join('<br>') || '<span class="mut">None recorded</span>') + '</div></div>' +
            '<div class="dp-sec"><div class="dp-h">Care team</div>' + U.kv([['PCP', E(D.provName(p.pcp) || 'None')]]) + '</div>' +
            '<div class="dp-sec"><div class="dp-h">Upcoming</div>' + (up.length ? U.kv(up.map(function (a) { return [D.us(a.date, 1) + ' ' + V.fmtT(a.time), E(a.reason || 'Visit') + ', ' + E(D.provName(a.prov))]; })) : '<span class="mut">Nothing booked</span> · <a href="#" id="book">Book</a>') + '</div>' +
            '<div class="dp-sec" id="docs"><div class="dp-h">Documents <span class="end">' + V.link('Open', '/v4/emr/documents?mrn=' + mrn) + '</span></div>' + (docs.slice(0, 5).map(function (d) { return '<div>' + E(d.name) + ' <span class="mut">' + D.us(d.rec, 1) + '</span></div>'; }).join('') || '<span class="mut">None</span>') + '</div>' +
            '<div class="dp-sec"><div class="dp-h">Scanned At Registration</div><div id="pt-scans" class="lst"><span class="mut">Loading…</span></div></div>' +
            '<div class="dp-sec" id="msgs"><div class="dp-h">Messages</div>' + (S.messages.filter(function (m) { return m.mrn === mrn; }).slice(0, 4).map(function (m) { return '<div><span class="mut">' + D.us(m.at.slice(0, 10), 1) + ' ' + m.via + ':</span> ' + E(m.text) + '</div>'; }).join('') || '<span class="mut">None</span>') + '</div>') +
        '</div>';
      U.fit(); U.search(); if (V.patientScans) V.patientScans(mrn, document.getElementById('pt-scans'));
    }
    V.redraw = draw; draw();
    U.on(app, 'tr[data-href]', 'click', function (tr, e) { if (!e.target.closest('a')) location.href = tr.dataset.href; });
    U.on(app, '[data-q]', 'click', function (el) { app.querySelectorAll('.ql-i').forEach(function (x) { x.classList.toggle('on', x === el); }); var t = document.getElementById(el.dataset.q); if (t) t.scrollIntoView({ block: 'start' }); });
    U.on(app, '#visit', 'click', function () { V.startVisit(mrn); });
    U.on(app, '#note', 'click', function () { V.startVisit(mrn); }); U.on(app, '#note2', 'click', function (a, e) { e.preventDefault(); V.startVisit(mrn); });
    U.on(app, '#orders', 'click', function () { var e = S.encs.filter(function (x) { return x.mrn === mrn && x.status === 'Draft'; })[0]; if (e) location.href = V.encHref(e.id) + '&tab=orders'; else { U.toast('Orders are placed from a visit. Starting one.'); V.startVisit(mrn); } });
    U.on(app, '#msg', 'click', function () { V.sendMessage(mrn); });
    U.on(app, '#switch', 'click', function () { V.pickPatient('Switch patient', function (m) { location.href = V.chartHref(m); }); });
    U.on(app, '#editpt', 'click', function (a, e) { e.preventDefault(); V.patientForm(D.pt(mrn)); });
    U.on(app, '#editmeds', 'click', function (a, e) { e.preventDefault(); V.patientForm(D.pt(mrn)); });
    U.on(app, '#book', 'click', function (a, e) { e.preventDefault(); V.apptForm(null, null, { mrn: mrn }); });
    U.on(app, '#upload', 'click', function () { V.upload(mrn); });
    U.on(app, '#export', 'click', function () {
      var p = D.pt(mrn); U.download(p.name.replace(/\W+/g, '-') + '-' + mrn + '.json', JSON.stringify({ patient: p, visits: S.encs.filter(function (e) { return e.mrn === mrn; }), claims: S.claims.filter(function (c) { return c.mrn === mrn; }), appointments: S.appts.filter(function (a) { return a.mrn === mrn; }), documents: S.docs.filter(function (d) { return d.mrn === mrn; }).map(function (d) { var x = Object.assign({}, d); delete x.data; return x; }), messages: S.messages.filter(function (m) { return m.mrn === mrn; }) }, null, 2), 'application/json');
      D.log('Record exported: ' + p.name);
    });
  };
  V.claimStatus = function (c) {
    if (c.stage === 'edits') return U.st('Edit: ' + ((c.edits[0] || {}).label || ''), (c.edits[0] || {}).lvl || 'amb');
    if (c.stage === 'ready') return '<span class="s-blue">Ready To Submit</span>';
    if (c.stage === 'submitted') return E(c.status || 'Submitted');
    if (c.stage === 'paid') return '<span class="s-grn">Paid ' + D.us(c.paidOn, 1).slice(0, 5) + '</span>';
    if (c.stage === 'denied') return U.st('Denied ' + c.carc, 'red');
    return E(c.stage);
  };

  /* ================= Documents ================= */
  V.upload = function (mrn, after) {
    U.pickFile('', true, function (files) {
      var big = 0, done = 0;
      files.forEach(function (f) {
        var rec = { id: D.id('DOC'), mrn: mrn || '', name: f.name.replace(/\.[^.]+$/, ''), file: f.name, type: /card|insur/i.test(f.name) ? 'Insurance' : /lab/i.test(f.name) ? 'Lab' : /consent|hipaa/i.test(f.name) ? 'Consent' : 'Other', src: 'Upload', rec: D.today(), size: U.size(f.size), mime: f.type, status: 'Unfiled' };
        var fin = function () { S.docs.unshift(rec); done++; if (done === files.length) { D.log('Uploaded ' + files.length + ' document' + (files.length === 1 ? '' : 's')); D.save(); U.toast(files.length + ' uploaded' + (big ? ', ' + big + ' too large to preview here (over 2 MB): details saved only.' : '.')); if (after) after(rec); else V.refresh(); } };
        if (f.size > 2 * 1048576) { big++; fin(); } else U.readFile(f).then(function (data) { rec.data = data; fin(); });
      });
    });
  };
  V.pages.documents = function () {
    var mrn = D.pt(q.get('mrn')) ? q.get('mrn') : '', Q = 'All', sel = '';
    function draw() {
      var all = S.docs.filter(function (d) { return !mrn || d.mrn === mrn; }), types = []; all.forEach(function (d) { if (types.indexOf(d.type) < 0) types.push(d.type); });
      var rows = all.filter(function (d) { return Q === 'All' || d.type === Q || (Q === 'await' && d.status === 'Awaiting signature') || (Q === 'unfiled' && d.status === 'Unfiled'); });
      if (!rows.some(function (d) { return d.id === sel; })) sel = rows[0] ? rows[0].id : '';
      var d = all.filter(function (x) { return x.id === sel; })[0], reqs = S.requests.filter(function (r) { return !mrn || r.mrn === mrn; }), outs = S.outgoing.filter(function (o) { return !mrn || o.mrn === mrn; });
      var prev = !d ? '<div class="blank">' + (all.length ? 'Select a document.' : 'No documents yet. Use Upload.') + '</div>' : !d.data ? '<div class="fax"><div class="fax-h"><b>' + E(d.name) + '</b><span>' + E(d.src) + ' ' + D.us(d.rec) + '</span></div><p class="mut">No preview stored for this file (' + E(d.size) + ').</p></div>'
        : /^image\//.test(d.mime) ? '<div class="fax" style="text-align:center"><img src="' + d.data + '" alt="' + E(d.name) + '" style="max-width:100%"></div>'
        : /pdf/.test(d.mime) ? '<iframe title="Preview" src="' + d.data + '" style="width:100%;height:100%;border:0;background:#fff"></iframe>'
        : /^text\//.test(d.mime) ? '<div class="fax"><pre style="white-space:pre-wrap;margin:0;font:12px var(--mono)">' + E(atob(d.data.split(',')[1] || '')) + '</pre></div>' : '<div class="fax"><p>' + E(d.file || d.name) + '</p><p class="mut">This file type can’t be previewed. Use Download.</p></div>';
      app.innerHTML = U.header('documents') + (mrn ? V.banner(mrn, '<button class="btn" id="switch">Switch patient</button><a class="btn" style="display:inline-flex;align-items:center" href="/v4/emr/documents">All patients</a>') : '') +
        U.toolbar('Documents', [['Upload', 'upload', { pri: 1, id: 'upload' }], ['Request from patient', 'mail', { id: 'request' }], ['Send fax', 'fax', { id: 'fax' }], '|', ['Print', 'print', { id: 'printdoc' }], ['Import CSV', 'upload', { id: 'importcsv' }], ['Download', 'download', { id: 'dl' }]]) +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr) 420px">' +
          U.pane('Document types', '', '', U.queue([{ items: [{ key: 'All', label: 'All', n: all.length, color: 'blue' }].concat(types.map(function (t) { return { key: t, label: t, n: all.filter(function (x) { return x.type === t; }).length, color: null }; })) },
            { h: 'Status', items: [{ key: 'await', label: 'Awaiting signature', n: all.filter(function (x) { return x.status === 'Awaiting signature'; }).length, color: 'amb' }, { key: 'unfiled', label: 'Unfiled', n: all.filter(function (x) { return x.status === 'Unfiled'; }).length, color: 'gray' }] }], Q)) +
          '<section class="pane"><div class="ph">' + (mrn ? 'Documents' : 'All documents') + ' <span class="sub">' + rows.length + '</span></div><div class="pbody">' +
            U.grid(['Name', 'Patient', 'Type', 'Source', 'Received', 'Size', 'Status'], rows.map(function (x) { return { sel: x.id === sel, attrs: ' data-doc="' + x.id + '"', cells: [E(x.name), E(x.mrn ? D.ptName(x.mrn) : 'Clinic'), E(x.type), E(x.src), D.us(x.rec), x.size, '<span class="s-' + (x.status === 'Unfiled' ? 'mut' : x.status === 'Awaiting signature' ? 'amb' : 'grn') + '">' + E(x.status) + '</span>'] }; }), { cb: 1, click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(7, 'No documents.') + '</tbody>') +
            U.sect('Requests to patient', 'sent through the portal', U.grid(['Form', 'Patient', 'Sent', 'Status', ''], reqs.map(function (r) { return { cells: [E(r.form), E(D.ptName(r.mrn)), D.us(r.sent), '<span class="s-' + (r.status === 'Received' ? 'grn' : 'amb') + '">' + E(r.status) + '</span>', r.status === 'Received' ? '' : '<button class="mini" data-recv="' + r.id + '">Mark received</button>'] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'None sent.') + '</tbody>')) +
            U.sect('Outgoing', 'faxes and messages', U.grid(['Document', 'To', 'Method', 'Sent', 'Status'], outs.map(function (o) { return { cells: [E(o.doc), E(o.to), E(o.method), D.us(o.sent), '<span class="s-grn">' + E(o.status) + '</span>'] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'Nothing sent.') + '</tbody>')) +
          '</div><div class="pf"><span>' + (d ? 1 : 0) + ' selected</span><span class="end">Showing ' + rows.length + ' of ' + all.length + '</span></div></section>' +
          '<section class="pane"><div class="ph">Preview <span class="sub">' + E(d ? d.name : '') + '</span></div><div class="pbody" style="background:var(--bg)">' + prev + '</div>' +
            (d ? '<div class="dp-sec" style="border-top:1px solid var(--b)">' + U.fields([{ k: 'name', label: 'Name', span: 2 }, { k: 'type', label: 'Type', type: 'select', options: ['Insurance', 'Intake', 'Consent', 'Outside records', 'EOB', 'Lab', 'Imaging', 'Other'] }, { k: 'status', label: 'Status', type: 'select', options: ['Unfiled', 'Filed to chart', 'Awaiting signature', 'Verified', 'Complete', 'Posted'] },
              { k: 'mrn', label: 'Patient', type: 'select', options: V.ptOptions(), blank: 'Clinic document', span: 4 }], d) + '</div><div class="dp-f"><button class="btn" id="deldoc">Delete</button><button class="btn pri" id="savedoc">Save</button></div>' : '') + '</section></div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    var cur = function () { return S.docs.filter(function (x) { return x.id === sel; })[0]; };
    U.on(app, '[data-q]', 'click', function (el) { Q = el.dataset.q; draw(); });
    U.on(app, 'tr[data-doc]', 'click', function (tr) { sel = tr.dataset.doc; draw(); });
    U.on(app, '#switch', 'click', function () { V.pickPatient('Switch patient', function (m) { location.href = '/v4/emr/documents?mrn=' + m; }); });
    U.on(app, '#upload', 'click', function () { V.upload(mrn, function (r) { sel = r.id; draw(); }); });
    U.on(app, '#savedoc', 'click', function () { var d = cur(); Object.assign(d, U.values(app.querySelector('.main > section:last-child .dp-sec'))); D.log('Document updated: ' + d.name); D.save(); draw(); U.toast('Saved.'); });
    U.on(app, '#deldoc', 'click', function () { var d = cur(); U.confirm('Delete Document', '<p class="cf">Delete ' + E(d.name) + '? This can’t be undone.</p>', 'Delete', function () { S.docs = S.docs.filter(function (x) { return x !== d; }); D.S.docs = S.docs; D.log('Document deleted: ' + d.name); D.save(); draw(); }, { danger: 1 }); });
    U.on(app, '#dl', 'click', function () { var d = cur(); if (!d) return U.toast('Select a document.'); if (!d.data) return U.toast('Only the details of this file were saved, so it can’t be downloaded here.'); var a = document.createElement('a'); a.href = d.data; a.download = d.file || d.name; a.click(); });
    U.on(app, '#printdoc', 'click', function () { var d = cur(); if (!d || !d.data) return window.print(); var w = window.open(''); w.document.write(/^image\//.test(d.mime) ? '<img src="' + d.data + '" style="max-width:100%">' : '<iframe src="' + d.data + '" style="width:100%;height:100vh;border:0"></iframe>'); setTimeout(function () { w.print(); }, 400); });
    U.on(app, '[data-recv]', 'click', function (b) { var r = S.requests.filter(function (x) { return x.id === b.dataset.recv; })[0]; r.status = 'Received'; D.save(); draw(); });
    U.on(app, '#request', 'click', function () {
      U.form('Request from patient', [{ k: 'mrn', label: 'Patient', type: 'select', options: V.ptOptions(), req: 1, span: 2, def: mrn }, { k: 'form', label: 'Form', type: 'select', options: ['HIPAA acknowledgment', 'Intake questionnaire', 'Insurance card, front and back', 'Updated photo ID', 'Consent to treat', 'Records release'], span: 2 }], {}, function (v) {
        S.requests.unshift({ id: D.id('R'), mrn: v.mrn, form: v.form, sent: D.today(), status: 'Sent' }); D.log('Requested ' + v.form + ' from ' + D.ptName(v.mrn)); D.save(); draw(); U.toast('Request recorded. (This preview does not send portal messages.)');
      }, { save: 'Send request' });
    });
    U.on(app, '#fax', 'click', function () {
      var docs = S.docs.filter(function (x) { return !mrn || x.mrn === mrn; });
      U.form('Send fax', [{ k: 'doc', label: 'Document', type: 'select', options: docs.map(function (x) { return [x.id, x.name]; }), req: 1, span: 4, def: sel }, { k: 'to', label: 'To (practice or person)', req: 1, span: 2 }, { k: 'num', label: 'Fax number', req: 1, span: 2, ph: '(512) 555-0101' }], {}, function (v) {
        var d = S.docs.filter(function (x) { return x.id === v.doc; })[0]; S.outgoing.unshift({ id: D.id('O'), mrn: d ? d.mrn : mrn, doc: d ? d.name : '', to: v.to + ' ' + v.num, method: 'Fax', sent: D.today(), status: 'Queued' });
        D.log('Fax queued to ' + v.to); D.save(); draw(); U.toast('Fax queued. (No fax line is connected in this preview.)');
      }, { save: 'Send fax' });
    });
  };
})();
