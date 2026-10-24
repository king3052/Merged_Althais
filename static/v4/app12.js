/* v4: checking coverage, and the documents that go with a claim. Both run on the server, for medical and dental clinics.
   - Eligibility (eligibility.py, /api/eligibility/check): asks the payer through Althais's clearinghouse. Dental plans
     come back with the annual maximum and what's left, the deductible, what the plan pays per category, frequency limits
     and waiting periods. The answer is kept on the patient (p.eligibility) and shown on the chart and the schedule.
   - Claim documents (claim_attachments.py): X-rays, perio charts, photos and narratives, stored encrypted on the server
     and sent with the claim as one PDF. Once the claim is sent they can't change. */
(function () {
  var E = U.esc, M = U.money, S = D.S;
  var json = function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.detail || j.error || 'Something went wrong.'); return j; }); };
  var post = function (url, body) { return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(json); };

  /* ---------- eligibility ---------- */
  var nameParts = function (n) { n = String(n || '').trim(); if (n.indexOf(',') > 0) { var a = n.split(/\s*,\s*/); return { last: a[0], first: (a[1] || '').split(/\s+/)[0] }; } var b = n.split(/\s+/); return { first: b[0] || '', last: b.slice(1).join(' ') }; };
  V.checkEligibility = function (p, quiet) {
    if (!p) return Promise.resolve(null);
    if (!p.payer || p.payer === 'Self-pay' || !p.member) { if (!quiet) U.toast('Add the payer and ' + (D.DENTAL ? 'subscriber' : 'member') + ' ID first.'); return Promise.resolve(null); }
    var n = nameParts(p.name), sub = p.subscriber || {};
    var body = { payer: p.payer, member_id: p.member, patient_dob: p.dob || '', relationship: sub.relationship || 'self',
      subscriber_first: sub.first || n.first, subscriber_last: sub.last || n.last, subscriber_dob: sub.dob || p.dob || '', service_date: D.today() };
    if (!quiet) U.toast('Checking coverage with ' + p.payer + '…', 'busy');
    return post('/api/eligibility/check', body).then(function (e) {
      if (e.status === 'active' || e.status === 'inactive') { p.eligibility = e; p.eligChecked = D.today(); D.log('Eligibility checked: ' + p.name + ' (' + e.status + ')'); D.save(); }
      if (!quiet) {
        if (e.status === 'active') U.toast('Coverage active with ' + p.payer + (e.dental && e.dental.annual_max_remaining != null ? ' · ' + M(e.dental.annual_max_remaining) + ' of the annual maximum left' : ''), 'ok');
        else if (e.status === 'inactive') U.toast('Coverage is inactive with ' + p.payer + '.', 'warn');
        else if (e.status === 'not_connected') U.confirm('Eligibility Isn’t Connected Yet', '<p class="cf">' + E((e.errors || [])[0] || 'Check coverage with the payer directly.') + '</p><p class="cf">If you checked by phone or on the payer’s website, mark it checked.</p>', 'Mark Checked', function () { p.eligChecked = D.today(); p.eligibility = { status: 'manual', checked_at: new Date().toISOString() }; D.log('Eligibility marked checked: ' + p.name); D.save(); V.refresh(); });
        else U.toast((e.errors || [])[0] || 'The payer didn’t answer. Try again.', 'warn');
      }
      V.refresh(); return e;
    }, function (er) { if (!quiet) U.toast(er.message, 'warn'); return null; });
  };
  var money = function (v) { return v == null ? null : M(v); };
  V.eligHtml = function (p) {
    var e = p && p.eligibility; if (!e || !e.status) return '';
    if (e.status === 'manual') return '<div class="mut" style="margin-top:6px;font-size:12px">Marked checked by hand.</div>';
    var when = e.checked_at ? D.us(e.checked_at.slice(0, 10), 1) : '';
    var head = '<div style="margin-top:8px">' + (e.status === 'active' ? U.st('Active', 'grn') : U.st('Inactive', 'amb')) + (e.plan ? ' <span class="mut">Plan ' + E(e.plan) + '</span>' : '') + (when ? ' <span class="mut">· ' + when + '</span>' : '') + '</div>';
    var d = e.dental;
    if (!d) {
      var kv = []; if (e.copay != null) kv.push(['Copay', M(e.copay)]); if (e.deductible != null) kv.push(['Deductible', M(e.deductible)]); if (e.deductible_remaining != null) kv.push(['Deductible left', M(e.deductible_remaining)]);
      return head + (kv.length ? '<div style="margin-top:4px">' + U.kv(kv) + '</div>' : '');
    }
    var kv2 = [];
    if (d.annual_max != null || d.annual_max_remaining != null) kv2.push(['Annual maximum', (money(d.annual_max_remaining) || '?') + ' left' + (d.annual_max != null ? ' of ' + M(d.annual_max) : ''), d.annual_max_remaining != null && d.annual_max_remaining < 200 ? 'amb' : '']);
    if (d.deductible != null || d.deductible_remaining != null) kv2.push(['Deductible', (money(d.deductible_remaining) || '?') + ' left' + (d.deductible != null ? ' of ' + M(d.deductible) : '')]);
    if (d.family_deductible != null) kv2.push(['Family deductible', (d.family_deductible_remaining != null ? M(d.family_deductible_remaining) + ' left of ' : '') + M(d.family_deductible)]);
    if (d.ortho_lifetime_max != null || d.ortho_remaining != null) kv2.push(['Ortho lifetime max', (d.ortho_remaining != null ? M(d.ortho_remaining) + ' left of ' : '') + money(d.ortho_lifetime_max)]);
    var list = function (title, rows, cls) { return rows && rows.length ? '<div class="dp-h" style="margin-top:8px">' + title + '</div>' + rows.map(function (x) { return '<div class="' + (cls || '') + '" style="font-size:12.5px;padding:1px 0"><b>' + E(x.category) + ':</b> ' + E(x.text) + '</div>'; }).join('') : ''; };
    return head + (kv2.length ? '<div style="margin-top:4px">' + U.kv(kv2) + '</div>' : '') +
      (d.coverage && d.coverage.length ? '<div class="dp-h" style="margin-top:8px">Plan pays</div><div class="kv">' + d.coverage.map(function (x) { return '<div><span>' + E(x.category) + (x.network === 'out' ? ' (out of network)' : '') + '</span><b class="mono">' + x.plan_pays + '%</b></div>'; }).join('') + '</div>' : '') +
      list('Frequency limits', d.limits) + list('Waiting periods', d.waiting_periods, 's-amb') + list('Not covered', d.not_covered, 's-red') + list('Payer notes', (d.notes || []).slice(0, 6), 'mut') +
      '<div class="mut" style="font-size:11.5px;margin-top:6px">As the payer reported it. Benefits aren’t a guarantee of payment.</div>';
  };
  U.on(document.body, '[data-elig]', 'click', function (b, ev) { ev.preventDefault(); b.disabled = true; V.checkEligibility(D.pt(b.dataset.elig)).then(function () { b.disabled = false; }); });

  // Patients › Run Eligibility: every active insured patient, a few at a time
  V.runEligibility = function (list, done) {
    var ok = list.filter(function (p) { return p.member; }), i = 0, n = { active: 0, inactive: 0, error: 0 }, notConnected = false;
    if (!ok.length) { U.toast('No patients with a payer and member ID.'); return; }
    U.toast('Checking ' + ok.length + ' patient' + (ok.length === 1 ? '' : 's') + '…', 'busy');
    (function next() {
      if (notConnected || i >= ok.length) {
        if (notConnected) return U.confirm('Eligibility Isn’t Connected Yet', '<p class="cf">Althais can’t ask payers yet. If you checked these ' + ok.length + ' patients by phone or on the payers’ websites, mark them checked.</p>', 'Mark ' + ok.length + ' Checked', function () { ok.forEach(function (p) { p.eligChecked = D.today(); p.eligibility = { status: 'manual' }; }); D.log('Eligibility marked checked: ' + ok.length + ' patients'); D.save(); if (done) done(); });
        U.toast(n.active + ' active, ' + n.inactive + ' inactive' + (n.error ? ', ' + n.error + ' not answered' : '') + (list.length - ok.length ? ', ' + (list.length - ok.length) + ' missing a member ID' : '') + '.', n.inactive || n.error ? 'warn' : 'ok');
        if (done) done(); return;
      }
      V.checkEligibility(ok[i++], true).then(function (e) {
        if (e && e.status === 'not_connected') notConnected = true; else if (e && (e.status === 'active' || e.status === 'inactive')) n[e.status]++; else n.error++;
        next();
      });
    })();
  };

  /* ---------- claim documents ---------- */
  var cache = {}, loading = {};
  var sentOnServer = function (c) { return ['submitted', 'paid', 'denied'].indexOf(c.stage) >= 0; };
  function load(cid) {
    if (loading[cid]) return loading[cid];
    loading[cid] = fetch('/api/claims/' + encodeURIComponent(cid) + '/attachments', { credentials: 'same-origin' }).then(json).then(function (j) {
      cache[cid] = j; delete loading[cid];
      var c = D.claim(cid); if (c && !c.sample) { var has = j.attachments.length > 0; if (!!c.attached !== has) { c.attached = has; D.scrub(c); D.save(); } }
      paint(cid); return j;
    }, function () { delete loading[cid]; cache[cid] = cache[cid] || { attachments: [], error: true }; paint(cid); });
    return loading[cid];
  }
  function paint(cid) { [].slice.call(document.querySelectorAll('[data-docs="' + cid + '"]')).forEach(function (el) { el.innerHTML = inner(D.claim(cid), cache[cid]); }); }
  function inner(c, j) {
    if (!c) return '';
    if (c.sample) return '<span class="mut">' + (c.attached ? U.I.check + ' Sample: X-rays and narrative on file.' : 'Sample claim: no documents.') + '</span>';
    if (!j) return '<span class="mut">Loading…</span>';
    if (j.error) return '<span class="mut">Couldn’t load the documents. <a href="#" data-docsreload="' + E(c.id) + '">Try again</a></span>';
    var lock = j.locked || sentOnServer(c), can = !lock;
    var rows = j.attachments.map(function (a) {
      return '<div class="att-row" style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--b2)">' +
        '<div style="flex:1;min-width:0"><b>' + E(a.label) + '</b> <span class="mut">' + E(a.caption || a.name || '') + '</span><div class="mut" style="font-size:11.5px">' + a.pages + ' page' + (a.pages === 1 ? '' : 's') + ' · ' + U.size(a.size) +
        (a.status === 'sent' ? ' · <span class="s-grn">Sent ' + D.us(a.sentAt.slice(0, 10), 1) + '</span>' : a.status === 'failed' ? ' · <span class="s-red">Not sent: ' + E(a.message || '') + '</span>' : '') + '</div></div>' +
        '<a class="mini" href="/api/claims/' + encodeURIComponent(c.id) + '/attachments/' + a.id + '" target="_blank" rel="noopener">View</a>' + (can ? '<button class="mini" data-docdel="' + a.id + '" data-docc="' + E(c.id) + '" aria-label="Remove ' + E(a.label) + '">Remove</button>' : '') + '</div>';
    }).join('');
    var failed = j.attachments.some(function (a) { return a.status === 'failed'; });
    return (rows || '<div class="mut">' + (lock ? 'No documents were sent with this claim.' : 'None yet. Crowns, buildups, root canals, scaling and root planing and surgical extractions usually need an X-ray and a narrative.') + '</div>') +
      (can ? '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"><button class="mini pri" data-docadd="' + E(c.id) + '">Add X-ray or File</button><button class="mini" data-docnarr="' + E(c.id) + '">Write Narrative</button></div>' +
        '<div class="mut" style="font-size:11.5px;margin-top:5px">Sent with the claim as one PDF (control number ' + E(j.acn || 'assigned on the first document') + '). JPEG, PNG, HEIC, TIFF or PDF, up to 10 MB each.</div>'
        : failed ? '<div style="margin-top:8px"><button class="mini pri" data-docsend="' + E(c.id) + '">Send Documents Again</button></div>' : '');
  }
  V.claimDocs = function (c) {
    if (!c) return '';
    if (!cache[c.id] && !c.sample) load(c.id);
    return '<div class="dp-sec"><div class="dp-h">X-rays and documents' + (cache[c.id] && cache[c.id].attachments && cache[c.id].attachments.length ? ' <span class="mut">' + cache[c.id].attachments.length + '</span>' : '') + '</div><div data-docs="' + E(c.id) + '">' + inner(c, cache[c.id]) + '</div></div>';
  };
  V.docsChanged = function (cid) { delete cache[cid]; load(cid); };
  var after = function (c, j, text) { cache[c.id] = j; c.attached = j.attachments.length > 0; D.scrub(c); D.history(c, text); D.log(text + ': ' + c.id); D.save(); paint(c.id); V.refresh(); };
  U.on(document.body, '[data-docsreload]', 'click', function (a, ev) { ev.preventDefault(); V.docsChanged(a.dataset.docsreload); });
  U.on(document.body, '[data-docadd]', 'click', function (b) {
    var c = D.claim(b.dataset.docadd); if (!c) return;
    U.pickFile('image/*,.heic,.heif,.tif,.tiff,.pdf,application/pdf', true, function (files) {
      if (!files.length) return;
      var guess = /perio/i.test(files[0].name) ? 'perio' : /narr|letter/i.test(files[0].name) ? 'narrative' : /photo|intraoral|img_/i.test(files[0].name) ? 'photo' : 'xray';
      U.form('Add ' + (files.length === 1 ? files[0].name : files.length + ' Files'), [
        { k: 'kind', label: 'What is it', type: 'select', options: [['xray', 'X-ray'], ['perio', 'Periodontal chart'], ['photo', 'Intraoral photo'], ['report', 'Report'], ['narrative', 'Narrative (scanned)'], ['other', 'Other document']], def: guess },
        { k: 'caption', label: 'Description (printed on the page)', ph: 'Bitewing, #14 distal decay' }], {}, function (v) {
        var i = 0, last = null;
        U.toast('Adding ' + files.length + ' file' + (files.length === 1 ? '' : 's') + '…', 'busy');
        (function next() {
          if (i >= files.length) { if (last) after(c, last, files.length + ' document' + (files.length === 1 ? '' : 's') + ' added'); U.toast((files.length === 1 ? 'Document' : files.length + ' documents') + ' added to ' + c.id + '.', 'ok'); return; }
          var f = files[i++], fd = new FormData(); fd.append('file', f); fd.append('kind', v.kind); fd.append('caption', v.caption || ''); fd.append('patient', D.ptName(c.mrn) || '');
          fetch('/api/claims/' + encodeURIComponent(c.id) + '/attachments', { method: 'POST', credentials: 'same-origin', body: fd }).then(json).then(function (j) { last = j; next(); },
            function (er) { if (last) after(c, last, 'Documents added'); U.toast(f.name + ': ' + er.message, 'warn'); });
        })();
      }, { save: 'Add', width: 480 });
    });
  });
  U.on(document.body, '[data-docnarr]', 'click', function (b) {
    var c = D.claim(b.dataset.docnarr); if (!c) return;
    var lines = (c.lines || []).map(function (l) { return D.DENTAL ? DX.lineText(l) : l.code; }).join(', ');
    U.form('Write Narrative', [{ k: 'text', label: 'Why the treatment was needed', type: 'textarea', rows: 8, req: 1, ph: 'Tooth #30: fractured mesiolingual cusp and recurrent decay under the existing MOD amalgam. Not restorable with a filling; crown needed.' }], {}, function (v) {
      if (v.text.length < 10) return 'Write a sentence or two about why the treatment was needed.';
      post('/api/claims/' + encodeURIComponent(c.id) + '/attachments/narrative', { text: v.text, patient: D.ptName(c.mrn), provider: D.provName(c.prov) || '', dos: D.us(c.dos) })
        .then(function (j) { after(c, j, 'Narrative added'); U.toast('Narrative added to ' + c.id + '.', 'ok'); }, function (er) { U.toast(er.message, 'warn'); });
    }, { save: 'Add Narrative', width: 600, intro: 'Printed as a page with the claim number, patient and dentist, and sent with the claim. Procedures: ' + E(lines || 'none') + '.' });
  });
  U.on(document.body, '[data-docdel]', 'click', function (b) {
    var c = D.claim(b.dataset.docc); if (!c) return;
    U.confirm('Remove Document', '<p class="cf">Remove this document from ' + E(c.id) + '?</p>', 'Remove', function () {
      fetch('/api/claims/' + encodeURIComponent(c.id) + '/attachments/' + b.dataset.docdel, { method: 'DELETE', credentials: 'same-origin' }).then(json)
        .then(function (j) { after(c, j, 'Document removed'); }, function (er) { U.toast(er.message, 'warn'); });
    }, { danger: 1 });
  });
  U.on(document.body, '[data-docsend]', 'click', function (b) {
    var cid = b.dataset.docsend; b.disabled = true;
    post('/api/claims/' + encodeURIComponent(cid) + '/attachments/send').then(function (j) { cache[cid] = j; paint(cid); U.toast('Documents sent' + (j.result && j.result.test ? ' (test mode)' : '') + '.', 'ok'); },
      function (er) { b.disabled = false; U.toast(er.message, 'warn'); V.docsChanged(cid); });
  });
})();
