/* v4, dental version: the screens that differ for a dental clinic (set per clinic in /admin).
   - V.claimForm: the claim as a form to view and print (the ADA dental claim's fields for dental, CMS-1500 for medical)
   - V.odontogram / V.toothChart: which teeth have procedures, on a visit, a claim or the patient's chart
   - V.dentalLines / V.addDentalLine: a claim's procedures with tooth, surfaces and quadrant, editable
   - V.loadDentalSample: a dental office's day for demos, every claim tripping a different dental rule
   Medical clinics never reach the dental code paths here: each one checks D.DENTAL. */
(function () {
  var E = U.esc, M = U.money, S = D.S;

  V.claimForm = function (c) { return D.DENTAL ? V.adaForm(c) : V.cms1500(c); };

  /* ---------- tooth chart ---------- */
  var UPPER = [], LOWER = [], PRIM_U = 'ABCDEFGHIJ'.split(''), PRIM_L = 'TSRQPONMLK'.split('');
  for (var i = 1; i <= 16; i++) UPPER.push(String(i));
  for (var j = 32; j >= 17; j--) LOWER.push(String(j));
  // lines: [{code, tooth, surface, quad, title?}] -> the mouth, the teeth with procedures marked
  V.odontogram = function (lines, o) {
    o = o || {};
    var by = {};
    (lines || []).forEach(function (l) { var t = DX.normTooth(l.tooth); if (!t) return; (by[t] = by[t] || []).push((l.title ? l.title + ': ' : '') + l.code + (l.surface ? ' ' + l.surface : '')); });
    var prim = Object.keys(by).some(function (t) { return /^[A-T]$/.test(t); });
    var cell = function (t, lo) {
      var on = by[t], sz = o.small ? 'font-size:8.5px;padding:3px 0;letter-spacing:-.4px;min-width:0;overflow:hidden' : 'font-size:11px;padding:5px 0';
      return '<div title="' + E(on ? 'Tooth ' + t + ': ' + on.join(', ') : 'Tooth ' + t) + '" data-tooth="' + t + '" style="' + sz + ';text-align:center;border:1px solid ' + (on ? 'var(--blue)' : 'var(--b2)') + ';' +
        'border-radius:' + (lo ? '7px 7px 3px 3px' : '3px 3px 7px 7px') + ';background:' + (on ? 'var(--blue)' : 'var(--surface,#fff)') + ';color:' + (on ? '#fff' : 'var(--t2)') + ';font-weight:' + (on ? 600 : 400) + '">' + t + '</div>';
    };
    var row = function (teeth, lo) { return '<div style="display:grid;grid-template-columns:repeat(' + teeth.length + ',minmax(0,1fr));gap:' + (o.small ? 1 : 2) + 'px;margin:2px 0">' + teeth.map(function (t) { return cell(t, lo); }).join('') + '</div>'; };
    var quads = (lines || []).filter(function (l) { return l.quad; }).map(function (l) { return l.code + ' ' + l.quad; });
    return '<div class="odo" aria-label="Tooth chart, Universal numbering">' + row(UPPER) + row(LOWER, 1) +
      (prim ? '<div class="mut" style="font-size:11px;margin-top:4px">Primary teeth</div>' + row(PRIM_U) + row(PRIM_L, 1) : '') +
      (quads.length ? '<div class="mut" style="font-size:11.5px;margin-top:4px">By quadrant or arch: ' + E(quads.join(', ')) + '</div>' : '') +
      (!Object.keys(by).length && !quads.length && !o.quiet ? '<div class="mut" style="font-size:11.5px;margin-top:4px">No tooth-specific procedures yet.</div>' : '') + '</div>';
  };
  V.teethTreated = function (mrn) {
    var t = {}; S.claims.forEach(function (c) { if (c.mrn === mrn) (c.lines || []).forEach(function (l) { var n = DX.normTooth(l.tooth); if (n) t[n] = 1; }); });
    return Object.keys(t);
  };
  // the patient's chart: every procedure on a claim, by tooth
  V.toothChart = function (mrn) {
    var rows = [], lines = [];
    S.claims.filter(function (c) { return c.mrn === mrn; }).sort(function (a, b) { return (b.dos || '').localeCompare(a.dos || ''); }).forEach(function (c) {
      (c.lines || []).forEach(function (l) { lines.push({ code: l.code, tooth: l.tooth, surface: l.surface, quad: l.quad, title: D.us(c.dos, 1) });
        if (l.tooth || l.quad) rows.push({ attrs: ' data-href="' + V.claimHref(c.id) + '"', cells: [E(l.tooth ? '#' + DX.normTooth(l.tooth) : l.quad), D.us(c.dos, 1), '<b class="mono">' + E(l.code) + '</b>', E(l.surface || ''), E(((S.fees.filter(function (f) { return f.code === l.code; })[0]) || {}).desc || ''), V.claimStatus(c)] }); });
    });
    return V.odontogram(lines) + '<div style="margin-top:8px">' + U.grid(['Tooth', 'DOS', 'CDT', 'Surface', 'Procedure', 'Claim'], rows, { click: 1, tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No tooth-specific procedures on file.') + '</tbody>') + '</div>';
  };

  /* ---------- a claim's procedures ---------- */
  var open = function (c) { return ['edits', 'ready'].indexOf(c.stage) >= 0; };
  V.dentalLines = function (c) {
    var ed = open(c), inp = function (i, f, v, w, ph) { return '<input class="inl" data-dlc="' + E(c.id) + '" data-dli="' + i + '" data-dlf="' + f + '" value="' + E(v || '') + '" placeholder="' + (ph || '') + '" style="width:' + w + 'px">'; };
    var rows = c.lines.map(function (l, i) {
      return { cells: [i + 1, ed ? inp(i, 'code', l.code, 58) : '<b class="mono">' + E(l.code) + '</b>', ed ? inp(i, 'tooth', l.tooth, 38, DX.needsTooth(l.code) ? 'req.' : '') : E(l.tooth || ''),
        ed ? inp(i, 'surface', l.surface, 50, DX.SURF_COUNT[l.code] ? 'req.' : '') : E(l.surface || ''), ed ? inp(i, 'quad', l.quad, 36, DX.needsQuad(l.code) || DX.needsArch(l.code) ? 'req.' : '') : E(l.quad || ''),
        M(D.lineFee(l)), ed ? '<button class="mini" data-dldel="' + i + '" data-dlc="' + E(c.id) + '">×</button>' : ''] };
    });
    return '<div class="dp-sec"><div class="dp-h">Procedures' + (ed ? '<span class="end"><a href="#" id="addline">Add procedure</a></span>' : '') + '</div>' +
      U.grid(['Ln', 'CDT', 'Tooth', 'Surface', 'Quad', ['Fee', 'num'], ''], rows, { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(7, 'No procedures yet.') + '</tbody>') +
      '<div style="margin-top:8px">' + V.odontogram(c.lines, { small: 1, quiet: 1 }) + '</div>' +
      (c.dx.length ? '<div class="mut" style="margin-top:5px">Diagnoses: ' + E(c.dx.join(', ')) + '</div>' : '') + '</div>' +
      (V.claimDocs ? V.claimDocs(c) : '');
  };
  var changed = function (c, text) { D.scrub(c); D.history(c, text); D.log(text + ': ' + c.id); D.save(); V.refresh(); };
  U.on(document.body, '[data-dlf]', 'change', function (el) {
    var c = D.claim(el.dataset.dlc), l = c && c.lines[+el.dataset.dli]; if (!l) return;
    var f = el.dataset.dlf, v = el.value.trim();
    l[f] = f === 'code' ? v.toUpperCase() : f === 'tooth' ? DX.normTooth(v) : f === 'surface' ? DX.normSurface(v) : v.toUpperCase();
    changed(c, 'Procedure ' + (+el.dataset.dli + 1) + ' edited: ' + DX.lineText(l));
  });
  U.on(document.body, '[data-dldel]', 'click', function (b) { var c = D.claim(b.dataset.dlc); if (!c) return; var l = c.lines.splice(+b.dataset.dldel, 1)[0]; changed(c, 'Procedure removed: ' + DX.lineText(l)); });
  V.addDentalLine = function (c, done) {
    U.form('Add Procedure', [{ k: 'code', label: 'CDT code', req: 1, ph: 'D2392', list: 'cdt-list' }, { k: 'tooth', label: 'Tooth', ph: '14 (or A to T)' }, { k: 'surface', label: 'Surfaces', ph: 'MO' }, { k: 'quad', label: 'Quadrant or arch', type: 'select', options: ['', 'UR', 'UL', 'LR', 'LL', 'UA', 'LA'] }], {}, function (v) {
      var code = v.code.toUpperCase().replace(/\s/g, ''), tooth = DX.normTooth(v.tooth);
      if (!DX.CDT.test(code)) return 'Enter a CDT code: D and four digits, like D2392.';
      if (tooth && !DX.toothKind(tooth)) return 'Tooth ' + tooth + ' isn’t a tooth number. Use 1 to 32, or A to T for primary teeth.';
      var l = D.lineOf({ code: code, units: 1, tooth: tooth, surface: DX.normSurface(v.surface), quad: v.quad }); l.tooth = l.tooth || ''; l.surface = l.surface || ''; l.quad = l.quad || '';
      c.lines.push(l); done(c, 'Procedure added: ' + DX.lineText(l));
    }, { save: 'Add', width: 520, intro: '<datalist id="cdt-list">' + S.fees.map(function (f) { return '<option value="' + E(f.code) + '">' + E(f.desc) + '</option>'; }).join('') + '</datalist>' });
  };

  /* ---------- the claim as the ADA dental claim's fields ---------- */
  V.adaForm = function (c) {
    var s = V.claimSnap(c), p = D.pt(c.mrn) || {}, prov = D.staff(c.prov) || {}, dt = function (iso) { return iso ? D.us(iso) : ''; };
    var f = function (n, label, val, w) { return '<div class="ad-f" style="' + (w ? 'grid-column:span ' + w : '') + '"><span>' + (n ? n + '. ' : '') + E(label) + '</span><b>' + (val == null || val === '' ? '&nbsp;' : E(val)) + '</b></div>'; };
    var sect = function (title, body) { return '<div class="ad-s"><div class="ad-h">' + E(title) + '</div><div class="ad-g">' + body + '</div></div>'; };
    var rows = s.cpts.slice(0, 10), dx = s.icds.slice(0, 4);
    var html = '<div class="ad" id="adaform"><div class="ad-t"><b>Dental claim</b><span>The fields of the ADA dental claim form, filled from this claim. Sent electronically as an 837D.</span></div>' +
      sect('Header', f(1, 'Type of transaction', c.preauth ? 'Request for predetermination / preauthorization' : 'Statement of actual services', 2) + f(2, 'Predetermination / preauthorization number', c.auth || '', 2)) +
      sect('Insurance company', f(3, 'Dental plan', s.payer, 4)) +
      sect('Policyholder / subscriber', f(12, 'Name', s.patient, 2) + f(13, 'Date of birth', dt(s.dob)) + f(14, 'Gender', s.sex) + f(15, 'Subscriber ID', s.insuredId || 'Missing', 2) + f(16, 'Plan / group number', p.group || '') + f(17, 'Employer', '')) +
      sect('Patient', f(18, 'Relationship to subscriber', 'Self') + f(20, 'Name', s.patient, 2) + f(21, 'Date of birth', dt(s.dob)) + f(22, 'Gender', s.sex) + f(23, 'Patient ID / account', s.mrn) + f('', 'Address', s.addrStreet, 2)) +
      '<div class="ad-s"><div class="ad-h">Record of services provided</div><table class="ad-l"><thead><tr><th>24. Date</th><th>25. Area</th><th>26. System</th><th>27. Tooth</th><th>28. Surface</th><th>29. Code</th><th>29a. Dx ptr</th><th>29b. Qty</th><th>30. Description</th><th>31. Fee</th></tr></thead><tbody>' +
        rows.map(function (l) { return '<tr><td>' + dt(s.svcDate) + '</td><td>' + E({ UR: '10', UL: '20', LL: '30', LR: '40', UA: '01', LA: '02' }[l.quad] || '') + '</td><td>' + (l.tooth ? 'JP' : '') + '</td><td>' + E(l.tooth || '') + '</td><td>' + E(l.surface || '') + '</td><td><b>' + E(l.code) + '</b></td><td>' + (dx.length ? 'A' : '') + '</td><td>' + (l.units || 1) + '</td><td>' + E(l.description || '') + '</td><td class="num">' + M(l.charge) + '</td></tr>'; }).join('') +
        (rows.length ? '' : '<tr><td colspan="10" class="mut">No procedures.</td></tr>') +
      '</tbody></table><div class="ad-g" style="margin-top:6px">' + f(33, 'Missing teeth', '', 2) + f(34, 'Diagnosis codes (ICD-10, optional)', dx.map(function (d, k) { return 'ABCD'[k] + ': ' + d.code; }).join('  ')) + f('32/33', 'Total fee', M(s.totalAmt)) + f(35, 'Remarks', c.remark || '', 4) + '</div></div>' +
      sect('Ancillary claim information', f(38, 'Place of treatment', '11 (office)') + f(39, 'Enclosures (X-rays, narrative)', c.attached ? 'Yes' : 'No') + f(40, 'Orthodontics?', 'No') + f(45, 'Treatment from injury?', 'No')) +
      sect('Billing dentist', f(48, 'Name and address', [S.clinic.name, S.clinic.addr, S.clinic.city].filter(Boolean).join(', '), 2) + f(49, 'NPI', S.clinic.npi) + f(51, 'TIN / EIN', S.clinic.ein) + f(52, 'Phone', S.clinic.phone)) +
      sect('Treating dentist', f(53, 'Signed', (prov.name || s.provider || '') + (prov.cred ? ' ' + prov.cred : ''), 2) + f(54, 'NPI', prov.npi || s.npi || '') + f(56, 'Address where performed', S.clinic.addr || '') + f('56a', 'Taxonomy', S.clinic.taxonomy || '')) + '</div>';
    U.modal('Dental claim, ' + c.id, html, [['Close', '', function () {}], ['Print', 'pri', function () {
      var w = window.open(''); w.document.write('<html><head><title>Dental claim ' + E(c.id) + '</title><style>' + ADA_CSS + '@page{size:letter;margin:.4in}body{margin:0;font-family:Arial,sans-serif}</style></head><body>' + html + '</body></html>'); w.document.close(); setTimeout(function () { w.print(); }, 300); return false;
    }]], { width: Math.min(980, window.innerWidth - 32) });
  };
  var ADA_CSS = '.ad{font:12px Arial,Helvetica,sans-serif;color:#111}.ad-t{display:flex;flex-direction:column;margin-bottom:8px}.ad-t b{font-size:16px}.ad-t span{font-size:11.5px;color:#555}' +
    '.ad-s{border:1px solid #9aa4b2;margin-bottom:6px}.ad-h{background:#eef2f7;font-weight:700;font-size:11px;letter-spacing:.03em;text-transform:uppercase;padding:3px 6px;border-bottom:1px solid #9aa4b2}' +
    '.ad-g{display:grid;grid-template-columns:repeat(4,1fr)}.ad-f{padding:3px 6px;border-right:1px solid #dde2ea;border-bottom:1px solid #dde2ea;min-height:34px}.ad-f span{display:block;font-size:10px;color:#555}.ad-f b{font:600 12px "Courier New",monospace;color:#0b2a6f}' +
    '.ad-l{width:100%;border-collapse:collapse}.ad-l th{font-size:10px;font-weight:400;text-align:left;background:#f6f8fb;border-bottom:1px solid #dde2ea;padding:3px 5px}.ad-l td{padding:3px 5px;border-bottom:1px solid #eef1f5;font-size:11.5px}.ad-l td.num{text-align:right}';
  var st = document.createElement('style'); st.textContent = ADA_CSS; document.head.appendChild(st);

  /* ---------- sample dental office ----------
     A full day for demos: today's schedule, a visit in progress for live coding, and claims in every stage. Each claim
     in Edits trips a different dental rule (surfaces, quadrant, same tooth, front tooth surface, age, frequency, filing
     deadline). Marked sample: it stays in this browser and is never sent to the server. */
  V.loadDentalSample = function (quiet) {
    if (S.patients.some(function (p) { return p.sample; })) return quiet ? null : U.toast('The sample clinic is already loaded. Use Reset to start it over.');
    var T = D.today(), add = function (n) { return D.addDays(T, n); }, me = V.myProvider(), now = new Date().toISOString();
    var staff = function (name, cred, provider, access) { var x = { id: D.id('S'), name: name, cred: cred, provider: provider, status: 'Active', access: access, creds: [], reminders: [], training: [], sample: true }; S.staff.push(x); return x.id; };
    var dds = me ? me.id : staff('Okafor, Nia', 'DDS', true, 'Provider');
    var rdh = staff('Lee, Jordan', 'RDH', true, 'Provider');
    staff('Patel, Sam', 'CPC', false, 'Biller');
    staff('Nguyen, Kim', '', false, 'Front desk');
    // name, dob, sex, dental plan, subscriber ID, allergies, medical history, medications, provider
    var P = {
      ava: ['Sample, Ava', '1988-03-14', 'F', 'Delta Dental PPO', 'DD-SMP-441027', '', '', '', rdh],
      ben: ['Sample, Ben', '1975-09-30', 'M', 'MetLife Dental', 'MET-SMP-881209', 'Penicillin (hives)', 'High blood pressure', 'Lisinopril 10 mg daily', dds],
      carla: ['Sample, Carla', '1992-01-08', 'F', 'Cigna Dental', 'CIG-SMP-550178', '', '', '', dds],
      david: ['Sample, David', '1965-11-22', 'M', 'Aetna Dental', 'AET-SMP-301992', '', 'Type 2 diabetes', 'Metformin 500 mg twice daily', dds],
      elena: ['Sample, Elena', '1971-06-14', 'F', 'Guardian Dental', 'GUA-SMP-773104', '', '', '', dds],
      frank: ['Sample, Frank', '1998-02-02', 'M', 'UHC Dental', 'UHC-SMP-900216', '', '', '', dds],
      grace: ['Sample, Grace', '2004-07-19', 'F', 'Humana Dental', '', '', '', '', dds],
      henry: ['Sample, Henry', '2015-05-05', 'M', 'TX Medicaid (Dental)', 'MCD-SMP-512339', '', '', '', rdh],
      iris: ['Sample, Iris', '1980-12-01', 'F', 'Delta Dental PPO', 'DD-SMP-448812', '', '', '', rdh],
      jack: ['Sample, Jack', '1959-04-27', 'M', 'MetLife Dental', 'MET-SMP-881744', 'Codeine (nausea)', 'On blood thinners', 'Warfarin 5 mg daily', dds],
      kate: ['Sample, Kate', '1990-08-09', 'F', 'Cigna Dental', 'CIG-SMP-550921', '', '', '', dds],
      leo: ['Sample, Leo', '1984-10-05', 'M', 'Guardian Dental', 'GUA-SMP-773388', '', '', '', rdh],
    };
    var m = {};
    Object.keys(P).forEach(function (k) { var x = P[k]; var p = { mrn: D.nextMrn(x[0]), name: x[0], dob: x[1], sex: x[2], payer: x[3], member: x[4], allergies: x[5], problems: x[6], meds: x[7], pcp: x[8], balance: k === 'grace' ? 60 : 0, status: 'Active', created: add(-400), eligChecked: T, sample: true }; S.patients.push(p); m[k] = p.mrn; });
    var L = function (spec) { return DX.parseProcs(spec).map(D.lineOf).map(function (l) { l.tooth = l.tooth || ''; l.surface = l.surface || ''; l.quad = l.quad || ''; return l; }); };
    var codesOf = function (lines) { return lines.map(function (l) { return { code: l.code, desc: (S.fees.filter(function (f) { return f.code === l.code; })[0] || {}).desc || l.code, type: 'CDT', tooth: l.tooth, surface: l.surface, quad: l.quad, conf: 92, why: 'From the visit note', state: 'acc' }; }); };
    var enc = function (k, spec, o) { var e = Object.assign({ id: D.id('E'), mrn: m[k], date: T, prov: P[k][8], visitType: 'Recall / hygiene', status: 'Signed', signedAt: now, signedBy: S.clinic.user, sample: true, vitals: {}, transcript: [], orders: [], codes: codesOf(L(spec || '')), addenda: [], ignored: {} }, o); S.encs.push(e); return e; };
    var mk = function (k, spec, stage, extra) {
      var c = Object.assign({ id: D.nextClaimId(), mrn: m[k], dos: T, prov: P[k][8], payer: P[k][3], lines: L(spec), dx: [], stage: stage, resolved: {}, created: now, sample: true }, extra || {});
      if (stage === 'edits' || stage === 'ready') D.scrub(c);
      D.history(c, 'Sample claim'); S.claims.push(c); return c;
    };
    // history first (paid), so frequency limits see it
    mk('iris', 'D0120; D1110; D0274', 'paid', { dos: add(-300), submittedOn: add(-299), paidOn: add(-280), paid: 198 });
    mk('iris', 'D0120; D1110', 'paid', { dos: add(-150), submittedOn: add(-149), paidOn: add(-131), paid: 140 });
    mk('ava', 'D0150; D0210; D1110', 'paid', { dos: add(-200), submittedOn: add(-199), paidOn: add(-180), paid: 276 });
    mk('ben', 'D0120; D1110', 'paid', { dos: add(-180), submittedOn: add(-179), paidOn: add(-160), paid: 140 });
    mk('david', 'D4341 LL; D4342 LR', 'paid', { dos: add(-400), submittedOn: add(-399), paidOn: add(-380), paid: 330, attached: true });
    mk('jack', 'D0120; D1110', 'paid', { dos: add(-175), submittedOn: add(-174), paidOn: add(-150), paid: 140 });
    mk('kate', 'D2392 #3 MO', 'paid', { dos: add(-10), submittedOn: add(-9), paidOn: add(-2), paid: 168 });
    mk('elena', 'D0120; D1110', 'paid', { dos: add(-18), submittedOn: add(-17), paidOn: add(-6), paid: 140 });
    mk('leo', 'D0120; D1110; D0274', 'paid', { dos: add(-24), submittedOn: add(-23), paidOn: add(-9), paid: 198 });
    mk('carla', 'D0150; D0330; D1110', 'paid', { dos: add(-60), submittedOn: add(-59), paidOn: add(-40), paid: 262 });

    // the visit in progress: the dentist writes, Althea codes it live (two fillings in the note)
    var eBen = enc('ben', '', { status: 'Draft', signedAt: '', signedBy: '', visitType: 'Restorative', reason: 'Recall, sensitivity upper left',
      s: 'Recall visit. Patient reports sensitivity to cold on the upper left for two weeks.', o: 'Periodic exam. Caries on #14 mesial and occlusal, and on #3 occlusal, confirmed on bitewings from March. Gingiva healthy.',
      a: 'Caries #14 MO and #3 O.', p: 'Local anesthetic, 2% lidocaine with epinephrine. #14 MO composite placed. #3 O composite placed. Occlusion checked. Recall in 6 months.', codes: [] });
    // signed visits whose claims trip a rule each
    var eCarla = enc('carla', 'D0140; D2391 #19 MOD', { visitType: 'Emergency', reason: 'Broken filling lower left', s: 'Broken filling lower left.', o: 'Limited exam. #19 large fractured restoration, recurrent decay mesial, occlusal and distal.', a: 'Recurrent caries #19 MOD.', p: '#19 MOD composite placed.' });
    var eDavid = enc('david', 'D4341; D1110', { visitType: 'Restorative', reason: 'Scaling and root planing', s: 'Bleeding gums.', o: 'Probing depths 5 to 6 mm with bleeding, upper right quadrant, teeth #2 to #5. Bone loss on X-rays.', a: 'Moderate periodontitis.', p: 'Scaling and root planing, upper right quadrant. Prophylaxis on remaining areas.' });
    var eElena = enc('elena', 'D2740 #30; D2391 #30 O', { visitType: 'Restorative', reason: 'Crown prep #30', s: 'Cracked tooth lower right.', o: '#30 fractured cusp with large failing restoration.', a: 'Fractured tooth #30.', p: 'Prepared #30 for a ceramic crown, buildup placed, impression taken, temporary crown cemented.' });
    var eFrank = enc('frank', 'D7140 #17; D2391 #17 O', { visitType: 'Emergency', reason: 'Pain lower left back', s: 'Pain lower left.', o: '#17 non-restorable caries.', a: 'Non-restorable #17.', p: 'Simple extraction #17 under local anesthetic.' });
    var eGrace = enc('grace', 'D0150; D0120; D2331 #8 MO', { visitType: 'New patient', reason: 'New patient, chipped front tooth', s: 'Chipped front tooth.', o: 'Comprehensive exam. #8 mesial-incisal fracture.', a: 'Fractured #8.', p: '#8 MI composite placed.' });
    var eIris = enc('iris', 'D0120; D1110; D0274', { reason: 'Recall', s: 'No complaints.', o: 'Periodic exam, light calculus. Four bitewings: no new caries.', a: 'Healthy.', p: 'Adult cleaning. Recall in 6 months.' });
    var eJack = enc('jack', 'D0120; D1110; D1206', { reason: 'Recall', s: 'No complaints. On warfarin.', o: 'Periodic exam. Light plaque.', a: 'Healthy.', p: 'Adult cleaning, fluoride varnish.' });
    var eAva = enc('ava', 'D0120; D1110; D0274', { reason: 'Recall', s: 'No complaints.', o: 'Periodic exam. Four bitewings, no caries.', a: 'Healthy.', p: 'Adult cleaning.' });

    var appt = function (k, date, time, visit, o) { S.appts.push(Object.assign({ id: D.id('A'), mrn: m[k], date: date, time: time, prov: P[k][8], reason: '', type: 'Recall / hygiene', visit: visit, sample: true }, o)); };
    appt('ava', T, '08:00', 'Checked out', { reason: 'Recall', encId: eAva.id, room: 'Op 1' });
    appt('iris', T, '08:00', 'Checked out', { reason: 'Recall', encId: eIris.id, room: 'Op 2' });
    appt('carla', T, '08:30', 'Checked out', { reason: 'Broken filling', type: 'Emergency', encId: eCarla.id, room: 'Op 3' });
    appt('elena', T, '09:00', 'Checked out', { reason: 'Crown prep #30', type: 'Restorative', encId: eElena.id, room: 'Op 3' });
    appt('jack', T, '09:00', 'Checked out', { reason: 'Recall', encId: eJack.id, room: 'Op 1' });
    appt('david', T, '10:00', 'Checked out', { reason: 'Scaling and root planing', type: 'Restorative', encId: eDavid.id, room: 'Op 2' });
    appt('frank', T, '10:30', 'Checked out', { reason: 'Toothache', type: 'Emergency', encId: eFrank.id, room: 'Op 3' });
    appt('grace', T, '11:00', 'Checked out', { reason: 'New patient', type: 'New patient', encId: eGrace.id, room: 'Op 1' });
    appt('ben', T, '11:30', 'In room', { reason: 'Recall, sensitivity upper left', type: 'Restorative', encId: eBen.id, room: 'Op 3' });
    appt('kate', T, '13:30', 'Checked in', { reason: 'Recall' });
    appt('leo', T, '14:00', 'Scheduled', { reason: 'Crown seat #19', type: 'Restorative' });
    appt('henry', T, '15:00', 'Scheduled', { reason: 'Child recall' });
    appt('ava', add(1), '09:00', 'Scheduled', { reason: 'Whitening consult', type: 'Consult' });
    appt('david', add(42), '10:00', 'Scheduled', { reason: 'Perio re-evaluation', type: 'Exam' });

    // edits: each trips a different dental rule
    mk('carla', 'D0140; D2391 #19 MOD', 'edits', { encId: eCarla.id, hadEdits: true });                 // 1-surface code, 3 surfaces: bill D2393
    mk('david', 'D4341; D1110', 'edits', { encId: eDavid.id, hadEdits: true });                          // no quadrant, cleaning with SRP, perio chart
    mk('elena', 'D2740 #30; D2391 #30 O', 'edits', { encId: eElena.id, hadEdits: true });                // crown and filling on one tooth, X-ray and narrative
    mk('frank', 'D7140 #17; D2391 #17 O', 'edits', { encId: eFrank.id, hadEdits: true });                // extracted and filled at one visit
    mk('grace', 'D0150; D0120; D2331 #8 MO', 'edits', { encId: eGrace.id, hadEdits: true });             // two exams, occlusal on a front tooth, no subscriber ID
    mk('iris', 'D0120; D1110; D0274', 'edits', { encId: eIris.id, hadEdits: true });                     // third exam and cleaning in 12 months
    mk('jack', 'D0120; D1110; D1206', 'edits', { encId: eJack.id, hadEdits: true });                     // fluoride for an adult
    mk('henry', 'D0120; D1120; D1206', 'edits', { dos: add(-84), hadEdits: true });                      // TX Medicaid, filing deadline close
    // ready, one already approved for tonight's batch
    mk('ava', 'D0120; D1110; D0274', 'ready', { encId: eAva.id });
    var dq = mk('kate', 'D0140; D0220; D9110', 'ready', { dos: add(-1), queuedAt: now, submitAt: V.nextBatch(), approvedAt: now, approvedBy: S.clinic.user });
    if (V.checkKey) dq.queueKey = V.checkKey(dq);
    // with the plan
    mk('leo', 'D2740 #19; D2950 #19', 'submitted', { dos: add(-21), submittedOn: add(-20), status: 'Accepted by payer', attached: true });
    mk('jack', 'D3330 #3; D0220', 'submitted', { dos: add(-5), submittedOn: add(-4), status: 'Rejected by clearinghouse: subscriber ID not found' });
    mk('carla', 'D2392 #12 DO', 'submitted', { dos: add(-35), submittedOn: add(-34), status: 'Accepted 277CA' });
    // denied, appealed, overturned
    mk('frank', 'D2950 #3; D2750 #3', 'denied', { dos: add(-30), submittedOn: add(-29), deniedOn: add(-12), carc: 'CO-252', reason: 'Attachment required', appealBy: add(48), dstatus: 'Needs review', everDenied: true });
    mk('ben', 'D4341 UR; D4341 UL', 'denied', { dos: add(-50), submittedOn: add(-49), deniedOn: add(-35), carc: 'CO-50', reason: 'Not medically necessary', appealBy: add(25), dstatus: 'Appealed', everDenied: true, appealedOn: add(-25),
      basis: 'Probing depths of 5 to 7 mm with bleeding in both upper quadrants documented, with bone loss on X-rays.', letter: '' });
    mk('elena', 'D0150; D0210', 'paid', { dos: add(-90), submittedOn: add(-89), paidOn: add(-45), paid: 188, everDenied: true, carc: 'CO-16', reason: 'Missing subscriber date of birth', appealOutcome: 'Overturned' });
    D.log('Sample dental clinic loaded'); D.save();
    if (!quiet) { U.toast('Sample dental clinic loaded: 12 patients, today’s schedule, a visit in progress and ' + S.claims.filter(function (c) { return c.sample; }).length + ' claims. Reset or remove it in Settings › Security and data.'); V.refresh(); }
  };
})();
