/* Althais v4 working preview: the clinic's data, kept in this browser (localStorage), encrypted with AES-256-GCM. Starts blank.
   The key is made per clinic by the server (crypto_box.browser_key) and is only in the signed-in page, which browsers
   don't keep, so what's stored can't be read after sign-out or off another copy of the computer's files.
   Every page reads and writes through window.D. Nothing here is sent anywhere except the two AI calls the encounter
   page makes to this server (/api/code-note, /api/althea/extract). */
(function () {
  var me = window.__ALTHAIS_USER__ || {};
  var D = {};
  // which version this clinic sees, set per clinic in /admin: "medical" (ER, urgent care, office) or "dental" (static/v4/dental.js)
  D.DENTAL = me.practice === 'dental' && !!window.DX;
  var KEY = 'althais.v4.store.v1::' + String(me.organization || me.email || 'local').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_') + (D.DENTAL ? '::dental' : '');   // one per clinic (and version)
  var pad = function (n) { return String(n).padStart(2, '0'); };
  // words that differ between the versions
  D.L = D.DENTAL ? { proc: 'CDT', procs: 'Procedures', pcp: 'Dentist', member: 'Subscriber ID', ins: 'Dental plan', form: 'ADA claim form', formShort: 'ADA', provider: 'Dentist', visit: 'Visit', edi: '837D', dxOptional: true }
                 : { proc: 'CPT', procs: 'CPT codes', pcp: 'PCP', member: 'Member ID', ins: 'Primary ins.', form: 'CMS-1500', formShort: 'CMS-1500', provider: 'Provider', visit: 'Encounter', edi: '837P', dxOptional: false };
  // a procedure (billed service) code, as opposed to a diagnosis
  D.isProc = function (type) { return type === 'CPT' || type === 'CDT'; };
  // a claim line from a code (keeps tooth, surface and quadrant for dental)
  D.lineOf = function (c) { var l = { code: String(c.code || '').toUpperCase(), mod: c.mod || c.modifier || '', units: +c.units || 1 }; if (Number(c.charge) > 0) l.charge = Math.round(Number(c.charge) * 100) / 100;   // a charge that came with the claim (EMR, extension, import) wins over the fee schedule
  if (c.tooth || c.surface || c.quad) { l.tooth = c.tooth || ''; l.surface = c.surface || ''; l.quad = c.quad || ''; } return l; };
  D.lineText = function (l) { return D.DENTAL ? DX.lineText(l) : l.code + (l.mod ? '-' + l.mod : '') + (l.units > 1 ? ' \u00d7' + l.units : ''); };

  D.PAYERS = D.DENTAL ? DX.PAYERS : ['Medicare B', 'Medicare Adv.', 'Aetna PPO', 'UHC', 'Cigna', 'BCBS TX', 'TX Medicaid', 'Self-pay'];
  D.VISIT_TYPES = D.DENTAL ? DX.VISITS : ['Office visit', 'Telehealth', 'New patient', 'Annual wellness', 'Procedure'];
  D.VISITS = ['Scheduled', 'Checked in', 'In room', 'Checked out', 'No show', 'Cancelled'];
  D.FEES = D.DENTAL ? DX.FEES : [['99202', 'New patient, straightforward', 92.35], ['99203', 'New patient, low MDM', 136.58], ['99204', 'New patient, moderate MDM', 211.61], ['99205', 'New patient, high MDM', 273.53],
    ['99212', 'Established, straightforward', 72.14], ['99213', 'Established, low MDM', 118.20], ['99214', 'Established, moderate MDM', 167.09], ['99215', 'Established, high MDM', 224.86],
    ['99395', 'Preventive, 18 to 39', 196.40], ['99396', 'Preventive, 40 to 64', 214.00], ['99397', 'Preventive, 65+', 228.15], ['G0439', 'Annual wellness, subsequent', 174.80],
    ['93000', 'ECG with interpretation', 73.36], ['36415', 'Venipuncture', 12.00], ['81002', 'Urinalysis, non-automated', 6.40], ['73721', 'MRI lower extremity joint', 612.00]];

  // Payer rule library. Each rule's check runs on every claim; local rules (added in Payer rules) run the same way.
  D.RULES = D.DENTAL ? DX.RULES : [
    { id: 'AET-DM-014', payer: 'Aetna', pub: '2026-10-09', eff: '2026-10-09', as: 'Note check', title: 'Foot exam documentation', applies: '99213 to 99215 with E10.x, E11.x', lvl: 'red',
      req: 'Foot exam documentation required, diabetes E&M 99213 to 99215', text: 'When an E&M visit is billed with a diabetes diagnosis (E10.x, E11.x), the note must document a foot exam: sensation, pulses, and skin.',
      how: ['Checks the note before signing', 'Searches the visit transcript for exam findings', 'Holds the claim as an edit if still missing'], src: 'Aetna Clinical Policy Bulletin, 10/09/2026' },
    { id: 'UHC-EM-022', payer: 'UHC', pub: '2026-10-03', eff: '2026-10-03', as: 'Note check', title: 'MDM support for 99214', applies: '99214', lvl: 'amb',
      req: '99214 requires 2 of 3 MDM elements documented', text: 'Level 4 office visits must document at least 2 of 3 medical decision making elements: problems, data reviewed, and risk.',
      how: ['Scores problems, data and risk from the note', 'Holds the claim as an edit if fewer than 2 are documented'], src: 'UHC Reimbursement Policy, 10/03/2026' },
    { id: 'BCBS-PA-108', payer: 'BCBS', pub: '2026-10-08', eff: '2026-12-01', as: 'Order check', title: 'Prior auth for ankle and knee MRI', applies: '73721', lvl: 'red',
      req: 'Prior auth required, MRI 73721, non-emergent', text: 'Non-emergent MRI of a lower extremity joint requires prior authorization starting 12/01/2026.',
      how: ['Checks the claim for an authorization number'], src: 'BCBS TX Provider Bulletin, 10/08/2026' },
    { id: 'AET-TH-031', payer: 'Aetna', pub: '2026-10-14', eff: '2026-10-14', as: 'Note check', title: 'Telehealth originating site', applies: 'Telehealth visits', lvl: 'amb',
      req: 'Telehealth: patient location must be documented', text: 'Telehealth visits must document where the patient was during the visit.',
      how: ['Checks telehealth notes for the patient’s location'], src: 'Aetna Telehealth Policy, 10/14/2026' },
    { id: 'CIG-MD-007', payer: 'Cigna', pub: '2026-09-30', eff: '2026-10-01', as: 'Claim edit', title: 'Modifier 25 review', applies: 'Preventive with problem visit', lvl: 'red',
      req: 'Modifier 25 on same-day preventive and problem visits', text: 'A problem visit billed the same day as a preventive visit needs modifier 25.',
      how: ['Flags same-day preventive and problem codes without modifier 25'], src: 'Cigna Reimbursement Policy R31, 09/30/2026' },
    { id: 'TXM-TF-095', payer: 'TX Medicaid', pub: '2026-09-18', eff: '2026-10-01', as: 'Deadline', title: 'Timely filing, 95 days', applies: 'All claims', lvl: 'amb',
      req: 'Timely filing 95 days from DOS', text: 'Claims must be received within 95 days of the date of service.',
      how: ['Flags unsubmitted claims within 15 days of the deadline'], src: 'TMHP Banner, 09/18/2026' },
    { id: 'MCR-CKD-003', payer: 'Medicare', pub: '2026-06-01', eff: '2026-06-01', as: 'Note check', title: 'CKD stage support', applies: 'N18.1 to N18.6', lvl: 'amb',
      req: 'CKD stage codes require eGFR support in assessment', text: 'CKD stage codes must be supported by an eGFR value cited in the assessment. "Stable" alone is a common denial reason.',
      how: ['Checks the assessment for an eGFR value', 'Proposes the eGFR found in the note or transcript, with its source', 'Provider approves the change'], src: 'Medicare LCD L35123, 06/01/2026' },
  ];

  function blank() {
    return {
      v: 1, seq: { mrn: 1000, claim: 10000, n: 1 },
      clinic: { name: 'My Clinic', user: 'You', userStaff: '', specialty: D.DENTAL ? 'General dentistry' : '', npi: '', ein: '', taxonomy: '', ptan: '', addr: '', city: '', phone: '', payto: 'Same as billing', fax: '', tz: 'Central (America/Chicago)' },
      soap: { consent: 'Required each visit', retention: '30 days', style: 'SOAP, concise', coding: 'On' },
      security: { twofa: 'Required', timeout: '15 minutes', audit: '7 years', baa: 'Not signed' },
      notify: { email: true, sms: false, denials: true, creds: true },
      templates: D.DENTAL ? ['Recall exam and cleaning', 'Restorative visit', 'Emergency visit'] : ['Office visit, established', 'Annual wellness', 'Telehealth follow-up'],
      ordersets: D.DENTAL ? ['Post-op extraction care', 'Perio re-evaluation'] : ['Diabetes follow-up labs', 'Hypertension follow-up'],
      patients: [], staff: [], appts: [], encs: [], claims: [], docs: [], requests: [], outgoing: [], messages: [], locations: [],
      localRules: [], fees: D.FEES.map(function (f) { return { code: f[0], desc: f[1], rate: f[2] }; }),
      integrations: (D.DENTAL ? [['DentalXChange', 'Dental claims, eligibility, ERA', false], ['NEA FastAttach', 'X-ray and narrative attachments', false], ['Surescripts', 'E-prescribing, med history', false], ['Stripe', 'Patient payments', false]]
        : [['Availity', 'Claims, eligibility, ERA', false], ['Quest Diagnostics', 'Lab orders and results', false], ['Surescripts', 'E-prescribing, med history', false], ['Stripe', 'Patient payments', false], ['Labcorp', 'Lab orders and results', false]]).map(function (i) { return { name: i[0], use: i[1], on: i[2], sync: '' }; }),
      log: [],
    };
  }
  // ---- encryption of this browser's copy: AES-256-GCM (static/v4/vendor/althais-crypto.min.js) ----
  // Stored as "A2u:<bytes>:" + the 12-byte nonce, ciphertext and 16-byte tag, packed 15 bits to a character
  // (U+1000 to U+8FFF, so never a broken surrogate): about half the room base64 would take.
  var BOX = (function () {
    var C = window.AltCrypto, enc = window.TextEncoder ? new TextEncoder() : null, AAD = enc ? enc.encode('althais-browser-store') : null;
    function b64(s) { try { var b = atob(s || ''), u = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u.length === 32 ? u : null; } catch (e) { return null; } }
    var key = C && enc && window.crypto && crypto.getRandomValues ? b64(window.__ALTHAIS_BK__) : null;
    function pack(u) {
      var n = Math.ceil(u.length * 8 / 15), out = new Uint16Array(n), acc = 0, bits = 0, j = 0, s = '';
      for (var i = 0; i < u.length; i++) { acc = (acc << 8) | u[i]; bits += 8; if (bits >= 15) { bits -= 15; out[j++] = 0x1000 + ((acc >> bits) & 0x7fff); acc &= (1 << bits) - 1; } }
      if (bits) out[j++] = 0x1000 + ((acc << (15 - bits)) & 0x7fff);
      for (var k = 0; k < n; k += 8192) s += String.fromCharCode.apply(null, out.subarray(k, Math.min(n, k + 8192)));
      return s;
    }
    function unpack(s, n) {
      var u = new Uint8Array(n), acc = 0, bits = 0, j = 0;
      for (var i = 0; i < s.length && j < n; i++) {
        var c = s.charCodeAt(i) - 0x1000; if (c < 0 || c > 0x7fff) throw new Error('bad data');
        acc = (acc << 15) | c; bits += 15;
        while (bits >= 8 && j < n) { bits -= 8; u[j++] = (acc >> bits) & 0xff; }
        acc &= (1 << bits) - 1;
      }
      if (j < n) throw new Error('short data');
      return u;
    }
    return {
      on: !!key,
      is: function (v) { return typeof v === 'string' && v.slice(0, 4) === 'A2u:'; },
      seal: function (text, k64) {
        var k = k64 ? b64(k64) : key; if (!k) return text;   // no key (should not happen): stored as before
        var nonce = crypto.getRandomValues(new Uint8Array(12)), ct = C.gcm(k, nonce, AAD).encrypt(enc.encode(text)), all = new Uint8Array(12 + ct.length);
        all.set(nonce); all.set(ct, 12);
        return 'A2u:' + all.length + ':' + pack(all);
      },
      open: function (v) {
        var m = /^A2u:(\d+):/.exec(v); if (!m || !key) throw new Error('no key');
        var all = unpack(v.slice(m[0].length), +m[1]);
        return new TextDecoder().decode(C.gcm(key, all.subarray(0, 12), AAD).decrypt(all.subarray(12)));
      },
      hash: function (text) {
        if (!C || !enc) return text;
        var h = C.sha256(enc.encode(text)), s = ''; for (var i = 0; i < h.length; i++) s += (h[i] < 16 ? '0' : '') + h[i].toString(16); return s;
      },
    };
  })();
  D.crypto = { on: BOX.on, hash: BOX.hash };

  var S, raw = null, lost = false, migrate = false;
  try { raw = localStorage.getItem(KEY); } catch (e) { raw = null; }
  if (raw && BOX.is(raw)) {
    try { S = JSON.parse(BOX.open(raw)); } catch (e) { S = null; lost = true; }
  } else if (raw) {   // saved before encryption: read it, and save it encrypted straight away
    try { S = JSON.parse(raw); migrate = true; } catch (e) { S = null; }
  }
  if (lost) {
    // the clinic's key changed (or the data is damaged): keep the unreadable copy aside, start again here, and let the
    // server bring patients and claims back (sync.js). Appointments and visit notes kept only in this browser can't be.
    try { if (!localStorage.getItem(KEY + '::undecryptable')) localStorage.setItem(KEY + '::undecryptable', raw); } catch (e) {}
    try { localStorage.removeItem(KEY + '::sent'); } catch (e) {}
    window.addEventListener('load', function () { setTimeout(function () { if (window.U && U.toast) U.toast('The records saved in this browser couldn’t be opened with this clinic’s key. Patients and claims are being brought back from the server.', 'warn'); }, 600); });
  }
  if (!S || S.v !== 1) { S = blank(); if (me.organization) S.clinic.name = me.organization; }
  D.S = S; D.KEY = KEY;

  // Saving encrypts the whole store, so changes made together are written once, a moment later, and always before
  // the page is left or hidden.
  var dirty = false, wt = null, gone = false;
  function write() {
    clearTimeout(wt); wt = null;
    if (!dirty || gone) return;
    dirty = false;
    try { localStorage.setItem(KEY, BOX.seal(JSON.stringify(S))); }
    catch (e) { (window.U && U.toast ? U.toast : alert)('This browser could not save. Storage may be full (large uploads).'); }
  }
  D.save = function () { dirty = true; if (!wt) wt = setTimeout(write, 200); };
  D.flush = function () { dirty = true; write(); };
  window.addEventListener('pagehide', write);
  window.addEventListener('beforeunload', write);
  document.addEventListener('visibilitychange', function () { if (document.hidden) write(); });
  D.reset = function () {
    gone = true; dirty = false; clearTimeout(wt);
    [KEY, KEY + '::sent', KEY + '::recs', KEY + '::undecryptable'].forEach(function (k) { try { localStorage.removeItem(k); } catch (e) {} });
  };
  // replace everything here with a backup (the backup file's text: plain JSON the clinic downloaded)
  D.restore = function (text) { gone = true; dirty = false; clearTimeout(wt); localStorage.setItem(KEY, BOX.seal(text)); try { localStorage.removeItem(KEY + '::sent'); localStorage.removeItem(KEY + '::recs'); } catch (e) {} };
  // the clinic was given a name: this browser's records move to it, encrypted with that clinic's key (from the server)
  D.moveTo = function (org, key64) {
    var to = 'althais.v4.store.v1::' + String(org || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_') + (D.DENTAL ? '::dental' : '');
    try { localStorage.setItem(to, key64 ? BOX.seal(JSON.stringify(S), key64) : JSON.stringify(S)); } catch (e) {}
  };
  if (migrate) D.flush();
  // appointments and visit notes are shared through the server (sync.js), so their ids also carry this browser's tag:
  // two browsers numbering from the same start can't make the same id
  D.id = function (p) {
    S.seq.n++; D.save();
    if (p !== 'A' && p !== 'E') return p + S.seq.n;
    if (!S.seq.tag) { var r = ''; try { var b = crypto.getRandomValues(new Uint8Array(4)); for (var i = 0; i < 4; i++) r += 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'[b[i] % 32]; } catch (e) { r = Math.random().toString(36).slice(2, 6).toUpperCase(); } S.seq.tag = r; }
    return p + S.seq.n + '-' + S.seq.tag;
  };
  D.log = function (text) { S.log.unshift({ at: new Date().toISOString(), text: text }); S.log = S.log.slice(0, 200); D.save(); };

  // ---- dates: stored as YYYY-MM-DD, shown as MM/DD/YYYY ----
  D.iso = function (d) { d = d || new Date(); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); };
  D.today = function () { return D.iso(new Date()); };
  D.us = function (iso, short) { if (!iso) return ''; var p = iso.slice(0, 10).split('-'); return p.length < 3 ? iso : p[1] + '/' + p[2] + '/' + (short ? p[0].slice(2) : p[0]); };
  D.addDays = function (iso, n) { var d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return D.iso(d); };
  D.daysBetween = function (a, b) { return Math.round((new Date(b + 'T12:00:00') - new Date(a + 'T12:00:00')) / 864e5); };
  D.longDate = function (iso) { return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }); };
  D.time = function () { return new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); };
  D.age = function (dob) { if (!dob) return ''; var b = new Date(dob + 'T12:00:00'), n = new Date(), a = n.getFullYear() - b.getFullYear(); if (n < new Date(n.getFullYear(), b.getMonth(), b.getDate())) a--; return a; };

  // ---- lookups ----
  D.pt = function (mrn) { return S.patients.filter(function (p) { return p.mrn === mrn; })[0] || null; };
  D.ptName = function (mrn) { var p = D.pt(mrn); return p ? p.name : '(removed patient)'; };
  D.staff = function (id) { return S.staff.filter(function (s) { return s.id === id; })[0] || null; };
  D.providers = function () { return S.staff.filter(function (s) { return s.provider && s.status !== 'Inactive'; }); };
  D.short = function (s) { if (!s) return ''; var p = s.name.split(/,\s*/); return p[0] + (p[1] ? ', ' + p[1][0] : ''); };
  D.provName = function (id) { var s = D.staff(id); return s ? D.short(s) : ''; };
  D.enc = function (id) { return S.encs.filter(function (e) { return e.id === id; })[0] || null; };
  D.claim = function (id) { return S.claims.filter(function (c) { return c.id === id; })[0] || null; };
  D.fee = function (code) { var f = S.fees.filter(function (x) { return x.code === String(code).toUpperCase(); })[0]; return f ? f.rate : 0; };
  D.lineFee = function (l) { return Number(l.charge) > 0 ? Number(l.charge) : D.fee(l.code) * (l.units || 1); };
  D.charge = function (c) { var f = D.sum(c.lines || [], D.lineFee); return f || Number(c.amount) || 0; };   // claims brought over keep their amount when a code has no fee here
  D.sum = function (list, f) { return list.reduce(function (s, x) { return s + (f ? (f(x) || 0) : x); }, 0); };
  D.payer = function (c) { var p = D.pt(c.mrn); return c.payer || (p ? p.payer : ''); };
  D.stage = function (st) { return S.claims.filter(function (c) { return c.stage === st; }); };
  D.nextMrn = function (name) { S.seq.mrn++; var ini = String(name || 'XX').split(/,\s*/); return ((ini[1] || 'X')[0] + (ini[0] || 'X')[0]).toUpperCase() + S.seq.mrn; };
  D.nextClaimId = function () { var id; do { S.seq.claim++; id = 'CHC-' + new Date().getFullYear() + '-' + S.seq.claim; } while (D.claim(id)); return id; };   // never reuse a number already on file
  D.allRules = function () { return D.RULES.concat(S.localRules); };

  // ---- the note as text (for coding, rule checks and the claim) ----
  D.noteText = function (e) {
    var v = D.DENTAL ? {} : e.vitals || {};
    return ['SUBJECTIVE: ' + (e.s || ''), 'OBJECTIVE: ' + [v.bp && 'BP ' + v.bp, v.hr && 'HR ' + v.hr, v.temp && 'Temp ' + v.temp, v.spo2 && 'SpO2 ' + v.spo2 + '%', v.wt && 'Wt ' + v.wt].filter(Boolean).join(', ') + ' ' + (e.o || ''),
      'ASSESSMENT: ' + (e.a || ''), 'PLAN: ' + (e.p || '')].concat((e.addenda || []).filter(function (x) { return x.signed; }).map(function (x) { return 'ADDENDUM: ' + x.text; })).join('\n');
  };

  /* ---- claim checks: basic edits plus every payer rule; each edit says what to fix and cites its evidence ---- */
  var sentenceWith = function (lines, re) { for (var i = 0; i < (lines || []).length; i++) if (re.test(lines[i].text)) return lines[i]; return null; };
  D.checkClaim = function (c) {
    if (D.DENTAL) return D.checkDental(c);
    var e = c._enc || D.enc(c.encId) || { s: '', o: '', a: '', p: '', transcript: [] }, p = D.pt(c.mrn), payer = D.payer(c) || '', note = D.noteText(e), out = [];
    var cpts = (c.lines || []).map(function (l) { return l.code; }), dx = c.dx || [];
    var has = function (re) { return re.test(note); };
    if (!cpts.length) out.push({ id: 'BASIC-CPT', lvl: 'red', label: 'No CPT code', text: 'A claim needs at least one service line.' });
    if (!dx.length) out.push({ id: 'BASIC-DX', lvl: 'red', label: 'No diagnosis', text: 'Every service line needs a diagnosis to point to.' });
    if (!p || !payer || payer === 'Self-pay') { if (payer !== 'Self-pay') out.push({ id: 'BASIC-INS', lvl: 'red', label: 'No insurance', text: 'Add the patient’s insurance before sending.' }); }
    else if (!p.member) out.push({ id: 'BASIC-MEM', lvl: 'amb', label: 'No member ID', text: 'No member ID on file for ' + payer + '. The payer may reject the claim without it.' });
    if (!(Number(c.amount) > 0) && cpts.some(function (x) { return !D.fee(x); })) out.push({ id: 'BASIC-FEE', lvl: 'amb', label: 'No fee', text: 'No fee on file for ' + cpts.filter(function (x) { return !D.fee(x); }).join(', ') + ' (Settings › Fee schedules).' });
    var em = cpts.filter(function (x) { return /^992(1[2-5]|0[2-5])$/.test(x); });
    var dos = c.dos || D.today();
    // AET-DM-014
    if (/^Aetna/.test(payer) && dos >= '2026-10-09' && cpts.some(function (x) { return /^9921[345]$/.test(x); }) && dx.some(function (d) { return /^E1[01]/.test(d); }) && !has(/foot|feet|monofilament|pedal|dorsalis/i)) {
      var q = sentenceWith(e.transcript, /\b(foot|feet)\b/i);
      out.push({ id: 'AET-DM-014', lvl: 'red', label: 'Doc. required', text: 'Aetna requires a documented foot exam when an E&M visit is billed with a diabetes diagnosis. No foot exam was found in the note.',
        evidence: q ? { kind: 'Transcript', when: q.t, quote: q.text } : null, addendum: q ? 'Foot exam (from visit recording, ' + q.t + '): ' + q.text : '' });
    }
    // UHC-EM-022
    if (/^UHC/.test(payer) && cpts.indexOf('99214') >= 0) {
      var probs = (e.a || '').split(/\n|;|\d\.\s/).filter(function (x) { return x.trim().length > 3; }).length >= 2;
      var data = /review|lab|ecg|ekg|x-?ray|imaging|bmp|cbc|a1c|egfr|result/i.test((e.o || '') + ' ' + (e.a || ''));
      var risk = /prescri|\brx\b|start|increase|decrease|\bmg\b|refill|surgery|admit/i.test(e.p || '');
      var n = [probs, data, risk].filter(Boolean).length;
      if (n < 2) out.push({ id: 'UHC-EM-022', lvl: 'amb', label: 'MDM elements', text: 'UHC requires 2 of 3 MDM elements for 99214. Found ' + n + ': ' + ['problems', 'data', 'risk'].filter(function (x, i) { return [probs, data, risk][i]; }).join(', ') + (n ? '' : 'none') + '.' });
    }
    // BCBS-PA-108
    if (/^BCBS/.test(payer) && dos >= '2026-12-01' && cpts.indexOf('73721') >= 0 && !c.auth) out.push({ id: 'BCBS-PA-108', lvl: 'red', label: 'Prior auth', text: 'Prior authorization is required for 73721. Add the authorization number to the claim.' });
    // AET-TH-031
    if (/^Aetna/.test(payer) && dos >= '2026-10-14' && (e.visitType === 'Telehealth') && !has(/patient (was |is )?(at|located)|location:|at home/i)) {
      var lq = sentenceWith(e.transcript, /\b(at home|i'?m at|kitchen|office|car|work)\b/i);
      out.push({ id: 'AET-TH-031', lvl: 'amb', label: 'Patient location', text: 'Telehealth notes must document where the patient was.', evidence: lq ? { kind: 'Transcript', when: lq.t, quote: lq.text } : null, addendum: lq ? 'Patient location during telehealth visit (from recording, ' + lq.t + '): ' + lq.text : '' });
    }
    // CIG-MD-007
    if (/^Cigna/.test(payer) && cpts.some(function (x) { return /^993[89]\d$/.test(x); }) && em.length && !(c.lines || []).some(function (l) { return /^99(21|20)/.test(l.code) && /25/.test(l.mod || ''); }))
      out.push({ id: 'CIG-MD-007', lvl: 'red', label: 'Modifier 25', text: 'Preventive and problem visits on the same day need modifier 25 on the problem visit.' });
    // TXM-TF-095
    if (/^TX Medicaid/.test(payer) && ['edits', 'ready'].indexOf(c.stage) >= 0 && D.daysBetween(dos, D.today()) >= 80) out.push({ id: 'TXM-TF-095', lvl: 'amb', label: 'Filing deadline', text: 'Timely filing ends ' + D.us(D.addDays(dos, 95)) + '. Send this claim now.' });
    // MCR-CKD-003
    if (/^Medicare/.test(payer) && dx.some(function (d) { return /^N18\.[1-6]/.test(d); }) && !/egfr/i.test(e.a || '')) {
      var m = ((e.o || '') + ' ' + (e.s || '')).match(/egfr[^0-9]{0,12}(\d{1,3})/i), tq = sentenceWith(e.transcript, /egfr|kidney number|kidney function/i);
      out.push({ id: 'MCR-CKD-003', lvl: 'amb', label: 'CKD support', text: 'CKD stage codes should cite eGFR in the assessment. "Stable" alone is a common denial reason.',
        evidence: m ? { kind: 'Note, objective', when: '', quote: m[0] } : tq ? { kind: 'Transcript', when: tq.t, quote: tq.text } : null, addendum: m ? 'Assessment: CKD stage supported by eGFR ' + m[1] + ' (objective, this visit).' : '' });
    }
    // local rules
    S.localRules.forEach(function (r) {
      if (payer.toLowerCase().indexOf(String(r.payer).toLowerCase()) === 0 && cpts.some(function (x) { return x.indexOf(r.cpt || '') === 0; }) && r.must && note.toLowerCase().indexOf(r.must.toLowerCase()) < 0)
        out.push({ id: r.id, lvl: 'amb', label: r.title, text: r.text || ('Note must mention "' + r.must + '".') });
    });
    return out;
  };
  /* ---- dental: the same basics, then the dental rules (static/v4/dental.js, the same as the server's) ---- */
  D.dentalInput = function (c, p) {
    p = p || D.pt(c.mrn) || {};
    var lines = (c.lines || []).map(function (l) { return { code: l.code, tooth: l.tooth || '', surface: l.surface || '', quad: l.quad || '' }; });
    return { cpt: lines.map(function (l) { return l.code; }), lines: lines, tooth_column: true, dob: p.dob || '', dos: c.dos || D.today() };
  };
  // this patient's other claims (any stage but a deleted draft), for frequency limits
  D.dentalHistory = function (c) {
    return S.claims.filter(function (x) { return x !== c && x.id !== c.id && x.mrn === c.mrn && x.dos && x.stage !== 'denied'; })
      .map(function (x) { return { dos: x.dos, cpt: (x.lines || []).map(function (l) { return l.code; }), lines: (x.lines || []).map(function (l) { return { code: l.code, tooth: l.tooth || '', surface: l.surface || '', quad: l.quad || '' }; }) }; });
  };
  D.checkDental = function (c) {
    var p = D.pt(c.mrn), payer = D.payer(c) || '', out = [], codes = (c.lines || []).map(function (l) { return l.code; });
    if (!codes.length) out.push({ id: 'BASIC-CPT', lvl: 'red', label: 'No procedure', text: 'A dental claim needs at least one CDT procedure.' });
    if (!p || !payer || payer === 'Self-pay') { if (payer !== 'Self-pay') out.push({ id: 'BASIC-INS', lvl: 'red', label: 'No dental plan', text: 'Add the patient\u2019s dental plan before sending.' }); }
    else if (!p.member) out.push({ id: 'BASIC-MEM', lvl: 'amb', label: 'No subscriber ID', text: 'No subscriber ID on file for ' + payer + '. The plan will reject the claim without it.' });
    if (!(Number(c.amount) > 0) && codes.some(function (x) { return !D.fee(x); })) out.push({ id: 'BASIC-FEE', lvl: 'amb', label: 'No fee', text: 'No fee on file for ' + codes.filter(function (x) { return !D.fee(x); }).join(', ') + ' (Settings \u203a Fee schedules).' });
    var input = D.dentalInput(c, p), found = DX.check(input), hist = D.dentalHistory(c);
    if (hist.length) found = found.concat(DX.historyFlags(hist.concat([input]))[hist.length] || []);
    var used = {};
    found.forEach(function (f) {
      if (f.severity === 'info') return;
      if (f.code === 'attach' && c.attached) return;   // marked sent with the claim
      var id = 'DEN-' + (DX.GROUP[f.code] || 'CODE') + (f.tooth ? '-' + f.tooth : ''), n = 2;
      while (used[id]) id = id.replace(/~\d+$/, '') + '~' + n++;
      used[id] = 1;
      out.push({ id: id, rule: 'DEN-' + (DX.GROUP[f.code] || 'CODE'), lvl: f.severity === 'error' ? 'red' : 'amb', label: DX.LABEL[f.code] || 'Dental rule', text: f.message, code: f.code, tooth: f.tooth || '' });
    });
    var e = c._enc || D.enc(c.encId), note = (e ? D.noteText(e) : String(c.note || '')).toLowerCase();
    S.localRules.forEach(function (r) {   // the clinic's own rules (Payer rules › Add rule), as in the medical version
      if (payer.toLowerCase().indexOf(String(r.payer).toLowerCase()) === 0 && codes.some(function (x) { return x.indexOf(r.cpt || '') === 0; }) && r.must && note.indexOf(r.must.toLowerCase()) < 0)
        out.push({ id: r.id, lvl: 'amb', label: r.title, text: r.text || ('Note must mention "' + r.must + '".') });
    });
    var dos = c.dos || D.today(), unsent = ['edits', 'ready'].indexOf(c.stage) >= 0, age = D.daysBetween(dos, D.today());
    if (unsent && /medicaid/i.test(payer) && age >= 80) out.push({ id: 'DEN-TF', lvl: 'amb', label: 'Filing deadline', text: 'TX Medicaid timely filing ends ' + D.us(D.addDays(dos, 95)) + '. Send this claim now.' });
    else if (unsent && age >= 330) out.push({ id: 'DEN-TF', lvl: 'amb', label: 'Filing deadline', text: 'Most dental plans need claims within 12 months of the date of service (' + D.us(D.addDays(dos, 365)) + '). Send this claim now.' });
    return out;
  };

  // re-run checks; a claim before submission lands in Edits or Ready
  D.scrub = function (c) {
    var res = c.resolved || {};
    c.edits = D.checkClaim(c).filter(function (x) { return !res[x.id]; });
    c.passed = 6 + D.allRules().length - c.edits.length;
    (c.edits).forEach(function (x) { c.ruleHits = c.ruleHits || {}; c.ruleHits[x.rule || x.id] = true; });
    if (c.stage === 'edits' || c.stage === 'ready') c.stage = c.edits.length ? 'edits' : 'ready';
    return c;
  };
  D.history = function (c, text) { (c.hist = c.hist || []).push({ at: new Date().toISOString(), text: text }); };

  D.totals = function () {
    var t = {}; ['edits', 'ready', 'submitted', 'paid', 'denied'].forEach(function (s) { var l = D.stage(s); t[s] = { n: l.length, amt: D.sum(l, D.charge) }; });
    var month = D.today().slice(0, 7), inMonth = S.claims.filter(function (c) { return (c.dos || '').slice(0, 7) === month; });
    t.billed = D.sum(inMonth, D.charge); t.count = inMonth.length; t.all = S.claims.length;
    var paidMonth = S.claims.filter(function (c) { return c.stage === 'paid' && (c.paidOn || '').slice(0, 7) === month; });
    t.collected = D.sum(paidMonth, function (c) { return c.paid; }); t.paidN = paidMonth.length;
    var since = D.addDays(D.today(), -90), recent = S.claims.filter(function (c) { return c.submittedOn && c.submittedOn >= since; });
    t.r90 = recent.length; t.d90 = recent.filter(function (c) { return c.everDenied; }).length;
    var decided = S.claims.filter(function (c) { return c.appealOutcome; }); t.decided = decided.length; t.overturned = decided.filter(function (c) { return c.appealOutcome === 'Overturned'; }).length;
    var paid = S.claims.filter(function (c) { return c.stage === 'paid' && c.submittedOn; }); t.avgPay = paid.length ? D.sum(paid, function (c) { return D.daysBetween(c.submittedOn, c.paidOn); }) / paid.length : null;
    t.firstPass = recent.length ? recent.filter(function (c) { return !c.everDenied && !c.hadEdits; }).length / recent.length : 0;
    return t;
  };
  window.D = D;
})();
