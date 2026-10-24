/* Althais v4, the dental EMR, part 3: the revenue cycle. Verify benefits (V.pages.dbenefits), Claims (dclaims), Pre-auths
   (dpreauths), ERA posting (dera), Denials & follow-up (ddenials), Patient billing (dbilling).
   Server: dental_api_rcm.py. Vendors are mocks until contracts are signed; screens say so. */
(function () {
  if (!window.V || !window.DA) return;
  var E = U.esc, M = U.money, M0 = U.money0;
  var chartHref = function (mrn, tab) { return '/v4/dental/chart?mrn=' + encodeURIComponent(mrn) + (tab ? '&tab=' + tab : ''); };
  var stop = function (app, page, title, e) { app.innerHTML = DA.frame(page, title) + DA.blank('<b>' + (e.status === 403 ? 'Your role doesn’t include this screen' : 'Couldn’t load') + '</b>' + E(e.message)); U.fit(); };

  DA.benefitsHtml = function (b) {
    if (!b || b.none) return DA.blank('No dental plan on file.');
    if (!b.checked_at) return DA.blank('<b>Not verified</b>' + E(b.payer) + ' · ' + E(b.member_id || 'no subscriber ID'));
    var pct = b.annual_max ? Math.round(100 * (b.max_remaining || 0) / b.annual_max) : 0;
    return '<div class="dp-sec">' + U.kv([['Coverage', b.active ? '<span class="s-grn">Active</span>' : '<span class="s-red">Not active</span>'], ['Plan', E(b.plan_name || b.payer)], ['Subscriber ID', '<span class="mono">' + E(b.member_id) + '</span>'], ['Checked', DA.us(b.checked_at) + (b.environment === 'mock' ? ' <span class="mockflag">MOCK</span>' : '')]]) + '</div>' +
      (b.active ? '<div class="dp-sec"><div class="dp-h">Annual maximum <span class="end">' + M0(b.max_remaining) + ' of ' + M0(b.annual_max) + ' left</span></div><div class="meter' + (pct < 25 ? ' bad' : pct < 50 ? ' warn' : '') + '"><i style="width:' + pct + '%"></i></div>' +
        '<div style="margin-top:8px">' + U.kv([['Deductible left', M0(b.deductible_remaining) + ' of ' + M0(b.deductible)]].concat(Object.keys(b.coinsurance || {}).map(function (k) { return [k.replace('_', ' ').replace(/^./, function (c) { return c.toUpperCase(); }), b.coinsurance[k] + '%']; }))) + '</div></div>' +
        '<div class="dp-sec"><div class="dp-h">Frequency</div>' + (b.frequency || []).map(function (f) { return '<div style="display:flex;gap:6px;padding:2px 0"><span style="width:16px">' + (f.ok ? '<span class="s-grn">' + U.I.check + '</span>' : '<span class="s-red">✕</span>') + '</span><span>' + E(f.label) + '</span><span class="mut" style="margin-left:auto;font-size:11.5px">' + (f.last ? 'last ' + DA.short(f.last) : 'never') + (f.next ? ' · next ' + DA.us(f.next) : '') + '</span></div>'; }).join('') + '</div>' : '');
  };

  /* ================= Verify benefits ================= */
  V.pages.dbenefits = function () {
    var app = V.app, day = V.q.get('date') || DA.addDays(DA.today(), 1), data = null, sel = '', ben = {};
    function load() { Promise.all([DA.loadSetup(), DA.get('/schedule?date=' + day)]).then(function (r) { data = r[1]; draw(); }, function (e) { stop(app, 'dbenefits', 'Verify benefits', e); }); }
    function draw() {
      var seen = {}, rows = data.appointments.filter(function (a) { if (!a.mrn || a.status === 'cancelled' || seen[a.mrn]) return false; seen[a.mrn] = 1; return true; });
      var ins = rows.filter(function (a) { return a.flags && a.flags.payer && a.flags.payer !== 'Self-pay'; }), ok = ins.filter(function (a) { return a.flags.elig_active && a.flags.elig >= DA.addDays(day, -30); });
      app.innerHTML = DA.frame('dbenefits', 'Verify benefits', [['Verify everyone', 'check', { pri: 1, id: 'all' }]], '<button class="mini" data-nav="-1">‹</button> <input type="date" class="mini" id="goday" value="' + day + '"> <button class="mini" data-nav="1">›</button>' + DA.mock()) +
        '<div class="kpis" style="grid-template-columns:repeat(4,1fr)"><div><span>Patients booked</span><b>' + rows.length + '</b></div><div><span>With insurance</span><b>' + ins.length + '</b></div><div><span>Verified in the last 30 days</span><b class="' + (ok.length === ins.length ? 's-grn' : 's-amb') + '">' + ok.length + ' of ' + ins.length + '</b></div><div><span>Self-pay / members</span><b>' + (rows.length - ins.length) + '</b></div></div>' +
        '<div class="main" style="grid-template-columns:minmax(0,1fr) 340px">' +
        U.pane(new Date(day + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' }), 'the night-before check: 270 out, 271 back', '', U.grid(['Time', 'Patient', 'Dental plan', 'Last verified', 'Status', ''], rows.map(function (a) {
          var f = a.flags || {}, stale = f.elig && f.elig < DA.addDays(day, -30);
          return { sel: a.mrn === sel, attrs: ' data-mrn="' + E(a.mrn) + '"', cells: [DA.time(a.start), V.link(a.patient, chartHref(a.mrn, 'insurance')), E(f.payer || 'Self-pay'), f.elig ? DA.us(f.elig) : '<span class="mut">never</span>',
            !f.payer || f.payer === 'Self-pay' ? '<span class="mut">n/a</span>' : f.elig_active && !stale ? '<span class="s-grn">' + U.I.check + 'Active</span>' : f.elig && !f.elig_active ? '<span class="s-red">Not active</span>' : U.st(stale ? 'Re-verify' : 'Not verified', 'amb'),
            f.payer && f.payer !== 'Self-pay' ? '<button class="mini" data-v="' + E(a.mrn) + '">Verify</button>' : ''] };
        }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'Nobody booked this day.') + '</tbody>')) +
        U.pane('Benefits', sel ? E((rows.filter(function (a) { return a.mrn === sel; })[0] || {}).patient || '') : '', sel ? V.link('Chart', chartHref(sel, 'insurance')) : '', sel ? (ben[sel] ? DA.benefitsHtml(ben[sel]) : DA.blank('Loading…')) : DA.blank('Pick a patient to see their benefits.')) + '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '[data-nav]', 'click', function (b) { day = DA.addDays(day, +b.dataset.nav); load(); });
    U.on(app, '#goday', 'change', function (i) { day = i.value; load(); });
    U.on(app, 'tr[data-mrn]', 'click', function (tr, e) { if (e.target.closest('a,button')) return; sel = tr.dataset.mrn; draw(); if (!ben[sel]) DA.get('/patients/' + encodeURIComponent(sel) + '/benefits').then(function (b) { ben[sel] = b; draw(); }); });
    U.on(app, '[data-v]', 'click', function (b) { var m = b.dataset.v; b.disabled = true; DA.post('/patients/' + encodeURIComponent(m) + '/eligibility').then(function (r) { ben[m] = r; sel = m; U.toast(r.active ? 'Active: ' + M0(r.max_remaining) + ' left.' : 'Not active.', r.active ? 'ok' : 'warn'); load(); }, function (e) { b.disabled = false; DA.fail(e); }); });
    U.on(app, '#all', 'click', function () { U.toast('Verifying everyone booked…', 'busy'); DA.post('/eligibility/batch', { date: day }).then(function (r) { U.toast(r.checked.length + ' verified' + (r.skipped.length ? '; ' + r.skipped.length + ' missing insurance details' : '') + '.', 'ok'); ben = {}; load(); }, DA.fail); });
  };

  /* ================= Claims ================= */
  V.pages.dclaims = function () {
    var app = V.app, list = [], Q = V.q.get('q') || 'ready', sel = +V.q.get('claim') || 0, cur = null, kind = 'claim';
    var QUEUES = [['ready', 'Ready to send', function (c) { return c.status === 'ready'; }, 'blue'], ['attention', 'Needs attention', function (c) { return c.status === 'draft' || c.status === 'rejected'; }, 'amb'],
      ['sent', 'Sent, waiting on payer', function (c) { return c.status === 'submitted' || c.status === 'accepted'; }, 'lblue'], ['denied', 'Denied', function (c) { return c.status === 'denied'; }, 'red'], ['partial', 'Partly paid', function (c) { return c.status === 'partial'; }, 'amb'],
      ['paid', 'Paid', function (c) { return c.status === 'paid'; }, 'grn'], ['all', 'All claims', function () { return true; }, 'gray']];
    function load() {
      return Promise.all([DA.loadSetup(), DA.get('/claims?kind=' + kind)]).then(function (r) { list = r[1]; if (sel) return openClaim(sel); draw(); }, function (e) { stop(app, 'dclaims', 'Claims', e); });
    }
    function openClaim(id) { sel = id; return DA.get('/claims/' + id).then(function (c) { cur = c; if (!list.some(function (x) { return x.id === id; })) Q = 'all'; draw(); }, DA.fail); }
    function detail() {
      if (!cur) return U.pane('Claim', '', '', DA.blank('Pick a claim to see its lines, edits, narrative and attachments.'));
      var c = cur, red = (c.edits || []).filter(function (e) { return e.lvl === 'red'; }), sent = ['submitted', 'accepted', 'paid', 'partial', 'denied'].indexOf(c.status) >= 0;
      var edits = (c.edits || []).map(function (e) { return '<div class="' + (e.lvl === 'red' ? 's-red' : 's-amb') + '" style="padding:2px 0">' + (e.lvl === 'red' ? U.err : U.warn) + E(e.msg) + '</div>'; }).join('');
      var att = (c.attachments || []).map(function (a, i) { return '<div style="display:flex;gap:6px;align-items:center;padding:2px 0"><span class="chip">' + E(a.kind.replace('_', ' ')) + '</span>' + E(a.label || '') + (a.ref ? ' <span class="mono mut">' + E(a.ref) + '</span>' : '') + (sent ? '' : '<button class="mini" style="margin-left:auto" data-rmatt="' + i + '">' + U.I.x + '</button>') + '</div>'; }).join('');
      var needs = (c.attachments_needed || []).filter(function (n) { return n.kind !== 'narrative' && !(c.attachments || []).some(function (a) { return a.kind === n.kind; }); });
      return U.pane(E(c.claim_no), DA.statusChip(c.status) + (c.frequency === '7' ? ' <span class="chip">Corrected</span>' : ''), V.link(c.patient.name, chartHref(c.mrn)),
        '<div class="dp-sec">' + U.kv([['Patient', E(c.patient.name) + ' · DOB ' + DA.us(c.patient.dob)], ['Payer', E(c.payer) + ' · <span class="mono">' + E(c.member_id || '') + '</span>'], ['Date of service', DA.us(c.dos)], ['Dentist', E(c.provider)], ['Claim check', '<b class="' + (c.score >= 85 ? 's-grn' : c.score >= 65 ? 's-amb' : 's-red') + '">' + c.score + '</b> / 100']]) + '</div>' +
        '<div class="dp-sec" style="padding:0">' + U.grid(['Code', 'Tooth', 'Surf', ['Fee', 'num'], ['Ins. est.', 'num'], ['Paid', 'num'], ['W/O', 'num'], ['Pt', 'num']], c.lines.map(function (l) { return { cells: ['<span class="mono">' + E(l.code) + '</span>' + (l.status === 'denied' ? ' <span class="chip red">denied</span>' : ''), E(l.tooth || l.quad || ''), E(l.surface || ''), M(l.fee), M(l.est_insurance), M(l.paid), M(l.write_off), M(l.patient_resp)] }; }), { tight: 1 }) + '</div>' +
        (edits ? '<div class="dp-sec"><div class="dp-h">Before it goes out</div>' + edits + '</div>' : '<div class="dp-sec s-grn">' + U.I.check + 'No edits. Clean claim.</div>') +
        '<div class="dp-sec"><div class="dp-h">Narrative <span class="end">' + (sent ? '' : '<a href="#" id="gennarr">Draft from chart</a>') + '</span></div><textarea class="ed" id="narr" rows="4" style="width:100%"' + (sent ? ' readonly' : '') + ' placeholder="Why the treatment was needed, in plain clinical terms">' + E(c.narrative || '') + '</textarea>' + (sent ? '' : '<div style="text-align:right;margin-top:4px"><button class="mini" id="savenarr">Save narrative</button></div>') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Attachments</div>' + (att || '<span class="mut">None</span>') + (needs.length ? '<div class="s-amb" style="margin-top:4px;font-size:12px">' + U.warn + 'Payer will want: ' + needs.map(function (n) { return n.why; }).join(', ') + '</div>' : '') +
          (sent ? '' : '<div style="display:flex;gap:5px;margin-top:6px"><button class="mini" id="attimg">Attach X-rays</button><button class="mini" id="attperio">Attach perio chart</button></div>') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">History</div>' + (c.history || []).slice().reverse().map(function (h) { return '<div style="font-size:12px"><span class="mut">' + DA.us(h[0]) + ' ' + h[0].slice(11, 16) + '</span> ' + E(h[1]) + '</div>'; }).join('') + '</div>',
        { footer: '<button class="btn" id="c837">837D</button>' + (['denied', 'partial', 'rejected'].indexOf(c.status) >= 0 ? '<button class="btn" id="corr">Corrected claim</button>' : '') + (sent ? '' : '<button class="btn" id="recheck">Check again</button><button class="btn pri" id="send"' + (red.length ? ' disabled title="Fix the red edits first"' : '') + '>Send claim</button>') });
    }
    function draw() {
      var q = QUEUES.filter(function (x) { return x[0] === Q; })[0], rows = list.filter(q[2]);
      var ready = list.filter(function (c) { return c.status === 'ready'; });
      app.innerHTML = DA.frame('dclaims', 'Claims', [['Send all ready (' + ready.length + ')', 'send', { pri: 1, id: 'sendall' }], ['Fetch ERAs', 'download', { id: 'fetch' }], '|', ['Predeterminations', 'clip', { id: 'kindtoggle' }]], (kind === 'predetermination' ? '<span class="chip blue">Showing predeterminations</span> ' : '') + DA.mock()) +
        '<div class="main" style="grid-template-columns:190px minmax(0,1fr) 380px">' +
        U.pane(kind === 'claim' ? 'Claims' : 'Predeterminations', '', '', U.queue([{ h: '', items: QUEUES.map(function (x) { return { key: x[0], label: x[1], n: list.filter(x[2]).length, color: x[3] }; }) }], Q),
          { after: U.sum([['Ready', M0(ready.reduce(function (s, c) { return s + c.total; }, 0))], ['Waiting on payers', M0(list.filter(QUEUES[2][2]).reduce(function (s, c) { return s + c.est_insurance; }, 0))], ['Denied', M0(list.filter(QUEUES[3][2]).reduce(function (s, c) { return s + c.total - c.paid; }, 0))]]) }) +
        U.pane(q[1], rows.length + '', '', U.grid(['Claim', 'DOS', 'Patient', 'Payer', 'Procedures', ['Billed', 'num'], ['Ins. est.', 'num'], ['Paid', 'num'], 'Status', 'Age', 'Check'], rows.map(function (c) {
          var e0 = (c.edits || [])[0];
          return { sel: c.id === sel, attrs: ' data-claim="' + c.id + '"', cells: ['<span class="mono">' + E(c.claim_no) + '</span>', DA.short(c.dos), E(c.patient), E(c.payer), '<span class="mono" style="font-size:11.5px">' + E(c.lines.join(', ')) + '</span>', M(c.total), M(c.est_insurance), M(c.paid), DA.statusChip(c.status), c.age + 'd',
            e0 && ['draft', 'ready', 'rejected'].indexOf(c.status) >= 0 ? '<span title="' + E(e0.msg) + '">' + U.st(e0.msg.slice(0, 26) + (e0.msg.length > 26 ? '…' : ''), e0.lvl) + '</span>' : ''] };
        }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(11, list.length ? 'Nothing in this queue.' : 'No claims yet. They’re made when procedures are completed in the chart.') + '</tbody>')) +
        detail() + '</div>';
      U.fit(); U.search();
    }
    load();
    var C = function () { return cur; };
    U.on(app, '[data-q]', 'click', function (el) { Q = el.dataset.q; draw(); });
    U.on(app, 'tr[data-claim]', 'click', function (tr) { openClaim(+tr.dataset.claim); history.replaceState(null, '', '/v4/dental/claims?claim=' + tr.dataset.claim); });
    U.on(app, '#kindtoggle', 'click', function () { kind = kind === 'claim' ? 'predetermination' : 'claim'; cur = null; sel = 0; Q = 'all'; load(); });
    U.on(app, '#send', 'click', function () { U.toast('Building the 837D and sending…', 'busy'); DA.post('/claims/' + C().id + '/submit').then(function (r) { U.toast(r.result.ok ? 'Sent. ' + r.result.message : 'Rejected: ' + r.result.message, r.result.ok ? 'ok' : 'warn'); load(); }, DA.fail); });
    U.on(app, '#sendall', 'click', function () { DA.post('/claims/submit-ready').then(function (r) { U.toast(r.sent.length + ' claims sent' + (r.held.length ? ', ' + r.held.length + ' held for edits' : '') + '.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#fetch', 'click', function () { U.toast('Checking the clearinghouse for remittances…', 'busy'); DA.post('/era/fetch').then(function (r) { var n = r.posted.filter(function (x) { return !x.error; }); U.toast(n.length ? n.length + ' ERA' + (n.length === 1 ? '' : 's') + ' auto-posted: ' + M(n.reduce(function (s, x) { return s + x.amount; }, 0)) + '.' : 'No new remittances.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#recheck', 'click', function () { DA.post('/claims/' + C().id + '/check').then(function () { openClaim(C().id); U.toast('Claim checked again.', 'ok'); }, DA.fail); });
    U.on(app, '#gennarr', 'click', function (a, e) { e.preventDefault(); DA.post('/claims/' + C().id + '/narrative').then(function (r) { if (!r.narrative) U.toast('These procedures don’t usually need a narrative.', 'info'); openClaim(C().id); }, DA.fail); });
    U.on(app, '#savenarr', 'click', function () { DA.patch('/claims/' + C().id, { narrative: document.getElementById('narr').value }).then(function (c) { cur = c; U.toast('Narrative saved.', 'ok'); draw(); }, DA.fail); });
    U.on(app, '#attperio', 'click', function () { DA.post('/claims/' + C().id + '/attach', { perio_latest: true }).then(function (c) { cur = c; U.toast('Perio chart attached.', 'ok'); draw(); }, DA.fail); });
    U.on(app, '[data-rmatt]', 'click', function (b) { DA.post('/claims/' + C().id + '/attach', { remove: +b.dataset.rmatt }).then(function (c) { cur = c; draw(); }, DA.fail); });
    U.on(app, '#attimg', 'click', function () {
      DA.get('/patients/' + encodeURIComponent(C().mrn)).then(function (x) {
        if (!x.images.length) return U.toast('No images on file for this patient. Take them in the chart’s Imaging tab.', 'warn');
        U.form('Attach X-rays', x.images.map(function (i) { return { k: 'i' + i.id, label: i.kind + ' ' + DA.us(i.taken) + ' (' + (i.teeth || []).slice(0, 8).map(function (t) { return '#' + t; }).join(' ') + ')', type: 'check', span: 2 }; }), {}, function (v) {
          var ids = x.images.filter(function (i) { return v['i' + i.id]; }).map(function (i) { return i.id; }); if (!ids.length) return 'Pick at least one.';
          DA.post('/claims/' + C().id + '/attach', { image_ids: ids }).then(function (c) { cur = c; U.toast(ids.length + ' image(s) attached.', 'ok'); draw(); }, DA.fail);
        }, { save: 'Attach' });
      }, DA.fail);
    });
    U.on(app, '#c837', 'click', function () { fetch('/api/dental/claims/' + C().id + '/837', { credentials: 'same-origin' }).then(function (r) { return r.text(); }).then(function (t) { U.modal('837D for ' + C().claim_no, '<div class="mut" style="margin-bottom:6px">X12 005010X224A2, as it goes to the clearinghouse.</div><div class="x12">' + E(t) + '</div>', [['Download', '', function () { U.download(C().claim_no + '.837', t, 'text/plain'); return false; }], ['Close', 'pri', function () {}]], { wide: 1 }); }); });
    U.on(app, '#corr', 'click', function () { DA.post('/claims/' + C().id + '/corrected').then(function (c) { U.toast('Corrected claim ' + c.claim_no + ' started (frequency 7). Fix it and send.', 'ok'); Q = 'attention'; sel = c.id; load(); }, DA.fail); });
  };

  /* ================= Pre-authorizations ================= */
  V.pages.dpreauths = function () {
    var app = V.app, list = [];
    function load() { DA.get('/preauths').then(function (l) { list = l; draw(); }, function (e) { stop(app, 'dpreauths', 'Pre-authorizations', e); }); }
    function draw() {
      var cnt = function (s) { return list.filter(function (p) { return s.indexOf(p.status) >= 0; }); };
      app.innerHTML = DA.frame('dpreauths', 'Pre-authorizations', [['Check all pending', 'refresh', { pri: 1, id: 'all' }]], DA.mock()) +
        '<div class="kpis" style="grid-template-columns:repeat(4,1fr)"><div><span>Waiting on payer</span><b>' + cnt(['submitted', 'pending']).length + '</b><small>' + M0(cnt(['submitted', 'pending']).reduce(function (s, p) { return s + p.amount; }, 0)) + ' of treatment</small></div><div><span>Approved</span><b class="s-grn">' + cnt(['approved', 'partial']).length + '</b><small>' + M0(cnt(['approved', 'partial']).reduce(function (s, p) { return s + p.approved_amount; }, 0)) + ' approved</small></div><div><span>Denied</span><b class="s-red">' + cnt(['denied']).length + '</b></div><div><span>Waiting over 14 days</span><b class="s-amb">' + list.filter(function (p) { return p.days_waiting > 14; }).length + '</b><small>call the payer</small></div></div>' +
        '<div class="main" style="grid-template-columns:minmax(0,1fr)">' + U.pane('Predeterminations', 'sent as 837D with CLM19 = PB', '', U.grid(['Patient', 'Payer', 'Procedures', ['Estimated', 'num'], 'Status', 'Sent', 'Waiting', ['Approved', 'num'], 'Expires', 'Payer ref', ''], list.map(function (p) {
          return { cells: [V.link(p.patient, chartHref(p.mrn, 'plans')), E(p.payer), '<span class="mono" style="font-size:11.5px">' + E((p.lines || []).join(', ')) + '</span>', M(p.amount), DA.statusChip(p.status), DA.us(p.submitted_at), p.days_waiting != null ? '<span class="' + (p.days_waiting > 14 ? 's-amb' : '') + '">' + p.days_waiting + ' days</span>' : '', p.approved_amount ? M(p.approved_amount) : '', DA.us(p.expires), '<span class="mono">' + E(p.ref) + '</span>',
            (['submitted', 'pending'].indexOf(p.status) >= 0 ? '<button class="mini" data-ref="' + p.id + '">Check status</button> ' : '') + '<button class="mini" data-dec="' + p.id + '">Record answer</button>'] };
        }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(11, 'No pre-authorizations. Request one from a treatment plan.') + '</tbody>')) + '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '[data-ref]', 'click', function (b) { DA.post('/preauths/' + b.dataset.ref + '/refresh').then(function (p) { U.toast('Status: ' + p.status + (p.approved_amount ? ', approved ' + M(p.approved_amount) : '') + '.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#all', 'click', function () { Promise.all(list.filter(function (p) { return ['submitted', 'pending'].indexOf(p.status) >= 0; }).map(function (p) { return DA.post('/preauths/' + p.id + '/refresh').catch(function () {}); })).then(function () { U.toast('Checked.', 'ok'); load(); }); });
    U.on(app, '[data-dec]', 'click', function (b) {
      var p = list.filter(function (x) { return x.id === +b.dataset.dec; })[0];
      U.form('Record the payer’s answer', [{ k: 'status', label: 'Decision', type: 'select', options: [['approved', 'Approved'], ['partial', 'Partly approved'], ['denied', 'Denied'], ['pending', 'Still pending']] }, { k: 'approved_amount', label: 'Approved amount', type: 'number', step: '0.01' }, { k: 'expires', label: 'Approval expires', type: 'date' }, { k: 'ref', label: 'Payer reference', def: p.ref }, { k: 'note', label: 'Note', span: 2, ph: 'By fax / portal / phone, who said what' }], {}, function (v) {
        DA.patch('/preauths/' + p.id, v).then(function () { U.toast('Saved.', 'ok'); load(); }, DA.fail);
      });
    });
  };

  /* ================= ERA posting ================= */
  V.pages.dera = function () {
    var app = V.app, list = [], sel = 0;
    function load() { DA.get('/era').then(function (l) { list = l; draw(); }, function (e) { stop(app, 'dera', 'ERA posting', e); }); }
    function draw() {
      var e = list.filter(function (x) { return x.id === sel; })[0], s = e && e.summary || {};
      app.innerHTML = DA.frame('dera', 'ERA posting', [['Fetch ERAs from clearinghouse', 'download', { pri: 1, id: 'fetch' }], ['Upload 835 file', 'upload', { id: 'up' }]], DA.mock()) +
        '<div class="kpis" style="grid-template-columns:repeat(4,1fr)"><div><span>Remittances posted</span><b>' + list.length + '</b></div><div><span>Insurance paid</span><b class="s-grn">' + M0(list.reduce(function (t, x) { return t + x.amount; }, 0)) + '</b></div><div><span>Auto-posted clean</span><b>' + list.filter(function (x) { return x.status === 'posted'; }).length + '</b></div><div><span>Need a person</span><b class="' + (list.some(function (x) { return x.status === 'exceptions'; }) ? 's-amb' : '') + '">' + list.filter(function (x) { return x.status === 'exceptions'; }).length + '</b><small>in Denials & follow-up</small></div></div>' +
        '<div class="main" style="grid-template-columns:minmax(0,1fr) 340px">' + U.pane('Remittances (835)', 'payments, contractual write-offs and patient portions post by themselves; denials go to the work queue', '', U.grid(['Received', 'Paid on', 'Payer', 'Check / EFT', ['Amount', 'num'], 'Claims', ['Patient portion', 'num'], 'Denials', 'Result'], list.map(function (x) {
          var m = x.summary || {}; return { sel: x.id === sel, attrs: ' data-era="' + x.id + '"', cells: [DA.us(x.created), DA.us(x.check_date), E(x.payer), '<span class="mono">' + E(x.check_no) + '</span>', M(x.amount), (m.posted || 0) + ' of ' + (m.claims || 0), M(m.patient_resp || 0), m.denials ? '<span class="s-red">' + m.denials + '</span>' : '0', x.status === 'posted' ? '<span class="s-grn">' + U.I.check + 'Auto-posted</span>' : U.st('Exceptions', 'amb')] };
        }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(9, 'No remittances yet. Send claims, then fetch ERAs.') + '</tbody>')) +
        U.pane('Remittance', e ? E(e.check_no) : '', '', e ? '<div class="dp-sec">' + U.kv([['Payer', E(e.payer)], ['Paid on', DA.us(e.check_date)], ['Amount', M(e.amount)], ['Claims on it', s.claims], ['Posted', s.posted], ['Insurance paid', M(s.paid || 0)], ['Patient portion', M(s.patient_resp || 0)], ['Denied lines', s.denials], ['Posted by', E(e.posted_by)]]) + '</div>' +
          ((s.exceptions || []).length ? '<div class="dp-sec"><div class="dp-h">Exceptions</div>' + s.exceptions.map(function (x) { return '<div class="s-amb">' + U.warn + E(x) + '</div>'; }).join('') + '<div style="margin-top:6px"><a href="/v4/dental/denials">Open the work queue</a></div></div>' : '<div class="dp-sec s-grn">' + U.I.check + 'Every line matched and posted.</div>') : DA.blank('Pick a remittance.')) + '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, 'tr[data-era]', 'click', function (tr) { sel = +tr.dataset.era; draw(); });
    U.on(app, '#fetch', 'click', function () { U.toast('Checking the clearinghouse…', 'busy'); DA.post('/era/fetch').then(function (r) { var ok = r.posted.filter(function (x) { return !x.error; }); U.toast(ok.length ? ok.length + ' remittance' + (ok.length === 1 ? '' : 's') + ' posted, ' + M(ok.reduce(function (s, x) { return s + x.amount; }, 0)) + '.' : 'No new remittances.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#up', 'click', function () { U.pickFile('.835,.txt,.edi,.x12', false, function (f) { U.readFile(f[0], true).then(function (t) { DA.post('/era/upload', { x12: t }).then(function (r) { U.toast('Posted ' + M(r.amount) + ' from ' + r.check_no + '.', 'ok'); sel = r.id; load(); }, DA.fail); }); }); });
  };

  /* ================= Denials & follow-up ================= */
  V.pages.ddenials = function () {
    var app = V.app, list = [], Q = 'all', sel = null;
    var ACTION = { fix: 'Fix and resubmit', attach: 'Attach and resubmit', appeal: 'Appeal', patient: 'Bill the patient', review: 'Review' };
    function load() { DA.get('/work').then(function (l) { list = l; draw(); }, function (e) { stop(app, 'ddenials', 'Denials & follow-up', e); }); }
    var key = function (w) { return w.id != null ? 'w' + w.id : 'c' + w.claim_id; };
    function letter(w) {
      return 'To: ' + w.payer + ' Appeals\nRe: Claim ' + w.claim_no + ', date of service ' + DA.us(w.dos) + '\nPatient: ' + w.patient + '\n\nWe request reconsideration of the denial (' + (w.carc ? 'CARC ' + w.carc + ': ' : '') + w.title.replace(/^.*denied: /, '') + ').\n\nThe treatment was necessary for the reasons in the enclosed narrative, and the enclosed radiographs and periodontal chart support it. Please reprocess the claim for payment.\n\nEnclosures: narrative, pre-operative radiographs, periodontal chart, copy of the EOB.\n\nSincerely,\n';
    }
    function draw() {
      var groups = [['all', 'Everything open'], ['denial', 'Denials'], ['exception', 'ERA exceptions'], ['rejection', 'Clearinghouse rejections'], ['aging', 'No response 30+ days'], ['preauth', 'Pre-auth denials'], ['review', 'Low reviews']];
      var rows = list.filter(function (w) { return Q === 'all' || w.kind === Q || (Q.indexOf('a:') === 0 && w.action === Q.slice(2)); }).sort(function (a, b) { return b.amount - a.amount; });
      var w = list.filter(function (x) { return key(x) === sel; })[0];
      app.innerHTML = DA.frame('ddenials', 'Denials & follow-up', [], '') +
        '<div class="kpis" style="grid-template-columns:repeat(4,1fr)"><div><span>Open items</span><b>' + list.length + '</b></div><div><span>Dollars at stake</span><b class="s-red">' + M0(list.reduce(function (s, x) { return s + (x.amount || 0); }, 0)) + '</b></div><div><span>Denials</span><b>' + list.filter(function (x) { return x.kind === 'denial'; }).length + '</b></div><div><span>Older than 14 days</span><b class="s-amb">' + list.filter(function (x) { return x.age > 14; }).length + '</b></div></div>' +
        '<div class="main" style="grid-template-columns:210px minmax(0,1fr) 380px">' +
        U.pane('Work queue', '', '', U.queue([{ h: 'By kind', items: groups.map(function (g) { return { key: g[0], label: g[1], n: g[0] === 'all' ? list.length : list.filter(function (x) { return x.kind === g[0]; }).length, color: g[0] === 'denial' ? 'red' : 'lblue' }; }) },
          { h: 'By next step', items: Object.keys(ACTION).map(function (a) { return { key: 'a:' + a, label: ACTION[a], n: list.filter(function (x) { return x.action === a; }).length, color: 'gray' }; }) }], Q)) +
        U.pane('Open', rows.length + '', '', U.grid(['Age', 'Patient', 'Claim', 'Payer', 'What happened', 'Next step', ['Amount', 'num'], 'Status'], rows.map(function (x) {
          return { sel: key(x) === sel, attrs: ' data-w="' + key(x) + '"', cells: [x.age + 'd', E(x.patient), '<span class="mono">' + E(x.claim_no || '') + '</span>', E(x.payer || ''), '<span class="wrap">' + E(x.title) + '</span>', E(ACTION[x.action] || x.action), M(x.amount || 0), E(x.status)] };
        }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(8, 'Nothing open. Every claim is paid or moving.') + '</tbody>')) +
        U.pane('Item', w ? E(ACTION[w.action] || '') : '', w && w.claim_id ? '<a href="/v4/dental/claims?claim=' + w.claim_id + '">Open claim</a>' : '', w ? '<div class="dp-sec"><div class="dp-h">' + E(w.title) + '</div>' + U.kv([['Patient', w.mrn ? V.link(w.patient, chartHref(w.mrn)) : ''], ['Payer', E(w.payer || '')], ['Amount', M(w.amount || 0)], ['CARC', E(w.carc || '')], ['Due', DA.us(w.due)], ['Assigned', E(w.assigned_to || 'Nobody')]]) + '</div>' +
          '<div class="dp-sec"><div class="dp-h">What to do</div>' + E(w.how || '') + '</div>' +
          (w.id != null ? '<div class="dp-sec"><div class="dp-h">Notes</div>' + (w.notes || []).map(function (n) { return '<div style="font-size:12px"><span class="mut">' + E(n[0].slice(0, 10)) + ' ' + E(n[1]) + ':</span> ' + E(n[2]) + '</div>'; }).join('') + '<div class="compose" style="padding:6px 0 0;border:0"><input id="wnote" placeholder="Called payer, ref #…"><button class="mini" id="addnote">Add</button></div></div>' : '') +
          '<div class="dp-sec" style="display:flex;gap:5px;flex-wrap:wrap">' + (w.claim_id && ['fix', 'attach'].indexOf(w.action) >= 0 ? '<button class="mini pri" id="wcorr">Corrected claim</button>' : '') + (w.action === 'appeal' ? '<button class="mini pri" id="wappeal">Draft appeal</button>' : '') + (w.claim_id && w.id != null ? '<button class="mini" id="wpt">Bill the patient</button><button class="mini" id="wwo">Write off</button>' : '') +
            (w.id != null ? '<button class="mini" id="wme">Assign to me</button><button class="mini" id="wdone">Resolved</button>' : '') + '</div>' : DA.blank('Pick an item. Biggest dollars first.')) + '</div>';
      U.fit(); U.search();
    }
    load();
    var W = function () { return list.filter(function (x) { return key(x) === sel; })[0]; };
    U.on(app, '[data-q]', 'click', function (el) { Q = el.dataset.q; draw(); });
    U.on(app, 'tr[data-w]', 'click', function (tr) { sel = tr.dataset.w; draw(); });
    U.on(app, '#addnote', 'click', function () { var t = document.getElementById('wnote').value.trim(); if (!t) return; DA.patch('/work/' + W().id, { note: t, status: 'working' }).then(load, DA.fail); });
    U.on(app, '#wme', 'click', function () { DA.patch('/work/' + W().id, { assigned_to: (window.__ALTHAIS_USER__ || {}).email, status: 'working' }).then(load, DA.fail); });
    U.on(app, '#wdone', 'click', function () { DA.patch('/work/' + W().id, { status: 'resolved' }).then(function () { sel = null; U.toast('Resolved.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#wcorr', 'click', function () { var w = W(); DA.post('/claims/' + w.claim_id + '/corrected').then(function (c) { if (w.id != null) DA.patch('/work/' + w.id, { status: 'working', note: 'Corrected claim ' + c.claim_no + ' started' }); location.href = '/v4/dental/claims?claim=' + c.id; }, DA.fail); });
    U.on(app, '#wpt', 'click', function () { U.confirm('Bill the patient', '<p class="cf">Move the denied amount to the patient’s balance? Do this only when the plan doesn’t cover it (frequency, maximum, not a benefit) and your financial policy allows it.</p>', 'Move to patient', function () { DA.post('/work/' + W().id + '/bill-patient').then(function (r) { sel = null; U.toast(M(r.amount) + ' moved to the patient.', 'ok'); load(); }, DA.fail); }); });
    U.on(app, '#wwo', 'click', function () { U.ask('Write off', 'Reason', '', function (r) { DA.post('/work/' + W().id + '/write-off', { reason: r }).then(function (x) { sel = null; U.toast('Wrote off ' + M(x.amount) + '.', 'ok'); load(); }, DA.fail); }, { ok: 'Write off', danger: 1 }); });
    U.on(app, '#wappeal', 'click', function () { var w = W(), t = letter(w); U.modal('Appeal letter', '<textarea class="ed" id="aplt" rows="16" style="width:100%">' + E(t) + '</textarea>', [['Download', '', function (c, m) { U.download('appeal-' + w.claim_no + '.txt', m.querySelector('#aplt').value, 'text/plain'); return false; }], ['Mark appealed', 'pri', function (c, m) { DA.patch('/work/' + w.id, { status: 'working', note: 'Appeal sent' }).then(load); }]], { wide: 1 }); });
  };

  /* ================= Patient billing ================= */
  V.pages.dbilling = function () {
    var app = V.app, tab = V.q.get('tab') || 'balances', S = null, AR = null, MEM = null;
    function load() {
      Promise.all([DA.loadSetup(), DA.get('/billing/summary'), DA.get('/memberships'), DA.get('/ar').catch(function () { return null; })]).then(function (r) { S = r[1]; MEM = r[2]; AR = r[3]; draw(); }, function (e) { stop(app, 'dbilling', 'Patient billing', e); });
    }
    function bars(b) { var max = Math.max.apply(null, Object.keys(b).map(function (k) { return b[k]; }).concat([1])); return Object.keys(b).map(function (k) { return '<div class="hbar"><span class="l">' + k + ' days</span><span class="m"><i style="width:' + Math.round(100 * b[k] / max) + '%"></i></span><span class="v">' + M0(b[k]) + '</span></div>'; }).join(''); }
    function draw() {
      var owe = S.owing.reduce(function (s, x) { return s + x.due; }, 0), paidTtp = S.requests.filter(function (r) { return r.status === 'paid'; });
      var body = '';
      if (tab === 'balances') body = U.grid(['Patient', ['Account balance', 'num'], ['With insurance', 'num'], ['Owes now', 'num'], ''], S.owing.map(function (o) { return { cells: [V.link(o.patient, chartHref(o.mrn, 'account')), M(o.balance), M(o.pending_insurance), '<b class="' + (o.due > 0 ? 's-red' : '') + '">' + M(o.due) + '</b>', o.due > 0 ? '<button class="mini" data-ttp="' + E(o.mrn) + '">Text-to-pay</button> <button class="mini" data-pay="' + E(o.mrn) + '" data-due="' + o.due + '">Collect</button> <button class="mini" data-plan="' + E(o.mrn) + '" data-due="' + o.due + '">Payment plan</button>' : ''] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'Nobody owes anything.') + '</tbody>');
      else if (tab === 'ttp') body = U.grid(['Sent', 'Patient', ['Amount', 'num'], 'To', 'Status', 'Paid'], S.requests.map(function (r) { return { cells: [DA.us(r.created), E(r.patient), M(r.amount), '…' + E(r.sent_to), DA.statusChip(r.status === 'paid' ? 'paid' : r.status === 'sent' ? 'pending' : r.status), r.paid_at ? DA.us(r.paid_at) : ''] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No payment links sent yet.') + '</tbody>');
      else if (tab === 'plans') body = U.grid(['Patient', ['Total', 'num'], ['Paid', 'num'], 'Months', 'Next', 'Autopay', 'Status'], S.plans.map(function (p) { return { cells: [V.link(p.patient, chartHref(p.mrn, 'account')), M(p.total), M(p.paid), p.months, p.next ? M(p.next.amount) + ' on ' + DA.us(p.next.due) : '', p.autopay ? 'Yes' : 'No', DA.statusChip(p.status === 'active' ? 'scheduled' : p.status)] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(7, 'No payment plans.') + '</tbody>');
      else if (tab === 'members') body = '<div class="cov">' + MEM.plans.map(function (p) { return '<div style="min-width:220px"><span>' + E(p.name) + '</span><b>' + M0(p.monthly) + '/mo · ' + M0(p.annual) + '/yr</b><div class="mut" style="font-size:11.5px;margin-top:3px">Includes ' + Object.keys(p.included || {}).map(function (k) { return p.included[k] + '× ' + k; }).join(', ') + '; ' + p.discount_pct + '% off everything else</div></div>'; }).join('') + '</div>' +
        U.grid(['Member', 'Plan', 'Billing', 'Started', 'Renews', 'Status'], MEM.members.map(function (x) { return { cells: [V.link(x.patient, chartHref(x.mrn, 'account')), E(x.plan), E(x.billing), DA.us(x.start), DA.us(x.renews), DA.statusChip(x.status === 'active' ? 'approved' : x.status)] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No members yet. Offer a plan to uninsured patients from their Account tab.') + '</tbody>');
      else if (tab === 'aging') body = AR ? '<div class="dgrid2"><div class="dpad"><div class="dp-h">Insurance AR</div>' + bars(AR.insurance) + '</div><div class="dpad"><div class="dp-h">Patient AR</div>' + bars(AR.patient) + '</div></div>' : DA.blank('Aging is for billers and admins.');
      app.innerHTML = DA.frame('dbilling', 'Patient billing', [], DA.mock()) +
        '<div class="kpis"><div><span>Patients owe now</span><b class="s-red">' + M0(owe) + '</b><small>' + S.owing.filter(function (o) { return o.due > 0; }).length + ' patients</small></div><div><span>Text-to-pay sent</span><b>' + S.requests.length + '</b><small>' + paidTtp.length + ' paid · ' + M0(paidTtp.reduce(function (s, r) { return s + r.amount; }, 0)) + '</small></div>' +
          '<div><span>Payment plans</span><b>' + S.plans.filter(function (p) { return p.status === 'active'; }).length + '</b><small>' + M0(S.plans.reduce(function (s, p) { return s + p.total - p.paid; }, 0)) + ' still to collect</small></div><div><span>Members</span><b>' + MEM.active + '</b><small>' + M0(MEM.mrr) + ' per month</small></div>' +
          '<div><span>Insurance AR</span><b>' + (AR ? M0(Object.keys(AR.insurance).reduce(function (s, k) { return s + AR.insurance[k]; }, 0)) : '-') + '</b></div><div><span>Patient AR 90+</span><b class="s-amb">' + (AR ? M0(AR.patient['91-120'] + AR.patient['120+']) : '-') + '</b></div></div>' +
        '<div class="main" style="grid-template-columns:minmax(0,1fr);height:calc(100vh - var(--chrome, 82px))"><section class="pane"><div class="dtabs">' + [['balances', 'Balances'], ['ttp', 'Text-to-pay'], ['plans', 'Payment plans'], ['members', 'Membership plans'], ['aging', 'Aging']].map(function (t) { return '<button data-tab="' + t[0] + '" class="' + (t[0] === tab ? 'on' : '') + '">' + t[1] + '</button>'; }).join('') + '</div><div class="pbody">' + body + '</div></section></div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '[data-tab]', 'click', function (b) { tab = b.dataset.tab; draw(); });
    U.on(app, '[data-ttp]', 'click', function (b) { DA.post('/patients/' + encodeURIComponent(b.dataset.ttp) + '/text-to-pay', {}).then(function (r) { U.toast('Payment link texted for ' + M(r.amount) + '.', 'ok'); load(); }, DA.fail); });
    U.on(app, '[data-pay]', 'click', function (b) { DA.takePayment(b.dataset.pay, +b.dataset.due, load); });
    U.on(app, '[data-plan]', 'click', function (b) {
      U.form('Payment plan', [{ k: 'total', label: 'Amount', type: 'number', step: '0.01', def: (+b.dataset.due).toFixed(2), req: 1 }, { k: 'down', label: 'Down payment', type: 'number', step: '0.01', def: '0' }, { k: 'months', label: 'Months', type: 'select', options: ['3', '6', '9', '12'], def: '6' }, { k: 'autopay', label: 'Autopay with the test card', type: 'check' }], {}, function (v) {
        DA.post('/patients/' + encodeURIComponent(b.dataset.plan) + '/payment-plans', { total: +v.total, down: +v.down, months: +v.months, autopay: v.autopay, token: v.autopay ? 'tok_visa' : '' }).then(function () { U.toast('Payment plan created.', 'ok'); load(); }, DA.fail);
      }, { save: 'Create' });
    });
  };
})();
