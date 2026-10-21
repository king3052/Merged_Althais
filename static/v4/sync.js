/* Brings the clinic's existing records into the new EMR, and keeps patients and claims on the server.
   - Every load: the old EMR's patients, claims, appointments and visit notes in this browser (its browser storage).
   - Every load: the server's patients (/api/patients) and claims (/api/claims), adding any this browser doesn't have.
   - After every change here: patients and claims are written back to the server (in the shape the old EMR reads too),
     so other logins see them. Appointments and visit notes stay in this browser, as they did in the old EMR.
   Nothing already here is overwritten by an import; records are matched by MRN and claim number. */
(function () {
  var S = D.S, me = window.__ALTHAIS_USER__ || {};
  var NS = 'org_' + String(me.organization || me.email || 'unknown').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') + '::';
  var OLD = { patients: NS + 'althais.patients.v1', claims: NS + 'althais.claims.v1', appts: NS + 'althais.appointments.v1', notes: NS + 'althais.notes.v1' };
  var read = function (k) { try { var v = JSON.parse(localStorage.getItem(k)); return Array.isArray(v) ? v : []; } catch (e) { return []; } };
  var has = function (p) { return (me.products || []).indexOf('suite') >= 0 || (me.products || []).indexOf(p) >= 0; };

  // ---- shape helpers ----
  function iso(d) {
    d = String(d || '').trim(); if (!d) return '';
    var m = d.match(/^(\d{4})-(\d{2})-(\d{2})/); if (m) return m[1] + '-' + m[2] + '-' + m[3];
    m = d.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/); if (m) { var y = m[3].length === 2 ? (+m[3] > 30 ? '19' : '20') + m[3] : m[3]; return y + '-' + ('0' + m[1]).slice(-2) + '-' + ('0' + m[2]).slice(-2); }
    var t = new Date(d); return isNaN(t) ? '' : D.iso(t);
  }
  function personName(n) {   // "Dr. R. Patel" -> "Patel, R." ; "Test, D" stays
    n = String(n || '').replace(/^dr\.?\s+/i, '').replace(/,?\s+(md|do|pa-c|pa|np|fnp-c|fnp|rn|dds|dmd)\.?$/i, '').trim(); if (!n) return '';
    if (/,/.test(n)) return n.replace(/\s*,\s*/, ', ');
    var p = n.split(/\s+/); return p.length > 1 ? p[p.length - 1] + ', ' + p.slice(0, -1).join(' ') : n;
  }
  function staffFor(name) {   // find or create the provider by name; returns the staff id
    var n = personName(name); if (!n) return '';
    var key = function (x) { return x.toLowerCase().replace(/[^a-z]/g, ''); }, last = key(n.split(',')[0]), init = key((n.split(',')[1] || '').trim().slice(0, 1));
    var hit = S.staff.filter(function (s) { var sl = key(s.name.split(',')[0]), si = key((s.name.split(',')[1] || '').trim().slice(0, 1)); return sl === last && (!init || !si || si === init); })[0];
    if (hit) return hit.id;
    var s = { id: D.id('S'), name: n, cred: '', spec: '', loc: '', access: 'Provider', provider: true, status: 'Active', hired: '', creds: [], reminders: [], training: [], imported: true };
    S.staff.push(s); return s.id;
  }
  var lines = function (x) { return Array.isArray(x) ? x.map(function (y) { return typeof y === 'string' ? y : [y.code, y.label || y.name, y.dose].filter(Boolean).join(' '); }).join('\n') : String(x || ''); };
  function patientFromOld(o) {
    var meta = String(o.insPrimaryMeta || o.member || '');
    return { mrn: o.mrn, name: personName(o.name) || o.name, dob: iso(o.dob), sex: (o.sex || 'X').slice(0, 1).toUpperCase(), phone: o.addrPhone || o.phone || '', email: o.email || '',
      addr: [o.addrStreet, o.addrCity, o.addrState, o.addrZip].filter(Boolean).join(', ') || o.addr || '', payer: o.insPrimary || o.payer || '', member: /^[—-]$/.test(meta) || /^eligible$/i.test(meta) ? '' : meta.replace(/^Mbr\s*/i, ''),
      pcp: o.pcp ? staffFor(o.pcp) : o.provider ? staffFor(o.provider) : '', allergies: lines(o.allergies).replace(/\n/g, '; '), problems: lines(o.problems).replace(/\n/g, '; '),
      meds: lines((o.meds || []).filter ? (o.meds || []).filter(function (m) { return !m.status || /active/i.test(m.status); }) : o.meds), balance: Number(o.balance) || 0, status: 'Active', created: iso(o.created || o.createdAt) || D.today(), imported: true };
  }
  var STAGE = function (st) { st = String(st || '').toLowerCase(); return /paid/.test(st) ? 'paid' : /denied|appeal/.test(st) ? 'denied' : /submitted|accepted|sent/.test(st) ? 'submitted' : /ready|queued|approved/.test(st) ? 'ready' : 'edits'; };
  function ensurePatient(mrn, name, payer) {
    if (!mrn) mrn = D.nextMrn(personName(name));
    if (!D.pt(mrn)) S.patients.push({ mrn: mrn, name: personName(name) || mrn, dob: '', sex: 'X', payer: payer || '', member: '', pcp: '', balance: 0, status: 'Active', created: D.today(), imported: true });
    return mrn;
  }
  // the service lines and diagnoses on an old claim, wherever that EMR kept them: the saved snapshot, the code list
  // (typed or not: 5 digits / letter + 4 digits = procedure, letter + 2 digits (+ .xxx) = diagnosis), or plain text fields
  function codesFrom(o) {
    var snap = o.snap || {}, codes = Array.isArray(o.codes) ? o.codes : [], all = [].concat(snap.cpts || [], snap.icds || [], codes);
    var isDx = function (c) { var k = String(c.code || '').toUpperCase().trim(); return /icd|diag|dx/i.test(c.type || '') || (!/cpt|hcpcs|proc/i.test(c.type || '') && /^[A-TV-Z][0-9][0-9A-Z](\.?[0-9A-Z]{1,4})?$/.test(k) && !/^[A-Z]\d{4}$/.test(k)); };
    var cpts = [], dx = [], seen = {};
    all.forEach(function (c) { if (!c || !c.code) return; var k = String(c.code).toUpperCase().trim(), key = k + '|' + (c.tooth || '') + '|' + (c.surface || '') + '|' + (c.quad || ''); if (seen[key]) return; seen[key] = 1;
      if (isDx(c)) dx.push(k); else cpts.push(D.lineOf({ code: k, mod: c.modifier || c.mod || '', units: +c.units || 1, tooth: c.tooth, surface: c.surface, quad: c.quad })); });   // dental: each procedure keeps its tooth
    if (!cpts.length && o.cpt) cpts = String(o.cpt).split(/[\s,;]+/).filter(Boolean).map(function (c) { return { code: c.toUpperCase(), mod: '', units: 1 }; });
    if (!dx.length && (o.icd || o.dx || o.diagnosis)) dx = String(o.icd || o.dx || o.diagnosis).split(/[\s,;]+/).filter(function (x) { return /^[A-Z]\d/i.test(x); }).map(function (c) { return c.toUpperCase(); });
    return { cpts: cpts, dx: dx };
  }
  function claimFrom(o) {
    var snap = o.snap || {}, codes = o.codes || [];
    var cd = codesFrom(o), cpts = cd.cpts, dx = cd.dx;
    var stage = STAGE(o.status), dos = iso(snap.svcDate || o.dos || o.ts || o.createdAt) || D.today();
    var st = snap.structured || {}, sv = st.vitals || {};
    var noteTxt = o.note || snap.note || (snap.structured ? ['CHIEF COMPLAINT: ' + (st.cc || ''), 'HPI: ' + (st.hpi || ''), 'ROS: ' + (st.ros || ''), 'VITALS: ' + [sv.bp && 'BP ' + sv.bp, sv.hr && 'HR ' + sv.hr].filter(Boolean).join(', '), 'EXAM: ' + (st.exam || ''), 'LABS: ' + (st.labs || ''), 'ASSESSMENT: ' + (st.assessment || ''), 'PLAN: ' + (st.plan || '')].join('\n') : '');
    var c = { id: o.claimId || o.id, encId: '', note: noteTxt, mrn: ensurePatient(o.mrn, o.patient || o.patientName, o.payer), dos: dos, prov: staffFor(o.provider || snap.provider), payer: o.payer || '', lines: cpts, dx: dx,
      stage: stage, resolved: {}, amount: Number(o.amount) || 0, created: o.ts || o.createdAt || '', imported: true,
      hist: [{ at: new Date().toISOString(), text: o.source === 'extension' ? 'Created with the Althais browser extension in the EMR (' + (o.status || 'Draft') + ')' : 'Brought over from the previous EMR (status: ' + (o.status || 'Draft') + ')' }] };
    if (stage === 'submitted' || stage === 'paid' || stage === 'denied') { c.submittedOn = dos; c.status = o.status; }
    if (stage === 'paid') { c.paid = Number(o.paid || o.amount) || 0; c.paidOn = iso(o.paidOn) || dos; }
    if (stage === 'denied') { c.carc = o.carc || 'CO-16'; c.reason = o.denialReason || (o.flags || [])[0] || 'Denied (from previous EMR)'; c.dstatus = /appeal/i.test(o.status) ? 'Appealed' : 'Needs review'; c.appealBy = D.addDays(dos, 90); c.everDenied = true; c.letter = o.appealLetter || ''; }
    if (stage === 'edits' || stage === 'ready') D.scrub(c);
    if (/queued/i.test(o.status || '')) {     // approved in the EMR with the extension: it waits for Althais's end-of-day submission, which checks it again
      c.stage = 'ready'; c.queuedAt = o.createdAt || new Date().toISOString(); c.submitAt = window.V && V.nextBatch ? V.nextBatch() : '';
      if (window.V && V.checkKey) c.queueKey = V.checkKey(c);
    }
    return c;
  }
  function encFromOld(n) {
    var s = n.structured || {}, v = s.vitals || {};
    return { id: 'OLD-' + n.id, mrn: n.mrn, apptId: '', date: iso(n.date) || D.today(), prov: staffFor(n.provider), visitType: n.type || 'Office visit', reason: s.cc || n.desc || '', status: /signed/i.test(n.status || '') ? 'Signed' : 'Draft',
      s: [s.cc && 'Chief complaint: ' + s.cc, s.hpi, s.ros && 'ROS: ' + s.ros].filter(Boolean).join('\n') || (s.cc ? '' : n.desc || ''), o: [s.exam, s.labs && 'Results: ' + s.labs].filter(Boolean).join('\n'), a: s.assessment || '', p: s.plan || '',
      vitals: { bp: v.bp || '', hr: v.hr || '', temp: v.temp || '', wt: v.weight || '', spo2: v.spo2 || '' }, transcript: [], orders: [], addenda: [], ignored: {},
      codes: String(n.cpt || '').split(/[\s,]+/).filter(Boolean).map(function (c) { return { code: c, desc: c, type: 'CPT', conf: 0, state: 'acc' }; }), signedAt: /signed/i.test(n.status || '') ? (n.signedAt || n.date) : '', signedBy: n.signedBy || n.provider || '', imported: true };
  }

  // ---- 1. you: so you can start visits right away ----
  if (!S.staff.length && (me.full_name || me.provider_name)) {
    var you = { id: D.id('S'), name: personName(me.provider_name || me.full_name), cred: '', spec: '', loc: '', email: me.email || '', access: /admin/.test(me.role || '') ? 'Admin' : 'Provider', provider: true, status: 'Active', hired: D.today(), creds: [], reminders: [], training: [] };
    S.staff.push(you); S.clinic.userStaff = you.id; S.clinic.user = (me.provider_name || me.full_name);
  }

  // ---- 2. the old EMR in this browser, every load (anything already here is skipped, so nothing doubles) ----
  var added = { p: 0, c: 0, a: 0, n: 0 };
  if (!D.DENTAL) {   // the previous EMR was medical only; a dental clinic starts in this one
    read(OLD.patients).forEach(function (o) { if (o && o.mrn && !D.pt(o.mrn)) { S.patients.push(patientFromOld(o)); added.p++; } });
    read(OLD.claims).forEach(function (o) { if (o && !o.isPlaceholder && (o.claimId || o.id) && !D.claim(o.claimId || o.id)) { S.claims.push(claimFrom(o)); added.c++; } });
    read(OLD.notes).forEach(function (n) { if (n && n.mrn && !D.enc('OLD-' + n.id)) { S.encs.push(encFromOld(n)); added.n++; } });
    read(OLD.appts).forEach(function (a) {
      if (!a || S.appts.some(function (x) { return x.id === 'OLD-' + a.id; })) return;
      var st = String(a.status || '').toLowerCase();
      S.appts.push({ id: 'OLD-' + a.id, mrn: ensurePatient(a.mrn, a.patient), date: iso(a.date), time: a.time || '09:00', prov: staffFor(a.provider), type: a.type || 'Office visit', reason: a.notes || a.type || '',
        visit: /cancel/.test(st) ? 'Cancelled' : /no.?show/.test(st) ? 'No show' : /complete|checked out|done/.test(st) ? 'Checked out' : /checked in|arrived/.test(st) ? 'Checked in' : 'Scheduled' });
      added.a++;
    });
    if (!S.importedLocal) S.importedLocal = new Date().toISOString();
    if (added.p + added.c + added.a + added.n) D.log('Brought over from the previous EMR: ' + added.p + ' patients, ' + added.c + ' claims, ' + added.a + ' appointments, ' + added.n + ' visit notes');
  }
  D.save();

  // claims saved without a rendering provider get the clinic's signing provider (the payer requires one)
  var defProv = function () { var me = D.staff(S.clinic.userStaff); return me && me.provider ? me.id : (D.providers()[0] || {}).id || ''; };
  S.claims.forEach(function (c) { if (!c.prov && defProv()) { c.prov = defProv(); D.history(c, 'Rendering provider set to ' + D.provName(c.prov) + ' (none was saved on the claim)'); } });
  S.claims.forEach(function (c) { if (c.stage === 'edits' || c.stage === 'ready') D.scrub(c); });   // open claims are re-checked against today's rules
  D.save();

  // ---- 3. the server, every load ----
  var api = function (url) { return fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }); };
  // also every 30 seconds while the page is open, so other logins' patients and claims show up live
  var pullServer = function () { return Promise.all([has('suite') ? api('/api/patients') : [], has('insurance') ? api('/api/claims') : []]).then(function (res) {
    var n = 0;
    (Array.isArray(res[0]) ? res[0] : []).forEach(function (o) { if (o && o.mrn && !D.pt(o.mrn)) { S.patients.push(patientFromOld(o)); n++; } });
    (Array.isArray(res[1]) ? res[1] : []).forEach(function (o) { var have = o && o.id && D.claim(o.id);
      if (have && have.imported && (!have.dx.length || !have.lines.length)) { var cd = codesFrom(o);
        if ((!have.dx.length && cd.dx.length) || (!have.lines.length && cd.cpts.length)) { if (!have.dx.length) have.dx = cd.dx; if (!have.lines.length) have.lines = cd.cpts; delete have.check; D.history(have, 'Codes restored from the previous EMR'); if (have.stage === 'edits' || have.stage === 'ready') D.scrub(have); n++; } } });
    (Array.isArray(res[1]) ? res[1] : []).forEach(function (o) { if (o && o.id && !D.claim(o.id)) { var c = claimFrom(o); if (!c.prov) c.prov = defProv(); S.claims.push(c); n++; } });
    // remember what the server already has so unchanged records aren't sent back
    (Array.isArray(res[0]) ? res[0] : []).forEach(function (o) { if (o && o.mrn && D.pt(o.mrn)) sent['p:' + o.mrn] = sent['p:' + o.mrn] || 'server'; });
    if (n) { S.claims.forEach(function (c) { if (c.stage === 'edits' || c.stage === 'ready') D.scrub(c); }); D.log('Brought over ' + n + ' records from the server'); D.save(); if (window.V && V.refresh) V.refresh(); }
    ready = true; push();
  }); };
  pullServer();
  D.pullServer = pullServer;
  setInterval(function () { if (!document.hidden) pullServer(); }, 30000);

  // ---- 4. write patients and claims back to the server after changes ----
  var sent = {}, ready = false, timer = null;
  try { sent = JSON.parse(localStorage.getItem(D.KEY + '::sent')) || {}; } catch (e) { sent = {}; }
  function patientOut(p) {
    var pcp = D.staff(p.pcp);
    return Object.assign({}, p, { insPrimary: p.payer, insPrimaryMeta: p.member ? 'Mbr ' + p.member : '', pcp: pcp ? pcp.name : '', provider: pcp ? pcp.name : '',
      allergies: String(p.allergies || '').split(/;\s*/).filter(Boolean), dob: p.dob,
      // the old EMR reads problems and medications as lists
      problems: String(p.problems || '').split(/;\s*|\n/).filter(Boolean).map(function (x) { var m = x.match(/^([A-Z]\d[0-9A-Z.]*)\s+(.*)$/); return m ? { code: m[1], label: m[2] } : { code: '', label: x }; }),
      meds: String(p.meds || '').split(/\n|;\s*/).filter(Boolean).map(function (x) { return { name: x, status: 'Active' }; }) });
  }
  var STATUS = { edits: 'Pending Review', ready: 'Ready to Submit', submitted: 'Submitted', paid: 'Paid', denied: 'Denied' };
  function claimOut(c) {
    return { id: c.id, patientName: D.ptName(c.mrn), mrn: c.mrn, payer: D.payer(c), amount: Math.round(D.charge(c)), status: c.stage === 'denied' && c.dstatus === 'Appealed' ? 'Appealed' : STATUS[c.stage] || 'Draft',
      codes: c.lines.map(function (l) { var x = { code: l.code, type: /^D\d{4}$/.test(l.code) ? 'CDT' : 'CPT', modifier: l.mod || '', units: l.units || 1 }; if (l.tooth || l.surface || l.quad) { x.tooth = l.tooth || ''; x.surface = l.surface || ''; x.quad = l.quad || ''; } return x; }).concat(c.dx.map(function (d) { return { code: d, type: 'ICD-10' }; })),
      flags: (c.edits || []).map(function (x) { return x.id; }), appealLetter: c.letter || '', createdAt: c.created || '', dos: c.dos || '', provider: (D.staff(c.prov) || {}).name || '' };
  }
  function push() {
    if (!ready) return;
    var jobs = [];
    if (has('suite')) S.patients.filter(function (p) { return !p.sample; }).forEach(function (p) { var body = JSON.stringify(patientOut(p)); if (sent['p:' + p.mrn] !== body && !(sent['p:' + p.mrn] === 'server' && p.imported && !p.edited)) jobs.push(['/api/patients', body, 'p:' + p.mrn]); });
    if (has('insurance')) S.claims.filter(function (c) { return !c.sample; }).forEach(function (c) { var body = JSON.stringify(claimOut(c)); if (sent['c:' + c.id] !== body) jobs.push(['/api/claims', body, 'c:' + c.id]); });
    (function next(i) {
      if (i >= jobs.length) { try { localStorage.setItem(D.KEY + '::sent', JSON.stringify(sent)); } catch (e) {} return; }
      var j = jobs[i];
      fetch(j[0], { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: j[1] })
        .then(function (r) { if (r.ok) sent[j[2]] = j[1]; }).catch(function () {}).then(function () { next(i + 1); });
    })(0);
  }
  var save = D.save;
  D.save = function () { save(); clearTimeout(timer); timer = setTimeout(push, 1500); };
  // an account created without a clinic name, whose Practice details already name the clinic: give the account that name,
  // so billing setup and claim sending work (server: /api/clinic/name moves the account's records to the clinic)
  (function () {
    var name = String(S.clinic.name || '').trim();
    if (String(me.organization || '').trim() || (me.role && me.role !== 'admin') || name.length < 2 || name === 'My Clinic') return;
    try { if (sessionStorage.getItem('althais.clinicNameTried')) return; sessionStorage.setItem('althais.clinicNameTried', '1'); } catch (e) {}
    fetch('/api/clinic/name', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
        if (!j || !j.organization || j.unchanged) return;
        try { localStorage.setItem('althais.v4.store.v1::' + String(j.organization).trim().toLowerCase().replace(/[^a-z0-9]+/g, '_'), localStorage.getItem(D.KEY) || JSON.stringify(S)); } catch (e) {}
        location.reload();
      }).catch(function () {});
  })();
})();
