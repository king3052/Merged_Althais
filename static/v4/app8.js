/* v4 working preview, part 8: Revenue › Connect EMR. Keep your EMR, add Althais's claim checks. Claims come in two ways: a live
   connection (athenahealth now, DrChrono when its app is set up) or an uploaded charges export from any EMR. Every claim is
   checked before it leaves (codes, bundling, missing diagnosis or payer, filing deadline) and nothing is saved until a person
   presses Save. Server: emr_connect.py, athenahealth.py, drchrono.py (/api/integrations/*). Saved claims show up in Claims
   because the app already pulls the clinic's server-side claims. */
(function () {
  var app = V.app, E = U.esc, M = U.money;
  var EMRS = [], SUM = null, WORK = [], PREVIEW = null, SEL = V.q.get('emr') || 'athenahealth', TAB = 'preview', CL = '', ERR = '', BUSY = false;
  var VERDICT = { clean: ['Clean', 'grn'], review: ['Review', 'amb'], fix: ['Fix first', 'red'] };

  function api(method, url, body, form) {
    return fetch(url, { method: method, credentials: 'same-origin', headers: body && !form ? { 'Content-Type': 'application/json' } : {}, body: form || (body ? JSON.stringify(body) : undefined) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || j.detail || 'Something went wrong.'); return j; }); });
  }
  var emr = function (k) { return EMRS.filter(function (e) { return e.key === k; })[0] || null; };
  var live = function () { return EMRS.filter(function (e) { return e.live; })[0] || null; };
  var action = function (e, kind) { return e && (e.actions || []).filter(function (a) { return a.kind === kind; })[0]; };

  function load() {
    return Promise.all([api('GET', '/api/integrations/emrs'), api('GET', '/api/integrations/summary'), api('GET', '/api/integrations/worklist')]).then(function (r) {
      EMRS = r[0].emrs; SUM = r[1]; WORK = r[2].claims; ERR = ''; if (!emr(SEL)) SEL = EMRS[0] ? EMRS[0].key : ''; draw();
    }, function (e) { ERR = e.message; draw(); });
  }

  /* ---------- the claims grids ---------- */
  function previewGrid() {
    var rows = PREVIEW.claims.map(function (c, i) {
      var v = VERDICT[c.verdict] || VERDICT.fix, fl = (c.flags || []).filter(function (f) { return f.severity !== 'info'; });
      return { sel: i === CL, attrs: ' data-pc="' + i + '"', cells: [U.st(v[0] + ' · ' + c.score, v[1]), '<span class="mono">' + E(c.claim_id || '-') + '</span>', E(c.patient_name), E(c.payer || '-'), E(c.dos || c.dos_raw || '-'),
        '<span class="mono">' + E((c.cpt || []).join(' ')) + '</span>', M(c.amount), fl.length ? '<span class="wrap">' + E(fl[0].message) + (fl.length > 1 ? ' <span class="mut">+' + (fl.length - 1) + ' more</span>' : '') + '</span>' : '<span class="s-mut">Nothing found</span>'] };
    });
    return U.grid(['Result', 'Claim #', 'Patient', 'Payer', 'DOS', 'Codes', ['Charge', 'num'], ['Findings', 'wrap']], rows, { click: 1 });
  }
  function workGrid() {
    var rows = WORK.map(function (c, i) {
      var lvl = c.status === 'Ready to Submit' ? 'grn' : c.status === 'Pending Review' ? 'amb' : 'blue';
      return { sel: i === CL, attrs: ' data-wc="' + i + '"', cells: [U.st(c.status, lvl), '<span class="mono">' + E(c.id) + '</span>', E(c.patient), E(c.payer || 'No payer'), M(c.amount), c.flags && c.flags.length ? '<span class="wrap">' + E(c.flags[0]) + (c.flags.length > 1 ? ' <span class="mut">+' + (c.flags.length - 1) + ' more</span>' : '') + '</span>' : '<span class="s-mut">None</span>'] };
    });
    return U.grid(['Status', 'Claim #', 'Patient', 'Payer', ['Charge', 'num'], ['Findings', 'wrap']], rows, { click: 1 })
      .replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'Nothing saved yet. Upload an export or pull from your EMR, then press Save.') + '</tbody>');
  }
  function howTo() {
    return '<div class="blank"><b>Bring your claims in, two ways.</b><br><br>' +
      '<b>1. Connect your EMR.</b> Pick it on the left. Where a live connection is available, Althais pulls recent claims for you to review.<br><br>' +
      '<b>2. Upload an export.</b> Press Upload File and choose a CSV or Excel charges report from any EMR. Get Template shows the columns that help most: patient, payer, date of service, CPT, diagnosis, charge.<br><br>' +
      'Either way, every claim is checked first and nothing is saved until you press Save. Use test data only until a business associate agreement is in place.</div>';
  }

  /* ---------- the right-hand detail ---------- */
  function emrDetail() {
    var e = emr(SEL); if (!e) return U.pane('EMR', '', '', '<div class="blank">Pick an EMR.</div>');
    var acts = (e.actions || []).map(function (a) {
      if (a.kind === 'link') return '<a class="btn pri" href="' + E(a.url) + '">' + E(a.label) + '</a>';
      return '<button type="button" class="btn' + (a.kind === 'disconnect' ? '' : ' pri') + '" data-act="' + E(a.kind) + '" data-days="' + (a.days || '') + '">' + E(a.label) + '</button>';
    }).join(' ');
    var test = /\(preview data\)/i.test(e.live_state || '');
    return U.pane(E(e.label), E(e.segment), '', '<div class="dp-sec">' + U.kv([['Connection', e.live ? '<span class="tc s-grn">' + U.I.check + 'Connected</span>' : '<span class="tc s-mut">Upload only</span>'], ['Access route', E(e.access)]]) +
      '<div class="t2" style="margin-top:8px;line-height:1.5">' + E(e.live_state) + '</div>' + (e.key === 'tebra' ? '<div class="mut" style="margin-top:6px;font-size:12px">This connects to a real Tebra account. Connect a live practice only after it has signed a business associate agreement with Althais.</div>' : '') + (test ? '<div class="mut" style="margin-top:6px;font-size:12px">This is the vendor’s test data, not real patients. Real clinics need the vendor’s production approval and a signed agreement first.</div>' : '') +
      (acts ? '<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">' + acts + '</div>' : '') + '</div>' +
      '<div class="dp-sec"><div class="dp-h">Upload works too</div><div class="t2" style="line-height:1.5">Any ' + E(e.label) + ' charges or claims export (CSV or Excel) can be checked now, with or without a live connection.</div>' +
      '<div style="margin-top:8px"><button type="button" class="btn" id="cx-upload2">Upload File</button></div></div>' + claimDetail());
  }
  function claimDetail() {
    var c = null, lines = [];
    if (TAB === 'preview' && PREVIEW) { c = PREVIEW.claims[CL]; if (c) lines = (c.flags || []).map(function (f) { return [f.severity, f.message]; }); }
    if (TAB === 'worklist' && WORK[CL]) { c = WORK[CL]; lines = (c.flags || []).map(function (m) { return ['warn', m]; }); }
    if (!c) return '';
    var name = c.patient_name || c.patient, id = c.claim_id || c.id;
    return '<div class="dp-sec"><div class="dp-h">Claim ' + E(id) + '</div><div style="font-weight:600;margin-bottom:6px">' + E(name) + '</div>' +
      (lines.length ? lines.map(function (l) { return '<div class="ct-prob">' + (l[0] === 'error' ? U.err : l[0] === 'warn' ? U.warn : '') + ' ' + E(l[1]) + '</div>'; }).join('') : '<div class="s-grn">' + U.I.check + ' Nothing found.</div>') + '</div>';
  }

  /* ---------- the page ---------- */
  function draw() {
    var cur = live(), p = PREVIEW, s = p ? p.summary : null;
    var strip = U.strip([['EMR Connection', cur ? E(cur.label) : 'Upload only', cur ? 'live' : 'no live connection yet', cur ? 'grn' : ''], ['Saved Claims', SUM ? SUM.open : '-', 'waiting in the worklist'], ['Flagged', SUM ? SUM.flagged : '-', 'need a fix or review', SUM && SUM.flagged ? 'amb' : ''],
      ['Charges At Risk', SUM ? M(SUM.at_risk) : '-', SUM && SUM.by_payer && SUM.by_payer[0] ? 'most at ' + E(SUM.by_payer[0].payer) : ''], ['In This Preview', s ? s.total : '-', s ? s.clean + ' clean · ' + s.fix + ' fix first' : 'nothing loaded']]);
    var pullable = action(emr(SEL), 'sync');
    var tb = U.toolbar('Connect EMR', [['Upload File', 'upload', { pri: 1, id: 'cx-upload' }], pullable ? ['Pull Last 30 Days', 'refresh', { id: 'cx-pull' }] : null, '|', ['Get Template', 'download', { id: 'cx-template' }], ['Refresh', 'refresh', { id: 'cx-refresh' }], '|', ['Browser Extension', 'send', { id: 'cx-ext' }], ['Claims', 'send', { href: '/v4/revenue/claims' }]].filter(Boolean),
      '<span class="tc s-mut" title="Claims are checked before anything is saved">Checked Before It Leaves</span>');
    var left = U.pane('Your EMR', '', '', U.queue([{ h: 'Choose Yours', items: EMRS.map(function (e) { return { key: e.key, label: e.label, n: e.live ? 'Live' : '', color: e.live ? 'grn' : null }; }) }], SEL));
    var body, footer = '';
    if (TAB === 'preview') {
      body = p ? previewGrid() : howTo();
      if (p) footer = '<span class="mut">' + E(p.file || '') + (p.notes && p.notes.length ? ' · ' + E(p.notes.join(' ')) : '') + (p.unmapped_columns && p.unmapped_columns.length ? ' · Columns not used: ' + E(p.unmapped_columns.join(', ')) : '') + '</span>' +
        '<span style="margin-left:auto;display:flex;gap:6px"><button type="button" class="btn" id="cx-save-clean">Save Clean Claims</button><button type="button" class="btn pri" id="cx-save-all">Save All To Worklist</button></span>';
    } else body = workGrid();
    var mid = '<section class="pane">' + U.tabs([['preview', 'Preview', p ? p.claims.length : ''], ['worklist', 'Worklist', WORK.length || '']], TAB) +
      '<div class="pbody">' + (ERR ? '<div class="s-red" style="padding:12px">' + E(ERR) + '</div>' : BUSY ? '<div class="mut" style="padding:12px">Working. Reading and checking every claim…</div>' : body) + '</div>' + (footer ? '<div class="pf" style="display:flex;align-items:center;gap:8px">' + footer + '</div>' : '') + '</section>';
    app.innerHTML = U.header('connect') + tb + strip + '<div class="main" style="grid-template-columns:220px minmax(0,1fr) 360px">' + left + mid + emrDetail() + '</div>';
    U.fit(); U.search();
  }

  /* ---------- actions ---------- */
  function setPreview(j) { PREVIEW = j; TAB = 'preview'; CL = 0; BUSY = false; ERR = ''; draw(); U.toast(j.summary.total + ' claims checked. Review them, then save.', 'ok'); }
  function fail(e) { BUSY = false; draw(); U.toast(e.message, 'warn'); }
  function upload() {
    U.pickFile('.csv,.xlsx,.xlsm,.xls', false, function (files) {
      if (!files[0]) return; var fd = new FormData(); fd.append('file', files[0]); fd.append('emr', SEL); BUSY = true; draw();
      api('POST', '/api/integrations/import/preview', null, fd).then(setPreview, fail);
    });
  }
  function pull(days) {
    BUSY = true; draw(); U.toast('Pulling claims from ' + (emr(SEL) || {}).label + '. This can take a minute…', 'busy');
    api('POST', '/api/integrations/' + SEL + '/sync', { days: days || 30 }).then(setPreview, fail);
  }
  function save(onlyClean) {
    if (!PREVIEW) return;
    api('POST', '/api/integrations/import/commit', { emr: SEL, only_clean: onlyClean, claims: PREVIEW.claims }).then(function (r) {
      PREVIEW = null; TAB = 'worklist'; CL = 0; U.toast('Saved ' + r.created + ' new, updated ' + r.updated + (r.skipped ? ', skipped ' + r.skipped + ' that need fixes' : '') + '.', 'ok'); load();
    }, function (e) { U.toast(e.message, 'warn'); });
  }

  V.pages.connect = function () {
    V.redraw = draw; draw(); load();
    U.on(app, '[data-q]', 'click', function (el) { SEL = el.dataset.q; draw(); });
    U.on(app, '[data-tab]', 'click', function (b) { TAB = b.dataset.tab; CL = 0; draw(); });
    U.on(app, 'tr[data-pc]', 'click', function (tr) { CL = +tr.dataset.pc; draw(); });
    U.on(app, 'tr[data-wc]', 'click', function (tr) { CL = +tr.dataset.wc; draw(); });
    U.on(app, '#cx-upload, #cx-upload2', 'click', upload);
    U.on(app, '#cx-pull', 'click', function () { pull(30); });
    U.on(app, '#cx-template', 'click', function () { location.href = '/api/integrations/template.csv'; });
    U.on(app, '#cx-ext', 'click', function () {
      var has = document.documentElement.getAttribute('data-althais-extension');
      var tokenBtn = ['Create Token', has ? '' : 'pri', function () {
        api('POST', '/api/extension/token').then(function (j) {
          var o = document.getElementById('ext-out');
          o.innerHTML = '<label class="ask-l" for="ext-tok">Pairing token</label><input id="ext-tok" readonly style="width:100%" value="' + U.esc(j.token) + '"><div class="t2" style="margin-top:6px;font-size:12px">Copy it now. It will not be shown again.</div>';
          var i = document.getElementById('ext-tok'); i.focus(); i.select();
        }, function (e) { U.toast(e.message, 'warn'); });
        return false;
      }];
      var connectBtn = ['Connect This Browser', 'pri', function (close) {
        var o = document.getElementById('ext-out');
        o.innerHTML = '<div class="t2">Connecting…</div>';
        api('POST', '/api/extension/token').then(function (j) {
          var done = false;
          var on = function (ev) {
            if (ev.source !== window || ev.origin !== location.origin || !ev.data || ev.data.type !== 'althais:paired') return;
            done = true; window.removeEventListener('message', on);
            if (ev.data.ok) { U.toast('This browser is connected. Open a claim in your EMR to try it.', 'ok'); close(); }
            else o.innerHTML = '<div class="s-red">' + U.esc(ev.data.error || 'The extension could not connect.') + '</div>';
          };
          window.addEventListener('message', on);
          window.postMessage({ type: 'althais:pair', token: j.token }, location.origin);
          setTimeout(function () { if (!done) { window.removeEventListener('message', on); o.innerHTML = '<div class="s-red">The extension didn\'t answer. Reload this page and try again, or use Create Token.</div>'; } }, 8000);
        }, function (e) { o.innerHTML = '<div class="s-red">' + U.esc(e.message) + '</div>'; });
        return false;
      }];
      U.modal('Browser Extension (prototype)',
        '<p class="t2" style="margin:0 0 8px">The extension checks each claim as you open it in your EMR. A small Althais badge on the claim screen shows Ready to submit, Worth a look or Fix first, and outlines the fields to fix. Checks send only codes, payer, date and charge, and nothing is stored. From the same badge, a biller can also code a visit note, fill the codes into the EMR, and create a claim for Althais to send after they approve it.</p>' +
        (has ? '<p class="t2" style="margin:0 0 8px"><b>The extension is installed in this browser.</b> Press Connect This Browser to link it to your Althais account. No copying needed.</p>'
             : '<p class="t2" style="margin:0 0 8px">Install the extension in Chrome or Edge, then reload this page to connect it in one click. Or create a pairing token and paste it into the extension settings. A token is shown once; your last five stay active, and Revoke All stops them all.</p>') +
        '<p class="t2" style="margin:0 0 8px">Use test data only until your practice has the right agreements in place. To try it with fake patients, open the <a href="/static/mock-emr/index.html" target="_blank" rel="noopener">test claim screens</a>. Each one has a known problem to catch.</p>' +
        '<div id="ext-out"></div>',
        [['Revoke All', '', function () { api('POST', '/api/extension/revoke').then(function () { U.toast('All extension tokens revoked.', 'ok'); }, function (e) { U.toast(e.message, 'warn'); }); return false; }]]
          .concat(has ? [tokenBtn, connectBtn] : [tokenBtn])
          .concat([['Close', '', function () {}]]), { width: 540 });
    });
    U.on(app, '#cx-refresh', 'click', function () { load().then(function () { U.toast('Up to date.', 'ok'); }); });
    U.on(app, '#cx-save-clean', 'click', function () { save(true); });
    U.on(app, '#cx-save-all', 'click', function () {
      var n = PREVIEW ? PREVIEW.claims.filter(function (c) { return c.verdict === 'fix'; }).length : 0;
      if (n) U.confirm('Save All To Worklist', '<p class="cf">' + n + ' claim' + (n === 1 ? ' needs' : 's need') + ' a fix before it can go out. Save them anyway so staff can fix them in the worklist?</p>', 'Save All', function () { save(false); });
      else save(false);
    });
    U.on(app, '[data-act]', 'click', function (b) {
      var kind = b.dataset.act, e = emr(SEL);
      if (kind === 'sync') return pull(+b.dataset.days || 30);
      if (kind === 'practice') return U.ask('Connect ' + e.label, 'Practice ID', '', function (v) {
        api('POST', '/api/integrations/' + SEL + '/connect', { practice_id: v }).then(function () { U.toast(e.label + ' connected.', 'ok'); load(); }, function (er) { U.toast(er.message, 'warn'); });
      }, { ok: 'Connect', ph: 'Digits only. The test practice has its own ID.', html: '<p class="t2" style="margin:0 0 10px">Your practice ID is in ' + E(e.label) + '. Althais reads claims only and never changes your records.</p>' });
      if (kind === 'tebra') return U.form('Connect Tebra', [
          { k: 'user', label: 'Tebra API username', req: 1, span: 2, ph: 'A separate Tebra user just for Althais is best' },
          { k: 'password', label: 'Password', type: 'password', req: 1, span: 2 },
          { k: 'customer_key', label: 'Customer Key', req: 1, span: 2, help: 'Your Tebra System Administrator generates this in Tebra. See Tebra Help: Get Started: Tebra SOAP APIs.' },
          { k: 'practice', label: 'Practice name (optional)', span: 2, help: 'Only if your Tebra account has more than one practice. Type it exactly as Tebra shows it.' }
        ], {}, function (v) {
          api('POST', '/api/integrations/tebra/connect', v).then(function () { U.toast('Tebra connected.', 'ok'); load(); }, function (er) { U.toast(er.message, 'warn'); });
        }, { save: 'Connect', width: 560, intro: 'Althais reads charges only and never changes anything in Tebra. Your login and key are stored encrypted and never shown again.' });
      if (kind === 'disconnect') return U.confirm('Disconnect ' + e.label, '<p class="cf">Althais stops pulling claims from ' + E(e.label) + '. Claims you already saved stay in the worklist.</p>', 'Disconnect', function () {
        api('POST', '/api/integrations/' + SEL + '/disconnect').then(function () { PREVIEW = null; U.toast('Disconnected.', 'ok'); load(); }, function (er) { U.toast(er.message, 'warn'); });
      }, { danger: 1 });
    });
  };
})();
