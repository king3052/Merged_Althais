/* v4 working preview, part 9: Settings › Access Log, plus the "opened this chart / claim / visit" events the other pages send.
   The log itself is kept on the server (audit.py): every data API call, sign-ins and sign-outs, and the records people open.
   Only the clinic's admins can read it. */
(function () {
  var app = V.app, E = U.esc;

  /* ---------- pages report what a person opened (one event per record per couple of minutes) ---------- */
  var last = {};
  V.seen = function (what, id, patient) {
    if (!id) return;
    var key = what + ':' + id, now = Date.now();
    if (last[key] && now - last[key] < 60000) return;
    last[key] = now;
    try {
      fetch('/api/access-log/view', { method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ what: what, id: String(id), patient: patient ? String(patient) : '' }) }).catch(function () {});
    } catch (e) {}
  };

  /* ---------- the Access Log page ---------- */
  var ROWS = [], PEOPLE = [], MORE = false, SEL = null, ERR = '', BUSY = false, F = { user: '', action: '', record: '', since: '', until: '' }, CHECK = null;
  var ACTIONS = [['view', 'Viewed'], ['create', 'Created'], ['change', 'Changed'], ['delete', 'Deleted'], ['export', 'Exported'], ['sign_in', 'Signed in'], ['sign_in_failed', 'Failed sign-in'], ['sign_out', 'Signed out']];
  var LABEL = {}; ACTIONS.forEach(function (a) { LABEL[a[0]] = a[1]; });
  var LVL = { delete: 'amb', export: 'blue', sign_in_failed: 'amb', change: '', create: '', view: 'mut' };

  function qs(extra) {
    var p = []; Object.keys(F).forEach(function (k) { if (F[k]) p.push(k + '=' + encodeURIComponent(F[k])); });
    if (extra) p.push(extra);
    return p.length ? '?' + p.join('&') : '';
  }
  function load(append) {
    BUSY = true; if (!append) draw();
    var before = append && ROWS.length ? 'before=' + ROWS[ROWS.length - 1].id : '';
    return fetch('/api/access-log' + qs(before), { credentials: 'same-origin' }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || j.detail || 'Couldn’t load the access log.'); return j; });
    }).then(function (j) {
      ROWS = append ? ROWS.concat(j.rows) : j.rows; PEOPLE = j.people || []; MORE = !!j.more; ERR = ''; BUSY = false;
      if (!append) SEL = ROWS[0] ? ROWS[0].id : null; draw();
    }, function (e) { ERR = e.message; BUSY = false; draw(); });
  }
  function when(iso) {
    if (!iso) return '';
    var d = new Date(iso); if (isNaN(d)) return E(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) + ' ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  }
  function browser(ua) {
    ua = ua || ''; if (!ua) return '';
    var b = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'Other';
    var os = /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) && !/iPhone|iPad/.test(ua) ? 'Mac' : /iPhone|iPad/.test(ua) ? 'iPhone/iPad' : /Android/.test(ua) ? 'Android' : /Linux/.test(ua) ? 'Linux' : '';
    return b + (os ? ' on ' + os : '');
  }
  function grid() {
    var rows = ROWS.map(function (r) {
      return { sel: r.id === SEL, attrs: ' data-row="' + r.id + '"', cells: ['<span class="mono">' + when(r.at) + '</span>', E(r.user || '(unknown)') + (r.on_behalf ? ' <span class="mut">· ' + E(r.on_behalf) + '</span>' : ''),
        U.st(LABEL[r.action] || r.action, LVL[r.action] || ''), E(r.what), '<span class="mono">' + E(r.record) + '</span>', r.status >= 400 ? '<span class="s-blue">' + r.status + '</span>' : '<span class="s-mut">' + (r.status || '') + '</span>',
        '<span class="mono s-mut">' + E(r.ip) + '</span>'] };
    });
    return U.grid(['Time', 'Person', 'Action', 'What', 'Record', 'Result', 'IP address'], rows, { click: 1 })
      .replace('<tbody></tbody>', '<tbody>' + U.empty(7, F.user || F.action || F.record || F.since || F.until ? 'Nothing matches these filters.' : 'Nothing recorded yet.') + '</tbody>');
  }
  function detail() {
    var r = ROWS.filter(function (x) { return x.id === SEL; })[0];
    var about = '<div class="dp-sec"><div class="dp-h">What this records</div><div class="t2" style="line-height:1.5">Every time someone in your clinic opens a patient chart, a visit or a claim, changes or deletes data, exports, or signs in or out. ' +
      'It records who, what, when and from where, never the contents of the chart. Rows can’t be edited or deleted in Althais, and each one is signed so changes made outside Althais show up in Check Integrity. Keep exports for at least six years.</div></div>';
    if (!r) return U.pane('Details', '', '', '<div class="blank">Select a row.</div>' + about);
    return U.pane('Details', '', '', '<div class="dp-sec">' + U.kv([['Time', when(r.at)], ['Person', E(r.user || '(unknown)') + (r.role ? ' <span class="mut">(' + E(r.role) + ')</span>' : '')], ['On behalf of', E(r.on_behalf || '-')],
      ['Action', E(LABEL[r.action] || r.action)], ['What', E(r.what || '-')], ['Record', '<span class="mono">' + E(r.record || '-') + '</span>'], ['Address', '<span class="mono" style="word-break:break-all">' + E((r.method ? r.method + ' ' : '') + r.path) + '</span>'],
      ['Result', E(r.status ? String(r.status) : '-')], ['IP address', '<span class="mono">' + E(r.ip || '-') + '</span>'], ['Browser', E(browser(r.agent) || '-')], ['Note', E(r.detail || '-')]]) + '</div>' + about);
  }
  function filters() {
    var inp = function (k, ph, type) { return '<label class="chipf">' + ph + ' <input data-fi="' + k + '" type="' + (type || 'text') + '" value="' + E(F[k]) + '"' + (type ? '' : ' placeholder="MRN, claim #…" style="width:130px"') + '></label>'; };
    return '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line,#e3e7ec)">' +
      U.chip('Person', 'user', PEOPLE, F.user) + U.chip('Action', 'action', ACTIONS, F.action) + inp('record', 'Record') + inp('since', 'From', 'date') + inp('until', 'To', 'date') +
      (F.user || F.action || F.record || F.since || F.until ? '<button type="button" class="btn" id="al-clear">Clear</button>' : '') + '</div>';
  }
  function draw() {
    var me = window.__ALTHAIS_USER__ || {};
    var tb = U.toolbar('Access Log', [['Export CSV', 'download', { pri: 1, id: 'al-csv' }], ['Check Integrity', 'check', { id: 'al-verify' }], ['Refresh', 'refresh', { id: 'al-refresh' }]],
      '<span class="tc s-mut">Kept by Althais for your clinic</span>');
    var today = ROWS.filter(function (r) { return (r.at || '').slice(0, 10) === new Date().toISOString().slice(0, 10); });
    var strip = U.strip([['Shown', ROWS.length + (MORE ? '+' : ''), 'most recent first'], ['People', PEOPLE.length, 'who used Althais'], ['Today', today.length, 'in this list'],
      ['Failed Sign-Ins', ROWS.filter(function (r) { return r.action === 'sign_in_failed'; }).length, 'in this list', ROWS.some(function (r) { return r.action === 'sign_in_failed'; }) ? 'amb' : ''],
      ['Integrity', CHECK ? (CHECK.altered_count ? CHECK.altered_count + ' altered' : 'All ' + CHECK.checked + ' intact') : 'Not checked', CHECK ? 'just now' : 'press Check Integrity', CHECK ? (CHECK.altered_count ? 'amb' : 'grn') : '']]);
    var body = ERR ? '<div class="s-red" style="padding:12px">' + E(ERR) + '</div>' : BUSY && !ROWS.length ? '<div class="mut" style="padding:12px">Loading…</div>' : grid() +
      (MORE ? '<div style="padding:10px"><button type="button" class="btn" id="al-more">Load More</button></div>' : '');
    var mid = '<section class="pane">' + filters() + '<div class="pbody">' + body + '</div></section>';
    app.innerHTML = U.header('accesslog') + tb + strip + '<div class="main" style="grid-template-columns:minmax(0,1fr) 360px">' + mid + detail() + '</div>';
    U.fit(); U.search();
    if (me.role && me.role !== 'admin' && !ERR) { ERR = 'Only your clinic’s admins can see the access log.'; draw(); }
  }

  V.pages.accesslog = function () {
    V.redraw = draw; draw(); load();
    U.on(app, 'tr[data-row]', 'click', function (tr) { SEL = +tr.dataset.row; draw(); });
    U.on(app, '[data-f]', 'change', function (el) { F[el.dataset.f] = el.value; load(); });
    U.on(app, '[data-fi]', 'change', function (el) { F[el.dataset.fi] = el.value.trim(); load(); });
    U.on(app, '#al-clear', 'click', function () { F = { user: '', action: '', record: '', since: '', until: '' }; load(); });
    U.on(app, '#al-more', 'click', function () { load(true); });
    U.on(app, '#al-refresh', 'click', function () { load(); });
    U.on(app, '#al-csv', 'click', function () { location.href = '/api/access-log.csv' + qs(); });
    U.on(app, '#al-verify', 'click', function () {
      fetch('/api/access-log/verify', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) {
        if (j.error) return U.toast(j.error, 'warn');
        CHECK = j; draw();
        U.toast(j.altered_count ? j.altered_count + ' row' + (j.altered_count === 1 ? ' was' : 's were') + ' changed outside Althais. Rows: ' + j.altered.slice(0, 10).join(', ') : 'All ' + j.checked + ' rows are intact.', j.altered_count ? 'warn' : 'ok');
      }, function () { U.toast('Couldn’t check right now.', 'warn'); });
    });
  };
})();
