/* v4: the AI and billing tools from the previous EMR, brought into the new one.
   - Claim check: acceptance likelihood (0 to 100) with flags and missing items, from /api/validate-claim
   - CMS-1500: the claim as the paper form, printable
   - Sending: the 837P transmission steps, then the server's claim gate (/api/billing/claims/transmit) decides sent or held
   - AI appeal letter (/api/appeal-letter), Code a Note (paste a note, get codes, check, make a claim)
   - Sample clinic: example patients, a visit and claims so every screen can be tried */
(function () {
  var E = U.esc, M = U.money, S = D.S;

  var desc = function (code) { var f = S.fees.filter(function (x) { return x.code === code; })[0]; return f ? f.desc : ''; };
  // the claim in the shape the server's claim gate and claim check read
  V.claimSnap = function (c) {
    var p = D.pt(c.mrn) || {}, e = c._enc || D.enc(c.encId), prov = D.staff(c.prov) || D.staff(S.clinic.userStaff) || D.providers()[0] || {};
    var ec = e ? e.codes : [], dsc = function (code) { var x = ec.filter(function (y) { return y.code === code; })[0]; return (x && x.desc) || desc(code); };
    return { claimId: c.id, patient: p.name || '', mrn: c.mrn, dob: p.dob || '', sex: p.sex || '', provider: prov.name || '', npi: prov.npi || '', payer: D.payer(c), svcDate: c.dos, pos: c.pos || '11',
      addrStreet: p.addr || '', addrPhone: p.phone || '', insuredId: p.member || '',
      cpts: c.lines.map(function (l) { var x = { code: l.code, description: dsc(l.code), type: D.DENTAL && /^D\d{4}$/.test(l.code) ? 'CDT' : 'CPT', modifier: l.mod || '', units: l.units || 1, charge: D.lineFee(l) };
        if (D.DENTAL) { x.tooth = l.tooth || ''; x.surface = l.surface || ''; x.quad = l.quad || ''; } return x; }),
      icds: c.dx.map(function (d) { return { code: d, description: dsc(d), type: 'ICD-10' }; }),
      totalAmt: D.charge(c), note: e ? D.noteText(e) : (c.note || ''), billing: { name: S.clinic.name, npi: S.clinic.npi, ein: S.clinic.ein, addr: S.clinic.addr, city: S.clinic.city, phone: S.clinic.phone } };
  };

  // a signed claim is approved: it goes to Althais's queue and is submitted with the end-of-day batch (or sooner with Submit Now)
  V.queueClaim = function (c, after) {
    if (!c || c.stage !== 'ready' || c.imported || !c.approvedAt) return Promise.resolve(null);   // only a claim a provider signed and approved
    var snap = V.claimSnap(c); snap.signedAt = c.created || new Date().toISOString();
    return fetch('/api/billing/claims/queue', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ claim: snap }) })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
        if (!j) return null;
        if (j.status === 'queued') { c.queuedAt = new Date().toISOString(); c.submitAt = j.submitAt; c.queueKey = V.checkKey(c); D.history(c, 'Approved and queued for Althais’s end-of-day submission'); }
        if (j.status === 'already_sent') { c.stage = 'submitted'; c.ref = j.reference; }
        D.save(); if (after) after(j); return j;
      }).catch(function () { return null; });
  };
  // Althais's end-of-day batch: 23:00 UTC unless the server says otherwise (billing_activation.BATCH_HOUR_UTC)
  V.nextBatch = function () { var n = new Date(), at = new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate(), 23, 0, 0)); if (at <= n) at.setUTCDate(at.getUTCDate() + 1); return at.toISOString(); };
  V.batchTime = function (iso) { var d = new Date(iso || V.nextBatch()); return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); };

  /* ---------- claim check: likelihood of acceptance ---------- */
  V.scoreColor = function (s) { return s >= 85 ? 'grn' : s >= 65 ? 'amb' : 'blue'; };
  var PERMANENT = /plan or role|Log in/;
  function checkOnce(c) {
    var snap = V.claimSnap(c);
    var ac = window.AbortController ? new AbortController() : null, tm = ac && setTimeout(function () { ac.abort(); }, 30000);   // a hung request is retried, never waited on forever
    return fetch('/api/validate-claim', { method: 'POST', credentials: 'same-origin', signal: ac ? ac.signal : undefined, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines: D.DENTAL ? snap.cpts : undefined, dob: D.DENTAL ? snap.dob : undefined, dos: D.DENTAL ? snap.svcDate : undefined, history: D.DENTAL ? D.dentalHistory(c) : undefined,
        codes: snap.cpts.concat(snap.icds).map(function (x) { return { code: x.code, description: x.description, type: x.type }; }),
        note: 'CLAIM: patient ' + (snap.patient || 'not on file') + ', DOB ' + (snap.dob || 'not on file') + ', sex ' + (snap.sex || 'not on file') + ', payer ' + (snap.payer || 'not on file') + ', member ID ' + (snap.insuredId || 'not on file') + ', rendering provider ' + (snap.provider || 'not on file') + ', date of service ' + (snap.svcDate || 'not on file') + ', place of service 11.\n\n' + snap.note }) })
      .then(function (r) { clearTimeout(tm); return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j, s: r.status }; }); }, function () { clearTimeout(tm); throw new Error('retry'); })
      .then(function (x) {
        if (x.s === 403) throw new Error('Your plan or role doesn’t include the claim check.');
        if (x.s === 401) throw new Error('Log in again to run the claim check.');
        if (x.s === 429) throw new Error('rate');
        if (!x.ok || x.j.error || x.j.score == null) throw new Error('retry');
        return x.j;
      });
  }
  // the claim check always answers: it retries by itself (1s, 2s, 4s, 8s…) until the server responds
  V.claimCheck = function (c, opts) {
    var maxTries = (opts && opts.tries) || Infinity;
    return new Promise(function (resolve, reject) {
      (function attempt(n) {
        checkOnce(c).then(function (j) {
          c.check = { score: Math.max(0, Math.min(100, Number(j.score) || 0)), flags: j.flags || [], missing: j.missing || [], at: new Date().toISOString() };
          D.history(c, 'Claim check: ' + c.check.score + '% likely to be accepted'); D.save(); resolve(c.check);
        }).catch(function (e) {
          if (PERMANENT.test(e.message)) return reject(e);
          if (n + 1 >= maxTries) return reject(new Error(e.message === 'rate' ? 'The AI service is at its usage limit right now.' : 'The claim check didn’t answer.'));
          setTimeout(function () { attempt(n + 1); }, e.message === 'rate' ? 60000 : Math.min(15000, 1000 * Math.pow(2, n)));   // at the AI usage limit: wait a minute
        });
      })(0);
    });
  };
  V.checkHtml = function (c, busy, err) {
    var k = c.check;
    var head = '<div class="dp-h">Claim check <span class="end"><button class="mini" data-runcheck="' + c.id + '"' + (busy ? ' disabled' : '') + '>' + (busy ? 'Checking…' : k ? 'Re-check' : 'Run claim check') + '</button></span></div>';
    if (err && PERMANENT.test(err)) return '<div class="dp-sec">' + head + '<div class="s-red">' + E(err) + '</div></div>';
    if (err) busy = true;
    if (!k) return '<div class="dp-sec">' + head + '<div class="mut">' + (busy ? 'Checking the claim… it updates here by itself.' : D.DENTAL ? 'Runs on its own. Checks tooth numbers, surfaces, quadrants, same-tooth conflicts, frequency and age limits, attachments and required fields before you send.' : 'Runs on its own. Checks medical necessity, E/M level support, modifiers, specificity, required fields and NCCI bundling before you send.') + '</div></div>';
    var col = V.scoreColor(k.score);
    return '<div class="dp-sec">' + head +
      '<div style="display:flex;align-items:center;gap:12px"><b class="s-' + col + '" style="font-size:22px;font-weight:600">' + k.score + '%</b><div style="flex:1"><div style="height:6px;background:var(--b2)"><div style="height:6px;width:' + k.score + '%;background:var(--' + col + ')"></div></div>' +
      '<div class="mut" style="font-size:12px;margin-top:3px">' + (k.score >= 85 ? 'Ready To Submit' : k.score >= 65 ? 'Usable, Some Risk' : 'Not Ready') + ' · Checked ' + new Date(k.at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) + '</div></div></div>' +
      (k.missing.length ? '<div class="ok-list" style="margin-top:6px">' + k.missing.map(function (m) { return '<div class="e">' + U.err + 'Missing: ' + E(m) + '</div>'; }).join('') + '</div>' : '') +
      (k.flags.length ? '<div class="ok-list">' + k.flags.map(function (f) { return '<div class="w">' + U.warn + E(f) + '</div>'; }).join('') + '</div>' : '') + '</div>';
  };
  // the claim check runs by itself: once per version of a claim's codes and note (a re-run only when they change)
  V.checkKey = function (c) { var e = D.enc(c.encId); return c.lines.map(function (l) { return l.code + (l.mod || '') + 'x' + (l.units || 1) + (l.tooth || l.surface || l.quad ? '#' + (l.tooth || '') + (l.surface || '') + (l.quad || '') : ''); }).join(',') + '|' + c.dx.join(',') + '|' + D.payer(c) + '|' + (e ? D.noteText(e) : c.note || '').length; };
  V.autoCheck = function (c, after) {
    if (!c || !c.lines.length || checking[c.id] || (checkErr[c.id] && PERMANENT.test(checkErr[c.id]))) return;
    if (['edits', 'ready'].indexOf(c.stage) < 0 && c.check) return;   // sent claims keep their last result
    var key = V.checkKey(c); if (c.check && c.checkKey === key) return;
    checking[c.id] = true;
    V.claimCheck(c).then(function () { c.checkKey = key; D.save(); }).catch(function (e) { checkErr[c.id] = e.message; }).then(function () { delete checking[c.id]; (after || V.refresh)(); });
  };
  // any page: buttons with data-runcheck run the check and redraw
  var checking = {}, checkErr = {};
  V.checkState = function (id) { return { busy: !!checking[id], err: checkErr[id] || '' }; };
  U.on(document.body, '[data-runcheck]', 'click', function (b) {
    var c = D.claim(b.dataset.runcheck); if (!c) return;
    checking[c.id] = true; delete checkErr[c.id]; V.refresh();
    V.claimCheck(c).then(function () { c.checkKey = V.checkKey(c); D.save(); }).catch(function (e) { checkErr[c.id] = e.message; }).then(function () { delete checking[c.id]; V.refresh(); });
  });

  /* ---------- CMS-1500 (02-12), laid out box for box like the NUCC form ---------- */
  V.cms1500 = function (c) {
    var s = V.claimSnap(c), p = D.pt(c.mrn) || {}, prov = D.staff(c.prov) || {}, loc = S.locations[0] || {};
    var X = function (on) { return '<i class="cx">' + (on ? 'X' : '') + '</i>'; };
    var v = function (t, cls) { return '<span class="cv' + (cls ? ' ' + cls : '') + '">' + (t == null || t === '' ? '&nbsp;' : E(t)) + '</span>'; };
    var L = function (n, t, sub) { return '<span class="cl">' + (n ? '<b>' + n + '.</b> ' : '') + t + (sub ? ' <em>' + sub + '</em>' : '') + '</span>'; };
    var dt = function (iso) { if (!iso) return ['', '', '']; var d = iso.split('-'); return [d[1], d[2], d[0].slice(2)]; };
    var dob = dt(s.dob), dos = dt(s.svcDate), today = dt(D.today());
    var adr = String(p.addr || '').split(/\s*,\s*/), street = adr[0] || '', city = adr.length > 2 ? adr[1] : '', stz = (adr[adr.length > 2 ? 2 : 1] || '').split(/\s+/), st = adr.length > 1 ? stz[0] || '' : '', zip = adr.length > 1 ? stz.slice(1).join(' ') : '';
    var payer = s.payer || '', kind = /^medicare/i.test(payer) ? 'mcr' : /medicaid/i.test(payer) ? 'mcd' : /tricare/i.test(payer) ? 'tri' : /champva/i.test(payer) ? 'chv' : payer && payer !== 'Self-pay' ? 'grp' : 'oth';
    var nm = String(s.patient || '').toUpperCase(), phone = String(p.phone || '').replace(/\D/g, ''), ph = [phone.slice(0, 3), phone.slice(3, 6) + (phone.length > 6 ? '-' + phone.slice(6) : '')];
    var cpts = s.cpts.slice(0, 6), icd = s.icds.slice(0, 12), ptr = icd.slice(0, 4).map(function (_, k) { return String.fromCharCode(65 + k); }).join('');
    var money = function (n) { var t = Number(n || 0).toFixed(2).split('.'); return '<span class="cv">' + t[0] + '</span><span class="cv cc">' + t[1] + '</span>'; };
    var line = function (l) {
      if (!l) return '<tr class="sl"><td colspan="13" class="shade">&nbsp;</td><td class="shade"></td><td class="shade"></td></tr><tr class="sl2">' + '<td></td>'.repeat(13) + '<td class="npi">NPI</td><td></td></tr>';
      return '<tr class="sl"><td colspan="13" class="shade">&nbsp;</td><td class="shade"></td><td class="shade"></td></tr><tr class="sl2">' +
        [dos[0], dos[1], dos[2], dos[0], dos[1], dos[2]].map(function (x) { return '<td>' + v(x) + '</td>'; }).join('') + '<td>' + v(s.pos) + '</td><td></td><td>' + v(l.code) + '</td><td>' + v((l.modifier || '').split(/[\s,]+/).join(' ')) + '</td><td>' + v(ptr) + '</td>' +
        '<td class="amt">' + money(l.charge) + '</td><td>' + v(l.units) + '</td><td class="npi">NPI</td><td>' + v(s.npi) + '</td></tr>';
    };
    var html = '<div class="c15" id="cms1500form">' +
      '<div class="c15-title"><b>HEALTH INSURANCE CLAIM FORM</b><span>APPROVED BY NATIONAL UNIFORM CLAIM COMMITTEE (NUCC) 02/12</span></div>' +
      '<div class="c15-pica"><span>PICA</span><span>PICA</span></div>' +
      '<div class="c15-grid">' +
        // 1 / 1a
        '<div class="c span2 r1">' + L('1', '') + '<div class="types">' + [['mcr', 'MEDICARE', 'Medicare#'], ['mcd', 'MEDICAID', 'Medicaid#'], ['tri', 'TRICARE', 'ID#/DoD#'], ['chv', 'CHAMPVA', 'Member ID#'], ['grp', 'GROUP HEALTH PLAN', 'ID#'], ['feca', 'FECA BLK LUNG', 'ID#'], ['oth', 'OTHER', 'ID#']].map(function (t) { return '<span>' + X(kind === t[0]) + '<b>' + t[1] + '</b><em>(' + t[2] + ')</em></span>'; }).join('') + '</div></div>' +
        '<div class="c">' + L('1a', 'INSURED’S I.D. NUMBER', '(For Program in Item 1)') + v(s.insuredId) + '</div>' +
        // 2 / 3 / 4
        '<div class="c">' + L('2', 'PATIENT’S NAME', '(Last Name, First Name, Middle Initial)') + v(nm) + '</div>' +
        '<div class="c">' + L('3', 'PATIENT’S BIRTH DATE', '') + '<div class="row">' + v(dob[0]) + v(dob[1]) + v(dob[2]) + '<span class="sx">SEX</span> M' + X(s.sex === 'M') + ' F' + X(s.sex === 'F') + '</div></div>' +
        '<div class="c">' + L('4', 'INSURED’S NAME', '(Last Name, First Name, Middle Initial)') + v(nm) + '</div>' +
        // 5 / 6 / 7
        '<div class="c">' + L('5', 'PATIENT’S ADDRESS', '(No., Street)') + v(street.toUpperCase()) + '</div>' +
        '<div class="c">' + L('6', 'PATIENT RELATIONSHIP TO INSURED') + '<div class="row">Self' + X(1) + ' Spouse' + X(0) + ' Child' + X(0) + ' Other' + X(0) + '</div></div>' +
        '<div class="c">' + L('7', 'INSURED’S ADDRESS', '(No., Street)') + v(street.toUpperCase()) + '</div>' +
        // city/state, 8, city/state
        '<div class="c split"><div>' + L('', 'CITY') + v(city.toUpperCase()) + '</div><div class="w25">' + L('', 'STATE') + v(st) + '</div></div>' +
        '<div class="c tall">' + L('8', 'RESERVED FOR NUCC USE') + '</div>' +
        '<div class="c split"><div>' + L('', 'CITY') + v(city.toUpperCase()) + '</div><div class="w25">' + L('', 'STATE') + v(st) + '</div></div>' +
        '<div class="c split"><div>' + L('', 'ZIP CODE') + v(zip) + '</div><div>' + L('', 'TELEPHONE', '(Include Area Code)') + '<div class="row">(' + v(ph[0]) + ')' + v(ph[1]) + '</div></div></div>' +
        '<div class="c split"><div>' + L('', 'ZIP CODE') + v(zip) + '</div><div>' + L('', 'TELEPHONE', '(Include Area Code)') + '<div class="row">(' + v(ph[0]) + ')' + v(ph[1]) + '</div></div></div>' +
        // 9-11
        '<div class="c">' + L('9', 'OTHER INSURED’S NAME', '(Last Name, First Name, Middle Initial)') + v('') + '</div>' +
        '<div class="c">' + L('10', 'IS PATIENT’S CONDITION RELATED TO:') + '</div>' +
        '<div class="c">' + L('11', 'INSURED’S POLICY GROUP OR FECA NUMBER') + v('') + '</div>' +
        '<div class="c">' + L('a', 'OTHER INSURED’S POLICY OR GROUP NUMBER') + v('') + '</div>' +
        '<div class="c">' + L('a', 'EMPLOYMENT?', '(Current or Previous)') + '<div class="row yn">' + X(0) + 'YES ' + X(1) + 'NO</div></div>' +
        '<div class="c">' + L('a', 'INSURED’S DATE OF BIRTH') + '<div class="row">' + v(dob[0]) + v(dob[1]) + v(dob[2]) + '<span class="sx">SEX</span> M' + X(s.sex === 'M') + ' F' + X(s.sex === 'F') + '</div></div>' +
        '<div class="c">' + L('b', 'RESERVED FOR NUCC USE') + '</div>' +
        '<div class="c">' + L('b', 'AUTO ACCIDENT?') + '<div class="row yn">' + X(0) + 'YES ' + X(1) + 'NO <span class="sx">PLACE (State)</span>' + v('') + '</div></div>' +
        '<div class="c">' + L('b', 'OTHER CLAIM ID', '(Designated by NUCC)') + v('') + '</div>' +
        '<div class="c">' + L('c', 'RESERVED FOR NUCC USE') + '</div>' +
        '<div class="c">' + L('c', 'OTHER ACCIDENT?') + '<div class="row yn">' + X(0) + 'YES ' + X(1) + 'NO</div></div>' +
        '<div class="c">' + L('c', 'INSURANCE PLAN NAME OR PROGRAM NAME') + v(payer.toUpperCase()) + '</div>' +
        '<div class="c">' + L('d', 'INSURANCE PLAN NAME OR PROGRAM NAME') + v('') + '</div>' +
        '<div class="c">' + L('10d', 'CLAIM CODES', '(Designated by NUCC)') + v('') + '</div>' +
        '<div class="c">' + L('d', 'IS THERE ANOTHER HEALTH BENEFIT PLAN?') + '<div class="row yn">' + X(0) + 'YES ' + X(1) + 'NO <em>If yes, complete items 9, 9a, and 9d.</em></div></div>' +
        // 12 / 13
        '<div class="c span2 sig"><div class="ctr"><b>READ BACK OF FORM BEFORE COMPLETING &amp; SIGNING THIS FORM.</b></div>' + L('12', 'PATIENT’S OR AUTHORIZED PERSON’S SIGNATURE', 'I authorize the release of any medical or other information necessary to process this claim. I also request payment of government benefits either to myself or to the party who accepts assignment below.') +
          '<div class="row">SIGNED ' + v('SIGNATURE ON FILE', 'grow') + ' DATE ' + v(today.join('/')) + '</div></div>' +
        '<div class="c sig">' + L('13', 'INSURED’S OR AUTHORIZED PERSON’S SIGNATURE', 'I authorize payment of medical benefits to the undersigned physician or supplier for services described below.') + '<div class="row">SIGNED ' + v('SIGNATURE ON FILE', 'grow') + '</div></div>' +
      '</div>' +
      '<div class="c15-grid c15-phys">' +
        '<div class="c">' + L('14', 'DATE OF CURRENT ILLNESS, INJURY, or PREGNANCY (LMP)') + '<div class="row">' + v('') + v('') + v('') + ' QUAL. ' + v('') + '</div></div>' +
        '<div class="c">' + L('15', 'OTHER DATE') + '<div class="row">QUAL. ' + v('') + v('') + v('') + v('') + '</div></div>' +
        '<div class="c">' + L('16', 'DATES PATIENT UNABLE TO WORK IN CURRENT OCCUPATION') + '<div class="row">FROM ' + v('') + ' TO ' + v('') + '</div></div>' +
        '<div class="c">' + L('17', 'NAME OF REFERRING PROVIDER OR OTHER SOURCE') + v('') + '</div>' +
        '<div class="c"><div class="row">17a. ' + v('') + '</div><div class="row">17b. NPI ' + v('') + '</div></div>' +
        '<div class="c">' + L('18', 'HOSPITALIZATION DATES RELATED TO CURRENT SERVICES') + '<div class="row">FROM ' + v('') + ' TO ' + v('') + '</div></div>' +
        '<div class="c span2">' + L('19', 'ADDITIONAL CLAIM INFORMATION', '(Designated by NUCC)') + v('') + '</div>' +
        '<div class="c">' + L('20', 'OUTSIDE LAB?') + '<div class="row yn">' + X(0) + 'YES ' + X(1) + 'NO <span class="sx">$ CHARGES</span>' + v('') + '</div></div>' +
        '<div class="c span2">' + L('21', 'DIAGNOSIS OR NATURE OF ILLNESS OR INJURY', 'Relate A-L to service line below (24E)') + ' <span class="sx">ICD Ind.</span>' + v('0') +
          '<div class="dx">' + 'ABCDEFGHIJKL'.split('').map(function (k, i) { return '<span>' + k + '. ' + v(icd[i] ? icd[i].code : '') + '</span>'; }).join('') + '</div></div>' +
        '<div class="c"><div>' + L('22', 'RESUBMISSION CODE') + '<div class="row">' + v('') + ' <span class="sx">ORIGINAL REF. NO.</span>' + v('') + '</div></div><div style="border-top:1px solid #000;margin:3px -5px 0;padding:3px 5px 0">' + L('23', 'PRIOR AUTHORIZATION NUMBER') + v(c.auth || '') + '</div></div>' +
      '</div>' +
      '<table class="c15-lines"><thead><tr><th colspan="6">24. A. DATE(S) OF SERVICE<br><span>From MM DD YY &nbsp; To MM DD YY</span></th><th>B.<br>PLACE OF SERVICE</th><th>C.<br>EMG</th><th colspan="2">D. PROCEDURES, SERVICES, OR SUPPLIES<br><span>CPT/HCPCS &nbsp; MODIFIER</span></th><th>E.<br>DIAGNOSIS POINTER</th><th>F.<br>$ CHARGES</th><th>G.<br>DAYS OR UNITS</th><th>I.<br>ID. QUAL.</th><th>J.<br>RENDERING PROVIDER ID. #</th></tr></thead><tbody>' +
        [0, 1, 2, 3, 4, 5].map(function (k) { return line(cpts[k]); }).join('') + '</tbody></table>' +
      '<div class="c15-foot">' +
        '<div class="c f25">' + L('25', 'FEDERAL TAX I.D. NUMBER') + '<div class="row">' + v(S.clinic.ein || '', 'grow') + ' SSN' + X(0) + ' EIN' + X(!!S.clinic.ein) + '</div></div>' +
        '<div class="c f26">' + L('26', 'PATIENT’S ACCOUNT NO.') + v(s.mrn) + '</div>' +
        '<div class="c f27">' + L('27', 'ACCEPT ASSIGNMENT?', '(For govt. claims, see back)') + '<div class="row yn">' + X(1) + 'YES ' + X(0) + 'NO</div></div>' +
        '<div class="c f28">' + L('28', 'TOTAL CHARGE') + '<div class="row">$ ' + money(s.totalAmt) + '</div></div>' +
        '<div class="c f29">' + L('29', 'AMOUNT PAID') + '<div class="row">$ ' + money(c.paid || 0) + '</div></div>' +
        '<div class="c f30">' + L('30', 'Rsvd for NUCC Use') + '</div>' +
        '<div class="c f31">' + L('31', 'SIGNATURE OF PHYSICIAN OR SUPPLIER INCLUDING DEGREES OR CREDENTIALS', '(I certify that the statements on the reverse apply to this bill and are made a part thereof.)') +
          '<div class="row">SIGNED ' + v(String(prov.name || s.provider || '').toUpperCase() + (prov.cred ? ' ' + prov.cred : ''), 'grow') + ' DATE ' + v(dos.join('/')) + '</div></div>' +
        '<div class="c f32">' + L('32', 'SERVICE FACILITY LOCATION INFORMATION') + v(String(loc.name || S.clinic.name || '').toUpperCase()) + v(String(loc.addr || S.clinic.addr || '').toUpperCase()) + '<div class="row">a. ' + v(S.clinic.npi) + ' b. ' + v('') + '</div></div>' +
        '<div class="c f33">' + L('33', 'BILLING PROVIDER INFO &amp; PH #') + ' ' + v(S.clinic.phone || '') + v(String(S.clinic.name || '').toUpperCase()) + v(String((S.clinic.addr || '') + (S.clinic.city ? ', ' + S.clinic.city : '')).toUpperCase()) + '<div class="row">a. ' + v(S.clinic.npi) + ' b. ' + v(S.clinic.taxonomy || '') + '</div></div>' +
      '</div>' +
      '<div class="c15-bot"><span>NUCC Instruction Manual available at: www.nucc.org</span><b>PLEASE PRINT OR TYPE</b><span>APPROVED OMB-0938-1197 FORM 1500 (02-12)</span></div></div>';
    var mw = Math.min(1004, window.innerWidth - 32);
    var w0 = U.modal('CMS-1500, ' + c.id, html, [['Close', '', function () {}], ['Print', 'pri', function () {
      var w = window.open(''); w.document.write('<html><head><title>CMS-1500 ' + E(c.id) + '</title><style>' + CMS_CSS + '@page{size:letter;margin:.3in}body{margin:0}</style></head><body>' + html + '</body></html>'); w.document.close(); setTimeout(function () { w.print(); }, 300); return false;
    }]], { width: mw });
    // fit the whole form on screen: measure it at full size, then scale it down
    var f = w0.querySelector('#cms1500form'), box = w0.querySelector('.mdl');
    w0.style.paddingTop = '2vh'; box.style.maxHeight = '96vh';
    var avail = window.innerHeight * 0.96 - 34 - 48 - 26;
    var z = Math.min(1, (window.innerWidth - 60) / 980, avail / f.offsetHeight); f.style.zoom = z.toFixed(3);
    box.style.width = Math.ceil(980 * z + 28) + 'px';
  };
  var CMS_CSS = '.c15{font:9px Arial,Helvetica,sans-serif;color:#000;background:#fff;border:0;width:980px}' +
    '.c15-title{display:flex;flex-direction:column}.c15-title b{font-size:17px}.c15-title span{font-size:8.5px;color:#333}.c15-pica{display:flex;justify-content:space-between;font-size:8.5px;padding:3px 0 2px;border-bottom:2px solid #000}' +
    '.c15-grid{display:grid;grid-template-columns:37% 25.3% 37.7%;border-left:1px solid #000}.c15-phys{border-top:2px solid #000}' +
    '.c15 .c{border-right:1px solid #000;border-bottom:1px solid #000;padding:2px 5px 3px;min-height:40px;box-sizing:border-box}.c15 .span2{grid-column:span 2}.c15 .tall{grid-row:span 2}' +
    '.c15 .split{display:flex;padding:0}.c15 .split>div{flex:1;padding:2px 5px 3px}.c15 .split>div+div{border-left:1px solid #000}.c15 .w25{flex:0 0 28%!important}' +
    '.c15 .cl{display:block;font-size:8.5px;line-height:1.25}.c15 .cl b{font-weight:700}.c15 .cl em{font-style:normal;font-size:7.5px;color:#333}' +
    '.c15 .cv{display:inline-block;min-width:24px;background:#e3edf9;font:11px "Courier New",monospace;font-weight:600;color:#0b2a6f;padding:1px 4px;margin:2px 3px 0 0;min-height:14px;vertical-align:middle}.c15 .cv.grow{min-width:55%}.c15 .cv.cc{min-width:16px}' +
    '.c15 .row{display:flex;align-items:center;flex-wrap:wrap;gap:2px;font-size:8.5px}.c15 .sx{margin:0 4px 0 8px;font-size:8.5px}' +
    '.c15 .cx{display:inline-block;width:13px;height:11px;background:#e3edf9;margin:0 3px;font:bold 10px Arial;color:#0b2a6f;text-align:center;line-height:11px;font-style:normal;vertical-align:middle}' +
    '.c15 .types{display:flex;justify-content:space-between;margin-top:2px}.c15 .types span{display:flex;flex-direction:column;align-items:flex-start;font-size:8.5px}.c15 .types b{font-weight:400;font-size:9.5px}.c15 .types em{font-size:7.5px;font-style:italic}' +
    '.c15 .sig{min-height:58px}.c15 .ctr{text-align:center;font-size:9px;margin-bottom:2px}' +
    '.c15 .dx{display:grid;grid-template-columns:repeat(4,1fr);gap:1px 8px;margin-top:3px}.c15 .dx span{font-size:9px}.c15 .dx .cv{min-width:70%}' +
    '.c15-lines{width:100%;border-collapse:collapse;border-left:1px solid #000}.c15-lines th{font-size:8px;font-weight:400;text-align:center;border-right:1px solid #000;border-bottom:1px solid #000;padding:2px;vertical-align:bottom}.c15-lines th span{font-size:7px}' +
    '.c15-lines td{border-right:1px solid #000;padding:0 2px;font-size:8px;text-align:center}.c15-lines tr.sl td{background:#f2f6fc;height:9px;border-bottom:1px dashed #9aa}.c15-lines tr.sl2 td{border-bottom:1px solid #000;height:19px}.c15-lines td .cv{margin:1px 0;min-width:0;font-size:10.5px}' +
    '.c15-lines td.npi{font-size:8px;font-weight:700}.c15-lines td.amt{white-space:nowrap}' +
    '.c15-foot{display:grid;grid-template-columns:20% 8% 18% 16% 16% 22%;border-left:1px solid #000}.c15-foot .f31{grid-column:span 2;min-height:70px}.c15-foot .f32{grid-column:span 2}.c15-foot .f33{grid-column:span 2}' +
    '.c15-bot{display:flex;justify-content:space-between;font-size:9px;padding-top:4px;border-top:2px solid #000}.c15-bot b{font-style:italic}';
  var st = document.createElement('style'); st.textContent = CMS_CSS; document.head.appendChild(st);

  /* ---------- sending: 837P steps, then the server's claim gate ---------- */
  V.transmit = function (list, done) {
    list = list.filter(function (c) { D.scrub(c); return c.stage === 'ready'; });
    if (!list.length) { U.toast('Nothing ready to send. Resolve the edits first.'); return; }
    var steps = ['Building ANSI X12 ' + D.L.edi, 'Validating segments and loops', 'Running the claim gate', 'Transmitting to the clearinghouse', 'Waiting for the 999 acknowledgment'];
    var w = U.modal('Sending ' + list.length + ' claim' + (list.length === 1 ? '' : 's'), '<div id="tx-steps"></div><pre id="tx-edi" class="mono" style="margin:10px 0 0;padding:8px;background:var(--ph);border:1px solid var(--b2);max-height:150px;overflow:auto;font-size:11px;white-space:pre-wrap"></pre><div id="tx-res"></div>', [['Close', '', function () { done && done(); }]], { wide: 1 });
    var c0 = list[0], s0 = V.claimSnap(c0), d = D.today().replace(/-/g, '');
    w.querySelector('#tx-edi').textContent = ['ISA*00*          *00*          *ZZ*ALTHAIS        *ZZ*CLEARINGHOUSE  *' + d.slice(2) + '*1200*^*00501*' + c0.id.replace(/\D/g, '').slice(-9) + '*0*T*:~', 'GS*HC*ALTHAIS*CLEARINGHOUSE*' + d + '*1200*1*X*' + (D.DENTAL ? '005010X224A2' : '005010X222A1') + '~', 'ST*837*0001*' + (D.DENTAL ? '005010X224A2' : '005010X222A1') + '~',
      'NM1*85*2*' + (S.clinic.name || '').toUpperCase() + '*****XX*' + (S.clinic.npi || '') + '~', 'NM1*IL*1*' + s0.patient.toUpperCase().replace(', ', '*') + '****MI*' + s0.insuredId + '~', 'CLM*' + c0.id + '*' + s0.totalAmt.toFixed(2) + '***' + (s0.pos || '11') + ':B:1*Y*A*Y*Y~',
      (s0.icds.length ? 'HI*' + s0.icds.map(function (x, i) { return (i ? 'ABF:' : 'ABK:') + x.code.replace('.', ''); }).join('*') + '~' : '')].filter(Boolean).concat(s0.cpts.map(function (l, i) {
        return D.DENTAL ? 'LX*' + (i + 1) + '~\nSV3*AD:' + l.code + '*' + l.charge.toFixed(2) + '**' + ({ UR: '10', UL: '20', LL: '30', LR: '40', UA: '01', LA: '02' }[l.quad] || '') + '**' + l.units + '~' + (l.tooth ? '\nTOO*JP*' + l.tooth + (l.surface ? '*' + l.surface.split('').join(':') : '') + '~' : '')
          : 'LX*' + (i + 1) + '~\nSV1*HC:' + l.code + (l.modifier ? ':' + l.modifier : '') + '*' + l.charge.toFixed(2) + '*UN*' + l.units + '***1~'; })).join('\n');
    var paint = function (n) { w.querySelector('#tx-steps').innerHTML = steps.map(function (t, i) { return '<div style="padding:3px 0" class="' + (i < n ? 's-grn' : i === n ? '' : 'mut') + '">' + (i < n ? U.I.check : i === n ? '<span class="dot" style="background:var(--blue);margin-right:7px"></span>' : '<span style="display:inline-block;width:14px"></span>') + E(t) + '</div>'; }).join(''); };
    var n = 0; paint(0);
    var tick = setInterval(function () { if (n < 2) paint(++n); }, 450);
    var results = [];
    (function next(i) {
      if (i >= list.length) {
        clearInterval(tick); paint(steps.length);
        var sent = results.filter(function (r) { return r.res.status === 'sent'; }), held = results.filter(function (r) { return r.res.status === 'held'; }), bad = results.filter(function (r) { return ['sent', 'held'].indexOf(r.res.status) < 0; });
        w.querySelector('#tx-res').innerHTML = '<div class="dp-h" style="margin-top:12px">Result</div>' + results.map(function (r) {
          var st = r.res.status;
          return '<div style="padding:5px 0;border-bottom:1px solid var(--b2)"><b class="mono">' + r.c.id + '</b> ' + E(D.ptName(r.c.mrn)) + ' · ' + M(D.charge(r.c)) + ' · ' +
            (st === 'sent' ? '<span class="s-grn">Sent' + (r.res.test ? ' (test mode)' : '') + (r.res.reference ? ', ref ' + E(r.res.reference) : '') + '</span>' +
              (r.res.attachments ? (r.res.attachments.ok ? ' <span class="s-grn">· ' + r.res.attachments.count + ' document' + (r.res.attachments.count === 1 ? '' : 's') + ' sent</span>' : '<div class="s-red" style="font-size:12px">The documents weren’t sent: ' + E(r.res.attachments.message || '') + ' Open the claim and use Send Documents Again.</div>') : '') : st === 'held' ? '<span class="s-amb">Held, not sent</span>' + (r.res.blockers || []).map(function (b) { return '<div class="mut" style="font-size:12px">' + E(b.title || '') + ': ' + E(b.reason || '') + (b.next ? ' · ' + E(b.next) : '') + '</div>'; }).join('') : '<span class="s-red">Not sent: ' + E(r.res.message || r.res.detail || 'error') + '</span>') + '</div>';
        }).join('') + (held.length ? '<div class="mut" style="margin-top:6px">Held claims stay in Ready To Submit. Finish the items above in <a href="/revenue/billing-activation">Billing activation</a> and send again.</div>' : '');
        w.querySelector('.mdl-h').firstChild.textContent = sent.length + ' sent, ' + held.length + ' held' + (bad.length ? ', ' + bad.length + ' not sent' : '');
        D.log('Sent ' + sent.length + ' claim(s), ' + held.length + ' held'); D.save(); if (done) done();
        return;
      }
      var c = list[i];
      fetch('/api/billing/claims/transmit', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ claim: V.claimSnap(c) }) })
        .then(function (r) { return r.json().then(function (j) { return r.ok ? j : { status: 'error', message: r.status === 403 ? (j.detail || 'Only billers and clinic admins can send claims.') : (j.detail || j.error || 'The claim could not be checked.') }; }); })
        .catch(function () { return { status: 'error', message: 'Could not reach the server.' }; })
        .then(function (res) {
          if (res.status === 'sent' || res.status === 'already_sent') { c.stage = 'submitted'; c.status = res.test ? 'Submitted (test)' : 'Submitted ' + D.us(D.today(), 1).slice(0, 5); c.submittedOn = D.today(); c.ref = res.reference || ''; D.history(c, 'Sent' + (res.test ? ' in test mode' : '') + (res.reference ? ', ref ' + res.reference : '') + (res.attachments ? (res.attachments.ok ? ', with ' + res.attachments.count + ' document(s)' : '; documents not sent') : '')); res.status = 'sent'; if (V.docsChanged) V.docsChanged(c.id); }
          else if (res.status === 'held') { c.heldReasons = res.blockers || []; D.history(c, 'Held by the claim gate: ' + (res.blockers || []).map(function (b) { return b.title; }).join('; ')); }
          else D.history(c, 'Not sent: ' + (res.message || 'error'));
          results.push({ c: c, res: res }); next(i + 1);
        });
    })(0);
  };

  /* ---------- AI appeal letter ---------- */
  V.aiLetter = function (c) {
    var s = V.claimSnap(c);
    return fetch('/api/appeal-letter', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patient: s.patient, mrn: s.mrn, claimId: c.id, svcDate: D.us(c.dos), provider: s.provider, payer: s.payer, cpt: s.cpts.map(function (x) { return D.DENTAL ? DX.lineText(x) : x.code; }).join(', '), icd: s.icds.map(function (x) { return x.code; }).join(', '), amount: M(s.totalAmt), status: 'Denied ' + (c.carc || '') + ' ' + (c.reason || ''), practice: S.clinic.name }) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok || j.error) throw new Error(r.status === 403 ? 'Your plan or role doesn’t include appeal drafting.' : 'Appeal drafting is unavailable right now.'); return j.letter; }); });
  };

  /* ---------- Code a Note ---------- */
  V.pages.codenote = function () {
    var app = V.app, st = V.cn || (V.cn = { note: '', codes: [], busy: false, err: '', check: null, mrn: '' });
    function draw() {
      var acc = st.codes.filter(function (c) { return c.on; });
      app.innerHTML = U.header('codenote') + U.toolbar('Code a Note', [['Get codes', 'check', { pri: 1, id: 'go' }], ['Check claim', 'refresh', { id: 'chk' }], ['Create claim', 'plus', { id: 'mk' }], '|', ['Clear', 'x', { id: 'clr' }]]) +
        '<div class="main" style="grid-template-columns:minmax(0,1fr) 470px">' +
          '<section class="pane"><div class="ph">Visit note <span class="sub">paste or type, then Get codes</span></div><div class="pbody" style="padding:10px"><textarea class="ed" id="cn" style="height:100%;min-height:400px" placeholder="' + (D.DENTAL ? 'Exam findings, teeth and surfaces treated, X-rays taken, anesthetic, what was done and next steps' : 'Chief complaint, HPI, exam, assessment and plan') + '">' + E(st.note) + '</textarea></div></section>' +
          '<section class="pane"><div class="ph">Codes <span class="sub">' + (st.busy ? 'reading the note…' : st.codes.length + ' suggested') + '</span></div><div class="pbody">' +
            (st.err ? '<div class="banner-note s-red">' + E(st.err) + '</div>' : '') +
            U.grid(['', 'Code', 'Description', ['Conf.', 'num']], st.codes.map(function (c, i) { return { cells: ['<input type="checkbox" data-cc="' + i + '"' + (c.on ? ' checked' : '') + '>', '<b>' + E(c.code) + '</b>' + (c.tooth || c.surface || c.quad ? ' <span class="mono">' + E((c.tooth ? '#' + c.tooth : '') + (c.surface ? ' ' + c.surface : '') + (c.quad ? ' ' + c.quad : '')) + '</span>' : '') + '<div class="mut" style="font-size:11px">' + c.type + '</div>', '<div class="wrap">' + E(c.desc) + '</div><div class="mut wrap" style="font-size:11.5px">' + E(c.why || '') + '</div>' + (c.gap ? '<div class="s-amb wrap" style="font-size:11.5px">' + U.warn + E(c.gap) + '</div>' : ''), '<b class="s-' + (c.conf < 80 ? 'amb' : 'grn') + '">' + c.conf + '%</b>'] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(4, 'Codes appear here.') + '</tbody>') +
            (st.check ? (function () { var k = st.check, col = V.scoreColor(k.score); return '<div class="dp-sec"><div class="dp-h">Claim check</div><b class="s-' + col + '" style="font-size:22px;font-weight:600">' + k.score + '%</b>' + k.missing.map(function (m) { return '<div class="s-red">' + U.err + 'Missing: ' + E(m) + '</div>'; }).join('') + k.flags.map(function (f) { return '<div class="s-amb">' + U.warn + E(f) + '</div>'; }).join('') + '</div>'; })() : '') +
            '<div class="dp-sec"><div class="dp-h">Make it a claim</div>' + U.fields([{ k: 'mrn', label: 'Patient', type: 'select', options: V.ptOptions(), blank: 'Choose a patient', span: 4 }], { mrn: st.mrn }) + '<div class="mut" style="margin-top:4px">' + acc.length + ' code' + (acc.length === 1 ? '' : 's') + ' checked will go on the claim.</div></div>' +
          '</div></section></div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    app.addEventListener('input', function (e) { if (e.target.id === 'cn') st.note = e.target.value; });
    app.addEventListener('change', function (e) { if (e.target.dataset.cc != null) st.codes[+e.target.dataset.cc].on = e.target.checked; if (e.target.dataset.k === 'mrn') st.mrn = e.target.value; });
    U.on(app, '#clr', 'click', function () { V.cn = null; st = V.cn = { note: '', codes: [], busy: false, err: '', check: null, mrn: '' }; draw(); });
    U.on(app, '#go', 'click', function () {
      if (st.note.trim().length < 20) return U.toast('Paste a note first.');
      st.busy = true; st.err = ''; st.check = null; draw();
      fetch('/api/code-note', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: st.note, encounter_type: 'office', specialty: D.DENTAL ? 'general dentistry' : 'primary care / outpatient office' }) })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok || j.error) throw new Error(r.status === 403 ? 'Your plan or role doesn’t include coding.' : 'Coding is unavailable right now.'); return j; }); })
        .then(function (j) { st.codes = (j.codes || []).map(function (c) { return { code: c.code, desc: c.description || '', type: /CDT/i.test(c.type || '') ? 'CDT' : /CPT/i.test(c.type || '') ? 'CPT' : 'ICD-10', tooth: c.tooth || '', surface: c.surface || '', quad: c.quad || '', conf: Number(c.confidence) || 0, why: c.justification || '', gap: c.documentation_gap || '', on: true }; }); })
        .catch(function (e) { st.err = e.message; }).then(function () { st.busy = false; draw(); });
    });
    var temp = function () { var acc = st.codes.filter(function (c) { return c.on; }); return { id: 'CHECK', encId: '', mrn: st.mrn, dos: D.today(), prov: '', lines: acc.filter(function (c) { return D.isProc(c.type); }).map(function (c) { return D.lineOf({ code: c.code, units: 1, tooth: c.tooth, surface: c.surface, quad: c.quad }); }), dx: acc.filter(function (c) { return !D.isProc(c.type); }).map(function (c) { return c.code; }), note: st.note, hist: [] }; };
    U.on(app, '#chk', 'click', function () {
      if (!st.codes.length) return U.toast('Get codes first.');
      var c = temp(); st.busy = true; draw();
      var realSave = D.save; D.save = function () {};   // a check here isn't saved anywhere
      V.claimCheck(c).then(function (k) { st.check = k; }).catch(function (e) { st.err = e.message; }).then(function () { D.save = realSave; st.busy = false; draw(); });
    });
    U.on(app, '#mk', 'click', function () {
      if (!st.mrn) return U.toast('Choose the patient.');
      var t = temp(); if (!t.lines.length) return U.toast('Check at least one ' + D.L.proc + ' code.');
      var c = { id: D.nextClaimId(), encId: '', mrn: st.mrn, dos: D.today(), prov: (V.myProvider() || {}).id || '', payer: (D.pt(st.mrn) || {}).payer, lines: t.lines, dx: t.dx, note: st.note, stage: 'edits', resolved: {}, created: new Date().toISOString(), check: st.check };
      D.scrub(c); D.history(c, 'Created in Code a Note'); S.claims.push(c); D.log('Claim from Code a Note: ' + c.id); D.save(); location.href = V.claimHref(c.id);
    });
  };

  /* ---------- sample clinic ----------
     A full urgent care day for demos and for trying every screen: today's schedule, a visit in progress, notes that trip
     each payer rule, and claims in every stage (edits, ready and approved, with the payer, rejected, paid, denied,
     appealed). Everything is marked sample: it stays in this browser and is never sent to the server. Reset puts the
     same patients, visits and claims back with today's dates, so every demo begins the same way. */
  V.loadSample = function (quiet) {
    if (D.DENTAL) return V.loadDentalSample(quiet);   // the dental clinic's sample day (app10.js)
    if (S.patients.some(function (p) { return p.sample; })) return quiet ? null : U.toast('The sample clinic is already loaded. Use Reset to start it over.');
    var T = D.today(), add = function (n) { return D.addDays(T, n); }, me = V.myProvider(), now = new Date().toISOString();
    var staff = function (name, cred, provider, access) { var x = { id: D.id('S'), name: name, cred: cred, provider: provider, status: 'Active', access: access, creds: [], reminders: [], training: [], sample: true }; S.staff.push(x); return x.id; };
    var doc = me ? me.id : staff('Morgan, Alex', 'MD', true, 'Provider');
    var np = staff('Rivera, Elena', 'FNP-C', true, 'Provider');
    staff('Patel, Sam', 'CPC', false, 'Biller');
    staff('Nguyen, Kim', '', false, 'Front desk');

    // name, dob, sex, payer, member ID, allergies, problems, meds, provider
    var P = {
      robert: ['Sample, Robert', '1958-05-03', 'M', 'Medicare B', 'SMP-1EG4TE5MK72', 'Sulfa (rash)', 'E11.22 T2DM with diabetic CKD; I10 Hypertension; N18.31 CKD stage 3a', 'Metformin 500 mg BID\nAmlodipine 5 mg daily\nLosartan 50 mg daily', doc],
      barbara: ['Sample, Barbara', '1961-11-22', 'F', 'Aetna PPO', 'SMP-W288104417', '', 'E11.9 Type 2 diabetes', 'Metformin 1000 mg BID', doc],
      marcus: ['Sample, Marcus', '1970-11-30', 'M', 'UHC', 'SMP-912204558', '', 'I10 Hypertension', 'Lisinopril 20 mg daily', doc],
      ashley: ['Sample, Ashley', '1990-07-30', 'F', 'Cigna', 'SMP-U77120394', 'Penicillin (hives)', '', '', np],
      thomas: ['Sample, Thomas', '1972-08-16', 'M', 'UHC', 'SMP-913302871', '', 'M25.561 Right knee pain', 'Ibuprofen 600 mg PRN', doc],
      linda: ['Sample, Linda', '1979-02-14', 'F', 'Cigna', 'SMP-U77341200', '', '', '', np],
      jose: ['Sample, Jose', '2003-09-09', 'M', 'TX Medicaid', 'SMP-512339087', '', '', '', np],
      grace: ['Sample, Grace', '1985-04-27', 'F', 'Aetna PPO', 'SMP-W288177320', '', 'F41.1 Generalized anxiety disorder', 'Sertraline 50 mg daily', doc],
      daniel: ['Sample, Daniel', '1994-12-03', 'M', 'BCBS TX', 'SMP-XOD889120345', '', '', '', np],
      olivia: ['Sample, Olivia', '1949-06-18', 'F', 'Medicare Adv.', 'SMP-H554019832', 'Codeine (nausea)', 'E11.9 Type 2 diabetes; N39.0 Recurrent UTI', 'Metformin 500 mg daily', doc],
      ethan: ['Sample, Ethan', '2001-03-22', 'M', 'Aetna PPO', '', '', '', '', np],
      priya: ['Sample, Priya', '1983-10-05', 'F', 'BCBS TX', 'SMP-XOD889155021', '', '', '', doc],
    };
    var m = {};
    Object.keys(P).forEach(function (k) { var x = P[k]; var p = { mrn: D.nextMrn(x[0]), name: x[0], dob: x[1], sex: x[2], payer: x[3], member: x[4], allergies: x[5], problems: x[6], meds: x[7], pcp: x[8], balance: k === 'ethan' ? 45 : 0, status: 'Active', created: add(-120), eligChecked: T, sample: true }; S.patients.push(p); m[k] = p.mrn; });

    var enc = function (k, o) { var e = Object.assign({ id: D.id('E'), mrn: m[k], date: T, prov: P[k][8], visitType: 'Office visit', status: 'Signed', signedAt: now, signedBy: S.clinic.user, sample: true, vitals: {}, transcript: [], orders: [], codes: [], addenda: [], ignored: {} }, o); S.encs.push(e); return e; };
    // the visit in progress: the doctor writes, Althea codes live (Medicare CKD rule)
    var eRobert = enc('robert', { status: 'Draft', signedAt: '', signedBy: '', reason: 'Diabetes and blood pressure follow-up',
      s: '68M returns for diabetes and blood pressure follow-up. Home BP readings in the 150s despite daily amlodipine. Fasting glucose 140 to 160. Mild ankle swelling in the evenings.',
      o: 'BP 152/94, HR 72. Trace bilateral ankle edema. A1c 7.8%, eGFR 58, urine albumin/creatinine 64.', a: '1. Type 2 diabetes with diabetic CKD, not at goal.\n2. Hypertension, uncontrolled on two agents.\n3. CKD stage 3a, stable.',
      p: 'Increase losartan to 100 mg daily. Start empagliflozin 10 mg. Repeat BMP in 2 weeks. Return in 6 weeks.', vitals: { bp: '152/94', hr: '72' },
      transcript: [{ t: '10:36 AM', text: 'My blood pressure at home has been running around 150, 155 most mornings.' }, { t: '10:39 AM', text: 'Your kidney number, the eGFR, was 58 last month, about the same as spring.' }],
      orders: [{ type: 'Rx', name: 'Losartan 100 mg tab', detail: '1 daily, #90, 1 refill', dx: 'I12.9', status: 'Pending' }, { type: 'Lab', name: 'Basic metabolic panel', detail: 'Due in 2 weeks', dx: 'N18.31', status: 'Pending' }] });
    // Aetna diabetes visit: foot exam said out loud, not written down (AET-DM-014 finds it in the transcript)
    var eBarbara = enc('barbara', { reason: 'Diabetes follow-up', s: 'Diabetes check. Home sugars in the 140s. No hypoglycemia. Taking metformin as prescribed.', o: 'BP 138/84, HR 76. A1c 7.2%.', a: 'Type 2 diabetes, improving.', p: 'Continue metformin. A1c in 3 months.', vitals: { bp: '138/84', hr: '76' },
      transcript: [{ t: '8:41 AM', text: 'Let me check your feet. Can you feel this? Good, both sides. No sores, and the pulses are strong.' }] });
    // UHC 99214 with thin decision making (UHC-EM-022)
    var eMarcus = enc('marcus', { reason: 'Blood pressure check', s: 'BP check. Feels well.', o: 'BP 134/82.', a: 'Hypertension.', p: 'Continue lisinopril.', vitals: { bp: '134/82' } });
    // Cigna preventive plus a problem visit, no modifier 25 (CIG-MD-007)
    var eLinda = enc('linda', { reason: 'Annual physical, sore throat', s: 'Annual physical. Also 3 days of sore throat.', o: 'Normal exam. Pharynx red, no exudate. Rapid strep negative.', a: '1. Routine adult exam.\n2. Acute pharyngitis, viral.', p: 'Age-appropriate screening ordered. Supportive care for throat.' });
    // Aetna telehealth with no patient location written (AET-TH-031)
    var eGrace = enc('grace', { visitType: 'Telehealth', reason: 'Anxiety follow-up (video)', s: 'Video visit. Anxiety better on sertraline, sleeping well.', o: 'Calm, normal speech on video.', a: 'Generalized anxiety disorder, improving.', p: 'Continue sertraline 50 mg. Follow up in 3 months.',
      transcript: [{ t: '10:04 AM', text: 'I\'m at home today, in my kitchen.' }] });
    var eAshley = enc('ashley', { reason: 'Cough and congestion', s: '3 days of cough and congestion, no fever.', o: 'Lungs clear. No distress.', a: 'Acute upper respiratory infection.', p: 'Supportive care. Return if fever or shortness of breath.' });

    var appt = function (k, date, time, visit, o) { S.appts.push(Object.assign({ id: D.id('A'), mrn: m[k], date: date, time: time, prov: P[k][8], reason: '', type: 'Office visit', visit: visit, sample: true }, o)); };
    appt('ashley', T, '08:00', 'Checked out', { reason: 'Cough, congestion', encId: eAshley.id, room: 'Exam 1' });
    appt('barbara', T, '08:30', 'Checked out', { reason: 'Diabetes follow-up', encId: eBarbara.id, room: 'Exam 2' });
    appt('marcus', T, '09:00', 'Checked out', { reason: 'BP check', encId: eMarcus.id, room: 'Exam 3' });
    appt('linda', T, '09:30', 'Checked out', { reason: 'Annual physical', encId: eLinda.id, room: 'Exam 1' });
    appt('grace', T, '10:00', 'Checked out', { reason: 'Anxiety follow-up', type: 'Telehealth', encId: eGrace.id });
    appt('robert', T, '10:30', 'In room', { reason: 'DM, HTN follow-up', encId: eRobert.id, room: 'Exam 2' });
    appt('olivia', T, '11:00', 'Checked in', { reason: 'Burning with urination' });
    appt('ethan', T, '11:30', 'No show', { reason: 'Sore throat' });
    appt('daniel', T, '13:30', 'Scheduled', { reason: 'Ankle recheck' });
    appt('priya', T, '14:00', 'Scheduled', { reason: 'Follow-up, chest pain' });
    appt('thomas', T, '15:00', 'Scheduled', { reason: 'Knee pain' });
    appt('jose', add(1), '09:00', 'Scheduled', { reason: 'Suture removal' });
    appt('marcus', add(1), '10:30', 'Scheduled', { reason: 'Lab review' });
    appt('grace', add(2), '16:00', 'Scheduled', { reason: 'Follow-up (video)', type: 'Telehealth' });

    var mk = function (k, cpt, dx, stage, extra) {
      var c = Object.assign({ id: D.nextClaimId(), mrn: m[k], dos: T, prov: P[k][8], payer: P[k][3], lines: cpt.map(function (x) { var l = typeof x === 'string' ? { code: x } : x; return { code: l.code, mod: l.mod || '', units: 1 }; }), dx: dx, stage: stage, resolved: {}, created: now, sample: true }, extra || {});
      if (stage === 'edits' || stage === 'ready') D.scrub(c);
      D.history(c, 'Sample claim'); S.claims.push(c); return c;
    };
    // edits: each one trips a different payer rule, with the evidence and the fix
    mk('barbara', ['99214'], ['E11.9'], 'edits', { encId: eBarbara.id, hadEdits: true });
    mk('marcus', ['99214'], ['I10'], 'edits', { encId: eMarcus.id, hadEdits: true });
    mk('linda', ['99396', '99213'], ['Z00.00', 'J02.9'], 'edits', { encId: eLinda.id, hadEdits: true });
    mk('grace', ['99213'], ['F41.1'], 'edits', { encId: eGrace.id, hadEdits: true });
    mk('jose', ['12002', '99283'], ['S61.411A', 'W26.0XXA'], 'edits', { dos: add(-82), amount: 412, hadEdits: true });
    // ready, one already approved for tonight's batch
    mk('ashley', ['99213'], ['J06.9'], 'ready', { encId: eAshley.id });
    var dq = mk('daniel', ['99213', '73610'], ['S93.401A'], 'ready', { dos: add(-1), amount: 236, queuedAt: now, submitAt: V.nextBatch() });
    if (V.checkKey) dq.queueKey = V.checkKey(dq);
    // with the payer
    mk('priya', ['99214', '93000'], ['R07.9'], 'submitted', { dos: add(-3), submittedOn: add(-2), status: 'Accepted 277CA' });
    mk('thomas', ['99213'], ['M25.561'], 'submitted', { dos: add(-39), submittedOn: add(-38), status: 'Accepted 277CA' });
    mk('robert', ['99214'], ['E11.22', 'N18.31'], 'submitted', { dos: add(-6), submittedOn: add(-5), status: 'Rejected 277CA: subscriber ID not found' });
    // paid this month
    mk('ashley', ['99213'], ['R10.9'], 'paid', { dos: add(-10), submittedOn: add(-9), paidOn: add(-1), paid: 92.4 });
    mk('daniel', ['99212'], ['J02.9'], 'paid', { dos: add(-18), submittedOn: add(-17), paidOn: add(-6), paid: 61.15 });
    mk('olivia', ['99214', '81002'], ['N39.0'], 'paid', { dos: add(-25), submittedOn: add(-24), paidOn: add(-9), paid: 134.7 });
    mk('grace', ['99203'], ['F41.1'], 'paid', { dos: add(-30), submittedOn: add(-29), paidOn: add(-12), paid: 104.1 });
    // earlier this quarter, paid on first pass
    [['priya', '99213', 'R51.9', -70, -48, 91.3], ['olivia', '99214', 'E11.9', -64, -41, 129.6], ['jose', '99283', 'S93.401A', -55, -33, 168.4], ['robert', '99214', 'I10', -50, -27, 131.2],
      ['barbara', '99213', 'E11.9', -44, -22, 94.8], ['daniel', '99213', 'J06.9', -38, -19, 90.1], ['ashley', '99212', 'R05.9', -33, -16, 60.7]].forEach(function (x) {
      mk(x[0], [x[1]], [x[2]], 'paid', { dos: add(x[3]), submittedOn: add(x[3] + 1), paidOn: add(x[4]), paid: x[5] });
    });
    // denied, appealed, overturned
    mk('thomas', ['73721'], ['M25.561'], 'denied', { dos: add(-12), submittedOn: add(-10), deniedOn: add(-3), carc: 'CO-197', reason: 'Prior authorization absent', appealBy: add(24), dstatus: 'Needs review', everDenied: true });
    mk('ethan', ['99213', '87880'], ['J02.9'], 'denied', { dos: add(-20), submittedOn: add(-19), deniedOn: add(-8), carc: 'CO-16', reason: 'Missing member ID', appealBy: add(52), dstatus: 'Needs review', everDenied: true, amount: 151 });
    mk('linda', ['99214'], ['J01.90'], 'denied', { dos: add(-45), submittedOn: add(-44), deniedOn: add(-30), carc: 'CO-50', reason: 'Not medically necessary', appealBy: add(30), dstatus: 'Appealed', everDenied: true, appealedOn: add(-20),
      basis: 'Symptoms for 12 days with facial pain and fever documented.\nExam findings support the visit level.', letter: '' });
    mk('marcus', ['99213'], ['I10'], 'paid', { dos: add(-60), submittedOn: add(-59), paidOn: add(-14), paid: 88.2, everDenied: true, carc: 'CO-16', reason: 'Missing referring provider', appealOutcome: 'Overturned' });
    D.log('Sample clinic loaded'); D.save();
    if (!quiet) { U.toast('Sample clinic loaded: 12 patients, today\'s schedule, a visit in progress and 25 claims. Reset or remove it in Settings › Security and data.'); V.refresh(); }
  };
  V.clearSample = function (quiet) {
    ['patients', 'appts', 'encs', 'claims'].forEach(function (k) { S[k] = S[k].filter(function (x) { return !x.sample; }); });
    S.staff = S.staff.filter(function (x) { return !x.sample; }); D.log('Sample clinic removed'); D.save();
    if (!quiet) { V.refresh(); U.toast('Sample clinic removed.'); }
  };
  // start a demo over: the sample clinic exactly as it was first loaded (dates move to today)
  V.resetSample = function () { V.clearSample(true); V.loadSample(true); U.toast('Sample clinic reset. Ready for a demo.'); V.refresh(); };
})();

/* ---------- Althea builds the claim: iterates until the claim check is 90% or better; the provider approves once ---------- */
(function () {
  var E = U.esc, M = U.money, S = D.S, TARGET = 90;
  var post = function (url, body) { var ac = window.AbortController ? new AbortController() : null, tm = ac && setTimeout(function () { ac.abort(); }, 45000);
    return fetch(url, { method: 'POST', credentials: 'same-origin', signal: ac ? ac.signal : undefined, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (r) { clearTimeout(tm); return r.json().then(function (j) { if (!r.ok || j.error) throw new Error(r.status === 403 ? 'Your plan or role doesn’t include this.' : r.status === 401 ? 'Log in again.' : (j.error || 'Unavailable right now.')); return j; }); }); };
  var SEC = { subjective: 's', objective: 'o', assessment: 'a', plan: 'p' };
  // the working copy: codes, note additions (each with its quoted source), and a note that includes them
  function workEnc(base, adds) {
    var w = Object.assign({}, base, { transcript: base.transcript || [] });
    adds.forEach(function (a) { var k = SEC[a.section] || 'a'; w[k] = (w[k] ? String(w[k]).replace(/\s+$/, '') + '\n' : '') + a.text; });
    return w;
  }
  function workClaim(ctx, codes, adds) {
    var cpt = codes.filter(function (c) { return D.isProc(c.type); }), dx = codes.filter(function (c) { return !D.isProc(c.type); });
    return { id: ctx.claim ? ctx.claim.id : 'Draft', encId: ctx.enc ? ctx.enc.id : '', _enc: ctx.enc ? workEnc(ctx.enc, adds) : null, mrn: ctx.mrn, dos: ctx.dos, prov: ctx.prov, payer: ctx.payer,
      lines: cpt.map(function (c) { return { code: c.code, mod: c.mod || '', units: c.units || 1 }; }), dx: dx.map(function (c) { return c.code; }), note: ctx.enc ? '' : (ctx.note || '') + adds.map(function (a) { return '\n' + a.text; }).join(''), resolved: {}, stage: 'edits', hist: [] };
  }
  function contextFor(target) {
    if (target.enc) { var e = target.enc, p = D.pt(e.mrn) || {}, made = S.claims.filter(function (c) { return c.encId === e.id; })[0];
      var cur = target.codes || (e.codes.some(function (c) { return c.state === 'acc'; }) ? e.codes.filter(function (c) { return c.state === 'acc'; }) : e.codes.filter(function (c) { return c.state !== 'rej'; }));
      return { enc: e, claim: made, mrn: e.mrn, dos: e.date, prov: e.prov, payer: p.payer, codes: cur.map(function (c) { return { code: c.code, type: c.type, desc: c.desc, mod: c.mod || '', units: 1, reason: 'From the visit coding' }; }) }; }
    var c = target.claim, e2 = D.enc(c.encId);
    return { enc: e2, claim: c, mrn: c.mrn, dos: c.dos, prov: c.prov, payer: D.payer(c), note: c.note || '',
      codes: c.lines.map(function (l) { return { code: l.code, type: 'CPT', desc: '', mod: l.mod || '', units: l.units || 1, reason: 'On the claim' }; }).concat(c.dx.map(function (d) { return { code: d, type: 'ICD-10', desc: '', reason: 'On the claim' }; })) };
  }

  // the loop: code (if needed), apply payer-rule fixes found in the record, check, improve from the documentation, repeat
  V.optimize = function (target, say) {
    say = say || function () {};
    var ctx = contextFor(target), codes = ctx.codes.slice(), adds = [], best = null;
    var transcript = ctx.enc ? (ctx.enc.transcript || []).filter(function (l) { return l.src !== 'command'; }).map(function (l) { return l.text; }).join('\n') : '';
    function ruleAdds() {
      D.checkClaim(workClaim(ctx, codes, adds)).forEach(function (x) {
        if (x.addendum && x.evidence && !adds.some(function (a) { return a.rule === x.id; })) adds.push({ section: x.id === 'MCR-CKD-003' ? 'assessment' : 'objective', text: x.addendum, source_quote: x.evidence.quote, rule: x.id, why: x.label + ' (' + x.id + ')' });
      });
    }
    // a claim always carries a visit code: if coding returned none, use the office visit level for this kind of visit
    function ensureEM() {
      if (codes.some(function (c) { return D.isProc(c.type); })) return;
      var newPt = ctx.enc && /new patient/i.test(ctx.enc.visitType || ''), tele = ctx.enc && /telehealth/i.test(ctx.enc.visitType || '');
      var code = newPt ? '99203' : '99213';
      codes.unshift({ code: code, type: 'CPT', desc: newPt ? 'Office visit, new patient, low MDM' : 'Office visit, established patient, low MDM', mod: tele ? '95' : '', units: 1, reason: 'Office visit for this ' + (newPt ? 'new' : 'established') + ' patient; the level is confirmed against the note by the claim check' });
      say('Added the visit code ' + code);
    }
    function check() { ensureEM(); var c = workClaim(ctx, codes, adds); var save = D.save; D.save = function () {}; return V.claimCheck(c, { tries: 2 }).then(function (k) { D.save = save; return k; }, function (e) { D.save = save; throw e; }); }
    function snapshot(k) { return { score: k.score, check: k, codes: JSON.parse(JSON.stringify(codes)), adds: JSON.parse(JSON.stringify(adds)), ctx: ctx }; }
    // fast path: one server call codes, builds and checks the claim (official ICD-10-CM, combination codes, E/M by MDM)
    var snap0 = V.claimSnap(workClaim(ctx, codes, adds)), noteNow = ctx.enc ? D.noteText(ctx.enc) : (ctx.note || '');
    var header = 'patient ' + (snap0.patient || 'not on file') + ', DOB ' + (snap0.dob || 'not on file') + ', sex ' + (snap0.sex || 'not on file') + ', payer ' + (snap0.payer || 'not on file') + ', member ID ' + (snap0.insuredId || 'not on file') + ', rendering provider ' + (snap0.provider || 'not on file') + ', date of service ' + (snap0.svcDate || 'not on file') + ', place of service 11';
    var fast = !String(noteNow).trim() ? Promise.resolve(null) : (say('Coding and building the claim', 'mut'), post('/api/claim-auto', { note: noteNow, transcript: transcript, header: header,
        visit: ctx.enc && ctx.enc.visitType ? 'office visit, ' + ctx.enc.visitType : 'office visit, established patient', codes: (target.pin || (ctx.enc ? [] : codes)).map(function (c) { return { code: c.code, type: c.type, modifier: c.mod || '' }; }) })
      .then(function (r) {
        var nc = (r.codes || []).filter(function (c) { return c.code; });
        if (!nc.some(function (c) { return /CPT/i.test(c.type); })) return null;
        codes = nc.map(function (c) { return { code: String(c.code).toUpperCase(), type: /CPT/i.test(c.type) ? 'CPT' : 'ICD-10', desc: c.description || '', mod: c.modifier || '', units: c.units || 1, reason: c.reason || '' }; });
        say('Codes: ' + codes.map(function (c) { return c.code + (c.mod ? '-' + c.mod : ''); }).join(', '));
        (r.rule_notes || []).forEach(function (t) { say('Coding rule: ' + t); });
        (r.note_additions || []).forEach(function (a) { adds.push(a); say('Note addition (' + a.section + '), from: “' + a.source_quote + '”'); });
        ctx.unresolved = r.unresolved || [];
        ruleAdds();
        var k = { score: Math.max(0, Math.min(100, Number(r.check && r.check.score) || 0)), flags: (r.check && r.check.flags) || [], missing: (r.check && r.check.missing) || [], at: new Date().toISOString() };
        say('Claim check: ' + k.score + '% (' + ((r.ms || 0) / 1000).toFixed(1) + 's)', k.score >= TARGET ? '' : 'w');
        best = snapshot(k);
        return k;
      }).catch(function () { return null; }));
    var p0 = fast.then(function (k) { if (k) throw { done: k }; }).then(function () { return codes.length ? null : (ctx.enc ? (say('Reading the note for codes', 'mut'), post('/api/code-note', { note: D.noteText(ctx.enc), encounter_type: 'office', payer: ctx.payer || '' }).then(function (j) {
      codes = (j.codes || []).map(function (c) { return { code: c.code, type: /CPT/i.test(c.type || '') ? 'CPT' : 'ICD-10', desc: c.description || '', mod: c.modifier || '', units: c.units || 1, reason: c.justification || '' }; }); say('Coded the visit: ' + codes.map(function (c) { return c.code; }).join(', '));
    })) : Promise.reject(new Error('This claim has no codes.'))); });
    return p0.then(function () { ruleAdds(); if (adds.length) say('Payer rules: ' + adds.length + ' fix' + (adds.length === 1 ? '' : 'es') + ' found in the visit record'); say('Running the claim check', 'mut'); return check(); })
      .catch(function (x) { if (x && x.done) return x.done.score >= TARGET ? { _best: true } : Object.assign(x.done, { _n: 3 }); throw x; })
      .then(function round(k, n) {
        if (k && k._best) return best;
        n = n || 1; if (!best || k.score > best.score) best = snapshot(k);
        say('Claim check, pass ' + n + ': ' + k.score + '%', k.score >= TARGET ? '' : 'w');
        if (k.score >= TARGET || n >= 4) return best;
        say('Improving from the documentation', 'mut');
        return post('/api/claim-build', { note: D.noteText(workClaim(ctx, codes, adds)._enc || { s: ctx.note, o: '', a: '', p: '' }), transcript: transcript, payer: ctx.payer || '',
          codes: codes.map(function (c) { return { code: c.code, type: c.type, modifier: c.mod }; }), flags: k.flags, missing: k.missing, edits: D.checkClaim(workClaim(ctx, codes, adds)).map(function (x) { return x.id + ': ' + x.text; }) })
          .then(function (r) {
            var nc = (r.codes || []).filter(function (c) { return c.code && c.format_valid !== false; });
            if (nc.some(function (c) { return D.isProc(c.type); })) {
              var before = codes.map(function (c) { return c.code + (c.mod ? '-' + c.mod : ''); }).join(', ');
              codes = nc.map(function (c) { return { code: String(c.code).toUpperCase(), type: c.type, desc: c.description || '', mod: c.modifier || '', units: c.units || 1, reason: c.reason || '' }; });
              var after = codes.map(function (c) { return c.code + (c.mod ? '-' + c.mod : ''); }).join(', ');
              if (before !== after) say('Codes: ' + before + ' → ' + after);
            }
            (r.note_additions || []).forEach(function (a) { if (!adds.some(function (x) { return x.text === a.text; })) { adds.push(a); say('Note addition (' + a.section + '), from: “' + a.source_quote + '”'); } });
            if (r.dropped_additions) say(r.dropped_additions + ' addition' + (r.dropped_additions === 1 ? '' : 's') + ' dropped: no matching quote in the record', 'w');
            ctx.unresolved = r.unresolved || [];
            ruleAdds();
            return check().then(function (k2) { return round(k2, n + 1); });
          });
      })
      .then(function (b) { b.unresolved = ctx.unresolved || []; return b; }, function (e) {
        if (best) { best.unresolved = ctx.unresolved || []; best.error = e.message; return best; }
        if (codes.length) return { score: null, check: null, codes: codes, adds: adds, ctx: ctx, unresolved: [], error: e.message };   // checks unavailable: still keep the codes and record-based fixes
        throw e; });
  };
  V.withAdds = workEnc;

  V.buildClaim = function (target, onDone) {
    var log = [], ctx, codes, adds;
    var w = U.modal('Althea is building the claim', '<div id="ab-log" class="ok-list"></div><div id="ab-res"></div>', [['Close', '', function () { if (onDone) onDone(); }]], { wide: 1 });
    var say = function (t, cls) { log.push([t, cls || '']); w.querySelector('#ab-log').innerHTML = log.map(function (l) { return '<div class="' + l[1] + '">' + (l[1] === 'w' ? U.warn : l[1] === 'mut' ? '' : U.I.check) + E(l[0]) + '</div>'; }).join(''); };
    var original = contextFor(target).codes.map(function (c) { return c.code + (c.mod ? '-' + c.mod : ''); }).join(', ');
    V.optimize(target, say).then(function (b) { ctx = b.ctx; show(b); }).catch(function (e) { say(e.message, 'w'); });

    function show(b) {
      codes = b.codes; adds = b.adds; ctx.unresolved = b.unresolved; var k = b.check, ok = k.score >= TARGET, col = V.scoreColor(k.score);
      var edits = D.checkClaim(workClaim(ctx, codes, adds)).filter(function (x) { return !/^BASIC-FEE/.test(x.id); });
      var who = (D.staff(ctx.prov) || {}).name || S.clinic.user;
      w.querySelector('.mdl-h').firstChild.textContent = ok ? 'Althea built the claim' : 'Althea’s best version of the claim';
      w.querySelector('#ab-res').innerHTML =
        '<div class="dp-sec" style="padding-left:0;padding-right:0"><b class="s-' + col + '" style="font-size:26px;font-weight:600">' + k.score + '%</b> <span class="mut">' + (ok ? 'Ready To Submit · Checked' : 'Not Ready · ' + TARGET + '% needs what’s listed under Still needs you') + '</span>' +
          '<div class="mut" style="font-size:12px">Started as ' + E(original || 'no codes') + '</div></div>' +
        '<div class="dp-h">Codes on the claim</div>' + U.grid(['Code', 'Type', 'Mod', 'Why'], codes.map(function (c) { return { cells: ['<b>' + E(c.code) + '</b>', c.type, E(c.mod || ''), '<span class="wrap">' + E(c.reason || c.desc || '') + '</span>'] }; }), { tight: 1 }) +
        '<div class="dp-h" style="margin-top:10px">Note additions <span class="sub">each quotes the visit record; added when you approve</span></div>' +
          (adds.length ? adds.map(function (a) { return '<div class="box"><div class="box-h"><span>' + E(a.section) + (a.why ? ' · ' + E(a.why) : '') + '</span></div><div class="box-b prop">' + E(a.text) + '<div class="mut" style="font-size:11.5px;margin-top:3px">Source: “' + E(a.source_quote) + '”</div></div></div>'; }).join('') : '<div class="mut">None needed.</div>') +
        (k.missing.length || k.flags.length || edits.length || (ctx.unresolved || []).length ? '<div class="dp-h" style="margin-top:10px">Still needs you</div><div class="ok-list">' +
          edits.map(function (x) { return '<div class="e">' + U.err + E(x.label + ': ' + x.text) + '</div>'; }).join('') + (ctx.unresolved || []).concat(k.missing).map(function (m) { return '<div class="e">' + U.err + E(m) + '</div>'; }).join('') + k.flags.map(function (f) { return '<div class="w">' + U.warn + E(f) + '</div>'; }).join('') + '</div>' : '') +
        '<div class="mut" style="margin-top:10px;font-size:12px">Approving ' + (ctx.enc && ctx.enc.status !== 'Signed' ? 'signs the note with these additions as ' + E(who) + ', ' : 'adds these to the record as an addendum by ' + E(who) + ', ') + 'puts these codes on the claim and sends it.</div>';
      var f = w.querySelector('.dp-f');
      f.innerHTML = '<button type="button" class="btn" data-ab="close">Not now</button><button type="button" class="btn pri" data-ab="ok">' + (ok ? 'Approve Claim' : 'Approve Anyway') + '</button>';
      f.querySelector('[data-ab=close]').onclick = function () { w.remove(); if (onDone) onDone(); };
      f.querySelector('[data-ab=ok]').onclick = function () { approve(k, who); w.remove(); };
    }
    function approve(k, who) { V.approveBuilt({ ctx: ctx, codes: codes, adds: adds, check: k }, who); }
  };
  V.approveBuilt = function (b, who, after) {
    var ctx = b.ctx, codes = b.codes, adds = b.adds, k = b.check;
    {
      var e = ctx.enc, now = new Date().toISOString();
      if (e) {
        if (e.status !== 'Signed') { adds.forEach(function (a) { var key = SEC[a.section] || 'a'; e[key] = (e[key] ? String(e[key]).replace(/\s+$/, '') + '\n' : '') + a.text; }); e.status = 'Signed'; e.signedAt = now; e.signedBy = who; (e.orders || []).forEach(function (o) { o.status = 'Signed'; }); S.appts.forEach(function (a) { if (a.encId === e.id) a.visit = 'Checked out'; }); }
        else adds.forEach(function (a) { (e.addenda = e.addenda || []).push({ text: a.text, from: 'Althea (source: “' + a.source_quote + '”)', signed: true, signedOn: D.today(), signedBy: who }); });
        e.codes = codes.map(function (c) { return { code: c.code, desc: c.desc || c.code, type: c.type, conf: 90, why: c.reason || '', mod: c.mod || '', state: 'acc' }; });
        e.check = k; e.checkKey = '';
      }
      var c = ctx.claim;
      if (!c) { c = { id: D.nextClaimId(), encId: e ? e.id : '', mrn: ctx.mrn, dos: ctx.dos, prov: ctx.prov, payer: ctx.payer, created: now, resolved: {}, stage: 'edits' }; S.claims.push(c); }
      c.lines = codes.filter(function (x) { return D.isProc(x.type); }).map(function (x) { return { code: x.code, mod: x.mod || '', units: x.units || 1 }; });
      c.dx = codes.filter(function (x) { return !D.isProc(x.type); }).map(function (x) { return x.code; });
      if (!e && adds.length) c.note = (c.note || '') + adds.map(function (a) { return '\n' + a.text; }).join('');
      if (['edits', 'ready'].indexOf(c.stage) < 0) c.stage = 'edits';
      c.resolved = {}; delete c.waitingOn; D.scrub(c); c.check = k; c.checkKey = V.checkKey(c);
      D.history(c, 'Built by Althea (' + k.score + '% claim check), approved by ' + who); D.log('Claim approved by ' + who + ': ' + c.id); D.save();
      delete c.proposal;
      if (c.stage === 'ready') V.transmit([c], function () { (after || V.refresh)(); });   // the send result stays on screen
      else { U.toast('Approved. ' + c.edits.length + ' payer edit(s) still need attention before it can be sent.'); (after || function () { location.href = V.claimHref(c.id); })(); }
    }
  };
})();

/* ---------- claims already made: Althea builds a better version of any claim under 90%, waiting for one approval ---------- */
(function () {
  var E = U.esc, S = D.S, busy = {};
  V.proposalBusy = function (id) { return !!busy[id]; };
  V.proposeFor = function (c, after) {
    if (D.DENTAL || busy[c.id] || !c.lines.length) return;   // Althea's claim builder is medical (E/M, ICD-10); dental claims are checked by the dental rules
    var key = V.checkKey(c); if (c.proposal && c.proposal.key === key) return;
    busy[c.id] = true; (after || V.refresh)();
    V.optimize({ claim: c }).then(function (b) {
      if (b.check) {
        var before = c.lines.map(function (l) { return l.code + (l.mod ? '-' + l.mod : ''); }).concat(c.dx).join(','), afterCodes = b.codes.map(function (x) { return x.code + (x.mod ? '-' + x.mod : ''); }).join(',');
        c.proposal = { key: key, score: b.check.score, check: b.check, codes: b.codes, adds: b.adds, unresolved: b.unresolved, changed: before !== afterCodes || b.adds.length > 0, from: c.check ? c.check.score : null, at: new Date().toISOString() };
      } else c.proposal = null;
      // a claim can't go out without a diagnosis: when it has none, Althea's diagnoses from the note go straight on
      // (the codes the note supports, checked against ICD-10-CM); the claim is then re-checked
      var dxs = (b.codes || []).filter(function (x) { return !D.isProc(x.type); }).map(function (x) { return x.code; });
      if (!c.dx.length && dxs.length && ['edits', 'ready'].indexOf(c.stage) >= 0) {
        c.dx = dxs; D.history(c, 'Diagnoses added by Althea from the visit note: ' + dxs.join(', ')); delete c.check; c.proposal = null; D.scrub(c);
      }
      D.save();
    }).catch(function () { c.proposal = null; }).then(function () { delete busy[c.id]; (after || V.refresh)(); });
  };
  V.proposalHtml = function (c) {
    var pr = c.proposal;
    if (busy[c.id]) return '<div class="dp-sec"><div class="edit" style="border-color:var(--sel);background:var(--tint)"><div class="edit-h" style="border:0">Althea is building this claim to 90% or better…</div></div></div>';
    if (!pr || pr.error || !pr.changed || pr.score <= (c.check ? c.check.score : 0)) return '';
    var col = V.scoreColor(pr.score);
    return '<div class="dp-sec"><div class="edit" style="border-color:var(--sel);background:var(--tint)"><div class="edit-h">Althea built this claim: <span class="s-' + col + '" style="margin-left:4px">' + pr.score + '%</span>' + (pr.from != null ? '<span class="end">was ' + pr.from + '%</span>' : '') + '</div><div class="edit-b">' +
      '<div class="b" style="margin-bottom:3px">Codes</div><div class="mono" style="font-size:12px;margin-bottom:6px">' + E(c.lines.map(function (l) { return l.code + (l.mod ? '-' + l.mod : ''); }).concat(c.dx).join(', ')) + '  →  <b>' + E(pr.codes.map(function (x) { return x.code + (x.mod ? '-' + x.mod : ''); }).join(', ')) + '</b></div>' +
      pr.codes.filter(function (x) { return x.reason; }).map(function (x) { return '<div class="mut" style="font-size:11.5px"><b>' + E(x.code) + '</b>: ' + E(x.reason) + '</div>'; }).join('') +
      (pr.adds.length ? '<div class="b" style="margin:8px 0 3px">Added to the record when you approve</div>' + pr.adds.map(function (a) { return '<div class="box"><div class="box-b prop">' + E(a.text) + '<div class="mut" style="font-size:11.5px;margin-top:3px">Source: “' + E(a.source_quote) + '”</div></div></div>'; }).join('') : '') +
      ((pr.unresolved || []).length ? '<div class="ok-list" style="margin-top:6px">' + pr.unresolved.map(function (u) { return '<div class="e">' + U.err + E(u) + '</div>'; }).join('') + '</div>' : '') +
      '<div style="display:flex;justify-content:flex-end;margin-top:8px"><button class="btn pri" data-approvebuilt="' + c.id + '">Approve Claim</button></div></div></div></div>';
  };
  U.on(document.body, '[data-approvebuilt]', 'click', function (b) {
    var c = D.claim(b.dataset.approvebuilt), pr = c && c.proposal; if (!pr) return;
    var who = (D.staff(c.prov) || {}).name || S.clinic.user;
    var sc = V.scoreColor(pr.score);
    U.modal('Approve Claim', '<div class="so"><div class="so-who">Approving as <b>' + E(who) + '</b> · ' + E(c.id) + ' · ' + E(D.payer(c) || '') + '</div>' +
      '<div class="so-sec"><div class="so-h">Claim</div><div class="so-score"><b class="s-' + sc + '">' + pr.score + '%</b><div class="so-bar"><i style="width:' + pr.score + '%;background:var(--' + sc + ')"></i></div><span>' + (pr.score >= 85 ? 'Ready To Submit' : pr.score >= 65 ? 'Usable, Some Risk' : 'Not Ready') + ' · Checked</span></div>' +
      '<div class="so-codes">' + pr.codes.map(function (x) { return '<div class="so-code"><b>' + E(x.code) + (x.mod ? '-' + E(x.mod) : '') + '</b><span>' + E(x.desc || '') + '</span></div>'; }).join('') + '</div></div>' +
      (pr.adds.length ? '<div class="so-sec"><div class="so-h">Added to the record<span>' + pr.adds.length + '</span></div><div class="so-adds">' + pr.adds.map(function (a) { return '<div class="so-add"><em>' + E(a.section || '') + '</em><div>' + E(a.text) + (a.source_quote ? '<small title="' + E(a.source_quote) + '">“' + E(a.source_quote) + '”</small>' : '') + '</div></div>'; }).join('') + '</div></div>' : '') +
      '<div class="so-foot">The claim is sent once approved.</div></div>', [['Cancel', '', function () {}], ['Approve Claim', 'pri', function () {
    var ctx = { enc: D.enc(c.encId), claim: c, mrn: c.mrn, dos: c.dos, prov: c.prov, payer: D.payer(c), note: c.note || '' };
      V.approveBuilt({ ctx: ctx, codes: pr.codes, adds: pr.adds, check: pr.check }, who, V.refresh);
    }]], { width: 540 });
  });
})();
