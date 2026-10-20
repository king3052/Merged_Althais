/* Shared v4 components. Each returns an HTML string; pages.js puts them together. */
(function () {
  var U = {};
  U.esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  U.money = function (n) { return '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
  U.money0 = function (n) { return '$' + Math.round(n || 0).toLocaleString('en-US'); };
  U.pct = function (n, d) { return (n * 100).toFixed(d == null ? 1 : d) + '%'; };
  U.initials = function (name) { var p = String(name).split(/,\s*/); return ((p[1] || '')[0] || '') + ((p[0] || '')[0] || ''); };

  var svg = function (d, w) { return '<svg class="ico" width="' + (w || 13) + '" height="' + (w || 13) + '" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>'; };
  U.I = {
    search: svg('<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>'),
    mic: svg('<rect x="6" y="1.5" width="4" height="8" rx="2"/><path d="M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2.5"/>'),
    plus: svg('<path d="M8 3v10M3 8h10"/>'),
    send: svg('<path d="M14 2L7 9M14 2l-4.5 12L7 9 2 6.5z"/>'),
    print: svg('<path d="M4.5 6V2h7v4M4.5 12H3a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-1.5"/><path d="M4.5 9.5h7V14h-7z"/>'),
    user: svg('<circle cx="8" cy="5.5" r="2.8"/><path d="M2.5 14c.8-2.6 3-4 5.5-4s4.7 1.4 5.5 4"/>'),
    cal: svg('<rect x="2" y="3" width="12" height="11" rx="1"/><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3"/>'),
    fax: svg('<path d="M4 6V2h6l2 2v2"/><rect x="2" y="6" width="12" height="7" rx="1"/><path d="M5 9.5h3"/>'),
    upload: svg('<path d="M8 11V2.5M4.5 6L8 2.5 11.5 6M2.5 13.5h11"/>'),
    download: svg('<path d="M8 2.5V11M4.5 7.5L8 11l3.5-3.5M2.5 13.5h11"/>'),
    refresh: svg('<path d="M13.5 8A5.5 5.5 0 1 1 11.8 4M13.5 2v3.5H10"/>'),
    check: svg('<path d="M3 8.5l3 3 7-7"/>'),
    mail: svg('<rect x="2" y="3.5" width="12" height="9" rx="1"/><path d="M2.5 4.5L8 9l5.5-4.5"/>'),
    save: svg('<path d="M3 2h8l2 2v10H3z"/><path d="M5 2v4h5V2M5 14V9.5h6V14"/>'),
    pencil: svg('<path d="M11 2.5l2.5 2.5L6 12.5H3.5V10z"/>'),
    clip: svg('<rect x="3" y="2.5" width="10" height="12" rx="1"/><path d="M6 2.5V1.5h4v1M5.5 7h5M5.5 10h5"/>'),
    chat: svg('<path d="M2.5 3h11v8h-6l-3 2.5V11h-2z"/>'),
    gear: svg('<circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/>'),
    undo: svg('<path d="M4 6h6.5a3.5 3.5 0 0 1 0 7H6M4 6l3-3M4 6l3 3"/>'),
    pause: svg('<path d="M6 3.5v9M10 3.5v9"/>'),
    out: svg('<path d="M6 2.5H3.5v11H6M10.5 5l3 3-3 3M13.5 8H6.5"/>'),
    x: svg('<path d="M4 4l8 8M12 4l-8 8"/>'),
  };
  // status marks: filled red circle with "!", amber triangle
  U.err = '<svg class="ico" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="#1d5bb8"/><path d="M8 4.5v4.2" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="11.3" r="1" fill="#fff"/></svg>';
  U.warn = '<svg class="ico" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l7 12.5H1z" fill="#a15c07"/><path d="M8 6v3.5" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/><circle cx="8" cy="11.6" r=".95" fill="#fff"/></svg>';
  U.mark = function (lvl) { return lvl === 'red' ? U.err : lvl === 'amb' ? U.warn : ''; };
  U.st = function (text, lvl) { return '<span class="tc s-' + (lvl === 'red' ? 'blue' : lvl || 'mut') + '">' + U.mark(lvl) + U.esc(text) + '</span>'; };
  U.SQ = { red: '#1d5bb8', amb: '#a15c07', grn: '#17784a', blue: '#1d5bb8', lblue: '#8fb0e3', gray: '#a3adb8', mut: '#a3adb8', '': '#a3adb8' };

  var NAV = [
    ['overview', 'Overview', '/v4/overview', []],
    ['emr', 'EMR', '/v4/emr/schedule', [['schedule', 'Schedule', '/v4/emr/schedule'], ['patients', 'Patients', '/v4/emr/patients'], ['chart', 'Patient chart', '/v4/emr/chart'], ['encounter', 'Encounter', '/v4/emr/encounter'], ['documents', 'Documents', '/v4/emr/documents']]],
    ['revenue', 'Revenue', '/v4/revenue/claims', [['claims', 'Claims', '/v4/revenue/claims'], ['tracker', 'Claim Tracker', '/v4/revenue/claim-tracker'], ['denials', 'Denials', '/v4/revenue/denials'], ['rules', 'Payer rules', '/v4/revenue/payer-rules'], ['codenote', 'Code a Note', '/v4/revenue/code-a-note'], ['billing', 'Billing activation', '/v4/revenue/billing-activation'], ['connect', 'Connect EMR', '/v4/revenue/connect']]],
    ['staff', 'Staff', '/v4/staff/team', []],
    ['settings', 'Settings', '/v4/settings', []],
  ];
  U.header = function (page) {
    return '<header class="hd"><a class="wm" href="/v4/overview">ALTHAIS</a>' + NAV.map(function (n) {
      var on = n[0] === page || n[3].some(function (s) { return s[0] === page; });
      return '<div class="hd-tab' + (on ? ' on' : '') + '"><a href="' + n[2] + '">' + n[1] + '</a>' +
        (n[3].length ? '<div class="hd-menu">' + n[3].map(function (s) { return '<a href="' + s[2] + '" class="' + (s[0] === page ? 'on' : '') + '">' + s[1] + '</a>'; }).join('') + '</div>' : '') + '</div>';
    }).join('') +
      '<div class="hd-end"><label class="hd-search">' + U.I.search + '<input id="hd-q" placeholder="Search patient, MRN, claim #" aria-label="Search" autocomplete="off"><div class="hd-res" id="hd-res"></div></label>' +
      '<span class="hd-user">' + U.esc(D.S.clinic.name) + ' <span class="hd-av">' + U.esc(U.initials2(D.S.clinic.user)) + '</span> ' + U.esc(D.S.clinic.user) + '</span>' +
      '<a class="hd-out" href="/logout" id="signout">' + U.I.out + 'Sign Out</a></div></header>' +
      // the active section's pages as tabs, always visible (Patients, Schedule… one click away)
      (function () { var sec = NAV.filter(function (n) { return n[0] === page || n[3].some(function (s) { return s[0] === page; }); })[0];
        return sec && sec[3].length ? '<nav class="snav">' + sec[3].map(function (s) { return '<a href="' + s[2] + '" class="' + (s[0] === page ? 'on' : '') + '">' + s[1] + '</a>'; }).join('') + '</nav>' : ''; })();
  };
  // actions: [label, icon, {pri, id, href}]
  U.askAlthea = function (start, hint) {
    var fab = document.getElementById('althea-fab'), panel = fab && fab.querySelector('.alt-panel');
    if (!fab) return U.toast('Althea isn’t available on this page.', 'warn');
    if (!panel || panel.offsetParent === null || panel.classList.contains('hidden')) { var l = fab.querySelector('.alt-launch'); if (l) l.click(); }
    setTimeout(function () { var i = document.getElementById('althea-text-input'); if (!i) return; i.value = start || ''; if (hint) i.placeholder = hint; i.focus(); i.setSelectionRange(i.value.length, i.value.length); }, 250);
  };
  U.toolbar = function (title, actions, end) {
    var html = '<div class="tb"><span class="tb-title">' + U.esc(title) + '</span><span class="tb-sep"></span>';
    actions.forEach(function (a) {
      if (a === '|') { html += '<span class="tb-sep"></span>'; return; }
      var o = a[2] || {}, tag = o.href ? 'a' : 'button';
      var btn = '<' + tag + (o.href ? ' href="' + o.href + '"' : ' type="button"') + ' class="act' + (o.pri ? ' pri' : '') + '"' + (o.id ? ' id="' + o.id + '"' : '') + '>' + (U.I[a[1]] || '') + U.esc(a[0]) + '</' + tag + '>';
      // {menu: [[label, id, icon]]}: more choices on hover, under the button
      html += o.menu ? '<span class="tb-dd">' + btn + '<span class="tb-menu">' + o.menu.map(function (m) { return '<button type="button" id="' + m[1] + '">' + (U.I[m[2]] || m[2] || '') + U.esc(m[0]) + '</button>'; }).join('') + '</span></span>' : btn;
    });
    return html + '<span class="tb-end">' + (end || '') + '</span></div>';
  };
  U.strip = function (cells) {
    return '<div class="ms">' + cells.map(function (c) { return '<div><span>' + U.esc(c[0]) + '</span><b class="' + (c[3] ? 's-' + (c[3] === 'red' ? 'blue' : c[3]) : '') + '">' + c[1] + '</b>' + (c[2] ? '<small>' + c[2] + '</small>' : '') + '</div>'; }).join('') + '</div>';
  };
  // pane(title, sub, end, body, {footer, id, cls, bodyId})
  U.pane = function (title, sub, end, body, o) {
    o = o || {};
    return '<section class="pane ' + (o.cls || '') + '"' + (o.id ? ' id="' + o.id + '"' : '') + (o.style ? ' style="' + o.style + '"' : '') + '>' +
      (title != null ? '<div class="ph">' + title + (sub ? ' <span class="sub">' + sub + '</span>' : '') + (end ? '<span class="end">' + end + '</span>' : '') + '</div>' : '') +
      '<div class="pbody"' + (o.bodyId ? ' id="' + o.bodyId + '"' : '') + '>' + body + '</div>' + (o.footer ? '<div class="pf">' + o.footer + '</div>' : '') + (o.after || '') + '</section>';
  };
  U.sect = function (title, sub, body, end) {
    return '<div class="sect"><div class="sect-h">' + title + (sub ? ' <span class="sub">' + sub + '</span>' : '') + (end ? '<span style="margin-left:auto;font-weight:400">' + end + '</span>' : '') + '</div>' + body + '</div>';
  };
  // groups: [{h, items: [{key, label, n, color, indent}]}]
  U.queue = function (groups, on) {
    return '<div class="ql">' + groups.map(function (g) {
      return (g.h ? '<div class="ql-h">' + U.esc(g.h) + '</div>' : '') + g.items.map(function (i) {
        return '<div class="ql-i' + (i.key === on ? ' on' : '') + '" data-q="' + U.esc(i.key) + '">' + (i.color === null ? '' : '<span class="sq" style="background:' + (U.SQ[i.color] || i.color || U.SQ.gray) + '"></span>') +
          '<span' + (i.indent ? ' style="padding-left:16px"' : '') + '>' + U.esc(i.label) + '</span><span class="c">' + (i.n == null ? '' : i.n) + '</span></div>';
      }).join('');
    }).join('') + '</div>';
  };
  U.sum = function (rows) { return '<div class="sum">' + rows.map(function (r) { return '<div><span>' + U.esc(r[0]) + '</span><b>' + r[1] + '</b></div>'; }).join('') + '</div>'; };
  // cols: [label | [label, cls]], rows: [{cells, sel, attrs, checked}]
  U.grid = function (cols, rows, o) {
    o = o || {};
    var head = '<thead><tr>' + (o.cb ? '<th class="cb">' + (rows.length ? '<input type="checkbox" data-cball aria-label="Select all"' + (rows.length && rows.every(function (r) { return r.checked; }) ? ' checked' : '') + '>' : '') + '</th>' : '') + cols.map(function (c) { c = [].concat(c); return '<th class="' + (c[1] || '') + '">' + U.esc(c[0]) + '</th>'; }).join('') + '</tr></thead>';
    var body = '<tbody>' + rows.map(function (r) {
      return '<tr class="' + (r.sel ? 'sel ' : '') + (r.click !== false && o.click ? 'click' : '') + '"' + (r.attrs || '') + '>' + (o.cb ? '<td class="cb"><input type="checkbox"' + ((o.selChecks === false ? r.checked : r.sel || r.checked) ? ' checked' : '') + ' aria-label="Select row"></td>' : '') +
        r.cells.map(function (c, i) { var col = [].concat(cols[i]); return '<td class="' + (col[1] || '') + '">' + c + '</td>'; }).join('') + '</tr>';
    }).join('') + '</tbody>';
    return '<table class="g' + (o.tight ? ' tight' : '') + '">' + head + body + '</table>';
  };
  U.kv = function (rows) { return '<div class="kv">' + rows.map(function (r) { return '<div><span>' + U.esc(r[0]) + '</span><b class="' + (r[2] ? 's-' + r[2] : '') + '" style="font-weight:400">' + r[1] + '</b></div>'; }).join('') + '</div>'; };
  U.kv3 = function (rows) { return '<div class="kv3">' + rows.map(function (r) { return '<div><span>' + U.esc(r[0]) + '</span><b class="' + (r[2] ? 's-' + r[2] : '') + '">' + r[1] + '</b></div>'; }).join('') + '</div>'; };
  U.tabs = function (tabs, on) { return '<div class="st">' + tabs.map(function (t) { t = [].concat(t); return '<button type="button" data-tab="' + t[0] + '" class="' + (t[0] === on ? 'on' : '') + '">' + U.esc(t[1] || t[0]) + (t[2] ? '<span class="n">' + t[2] + '</span>' : '') + '</button>'; }).join('') + '</div>'; };
  // filter chip: "Payer All ▾" as a real dropdown; opts [[value, label]]
  U.chip = function (label, key, opts, val) {
    return '<label class="chipf">' + U.esc(label) + ' <select data-f="' + key + '"><option value="">All</option>' + opts.map(function (o) { o = [].concat(o); return '<option value="' + U.esc(o[0]) + '"' + (String(o[0]) === String(val || '') ? ' selected' : '') + '>' + U.esc(o[1] == null ? o[0] : o[1]) + '</option>'; }).join('') + '</select></label>';
  };
  U.uniq = function (a) { var o = []; a.forEach(function (x) { if (x && o.indexOf(x) < 0) o.push(x); }); return o.sort(); };
  U.initials2 = function (n) { var p = String(n || '').replace(',', ' ').split(/\s+/).filter(Boolean); return ((p[0] || '?')[0] + (p[1] ? p[1][0] : '')).toUpperCase(); };
  U.empty = function (n, text) { return '<tr><td colspan="' + n + '" class="s-mut" style="padding:9px 8px">' + U.esc(text) + '</td></tr>'; };
  U.download = function (name, text, mime) { var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type: mime || 'text/csv' })); a.download = name; document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500); };
  U.csv = function (rows) { return rows.map(function (r) { return r.map(function (c) { c = String(c == null ? '' : c); return /[",\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c; }).join(','); }).join('\n'); };
  U.pickFile = function (accept, multiple, cb) { var i = document.createElement('input'); i.type = 'file'; if (accept) i.accept = accept; i.multiple = !!multiple; i.onchange = function () { cb([].slice.call(i.files)); }; i.click(); };
  U.readFile = function (f, asText) { return new Promise(function (res) { var r = new FileReader(); r.onload = function () { res(r.result); }; if (asText) r.readAsText(f); else r.readAsDataURL(f); }); };
  U.size = function (n) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; };
  // modal(title, body, buttons [[label, 'pri'|'' , fn(close) -> false keeps it open]], {wide})
  U.modal = function (title, body, buttons, o) {
    o = o || {};
    var w = document.createElement('div'); w.className = 'mdl-wrap';
    w.innerHTML = '<div class="mdl' + (o.wide ? ' wide' : '') + '"' + (o.width ? ' style="width:' + o.width + 'px"' : '') + ' role="dialog" aria-modal="true"><div class="mdl-h">' + U.esc(title) + '<button type="button" class="mdl-x" aria-label="Close">' + U.I.x + '</button></div><div class="mdl-b">' + body + '</div>' +
      '<div class="dp-f">' + (buttons || []).map(function (b, i) { return '<button type="button" class="btn ' + (b[1] || '') + '" data-mb="' + i + '">' + U.esc(b[0]) + '</button>'; }).join('') + '</div></div>';
    var close = function () { w.remove(); document.removeEventListener('keydown', esc); };
    var esc = function (e) { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', esc);
    w.addEventListener('click', function (e) { if (e.target === w || e.target.closest('.mdl-x')) close(); var b = e.target.closest('[data-mb]'); if (b) { var r = buttons[+b.dataset.mb][2](close, w); if (r !== false) close(); } });
    document.body.appendChild(w);
    var f = w.querySelector('input:not([type=hidden]):not([readonly]),select,textarea'); if (f) f.focus();
    return w;
  };
  // in-app replacements for the browser's confirm() / prompt(): same look as every other dialog
  // U.confirm(title, html, okLabel, onOk, {danger, cancel})
  U.confirm = function (title, html, ok, fn, o) {
    o = o || {};
    return U.modal(title, html, [[o.cancel || 'Cancel', '', function () {}], [ok || 'OK', o.danger ? 'pri danger' : 'pri', function () { fn(); }]], { width: o.width || 440 });
  };
  // U.ask(title, label, default, onValue(text), {ok, ph, must: 'DELETE', type, html})
  U.ask = function (title, label, def, fn, o) {
    o = o || {};
    var w = U.modal(title, (o.html || '') + '<label class="ask-l" for="ask-v">' + U.esc(label) + '</label><input id="ask-v" type="' + (o.type || 'text') + '"' + (o.step ? ' step="' + o.step + '"' : '') + ' value="' + U.esc(def == null ? '' : def) + '" placeholder="' + U.esc(o.ph || '') + '" style="width:100%"><div class="s-red" id="ask-err" style="margin-top:6px;font-size:12px"></div>',
      [['Cancel', '', function () {}], [o.ok || 'Save', o.danger ? 'pri danger' : 'pri', function (close, w) {
        var v = w.querySelector('#ask-v').value.trim();
        if (!v || (o.must && v !== o.must)) { w.querySelector('#ask-err').textContent = o.must ? 'Type ' + o.must + ' to confirm.' : 'Enter a value.'; return false; }
        fn(v);
      }]], { width: o.width || 420 });
    var inp = w.querySelector('#ask-v'); inp.select();
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); w.querySelector('[data-mb="1"]').click(); } });
    return w;
  };
  // fields: [{k, label, type, options, req, span, ph, help}]
  U.fields = function (fields, v) {
    v = v || {};
    return '<div class="form" style="padding:0">' + fields.map(function (f) {
      var val = v[f.k] == null ? (f.def == null ? '' : f.def) : v[f.k], id = 'f_' + f.k, inp;
      if (f.type === 'select') inp = '<select id="' + id + '" data-k="' + f.k + '">' + (f.blank ? '<option value="">' + U.esc(f.blank) + '</option>' : '') + f.options.map(function (o) { o = [].concat(o); return '<option value="' + U.esc(o[0]) + '"' + (String(o[0]) === String(val) ? ' selected' : '') + '>' + U.esc(o[1] == null ? o[0] : o[1]) + '</option>'; }).join('') + '</select>';
      else if (f.type === 'textarea') inp = '<textarea id="' + id + '" data-k="' + f.k + '" rows="' + (f.rows || 4) + '" placeholder="' + U.esc(f.ph || '') + '">' + U.esc(val) + '</textarea>';
      else if (f.type === 'check') return '<div class="' + (f.span ? 's' + f.span : '') + '"><label class="chk"><input type="checkbox" id="' + id + '" data-k="' + f.k + '"' + (val ? ' checked' : '') + '> ' + U.esc(f.label) + '</label></div>';
      else inp = '<input id="' + id + '" data-k="' + f.k + '" type="' + (f.type || 'text') + '" value="' + U.esc(val) + '" placeholder="' + U.esc(f.ph || '') + '"' + (f.step ? ' step="' + f.step + '"' : '') + (f.ro ? ' readonly' : '') + (f.list ? ' list="' + f.list + '"' : '') + '>';
      return '<div class="' + (f.span ? 's' + f.span : '') + '"><label for="' + id + '">' + U.esc(f.label) + (f.req ? ' <span class="s-red">*</span>' : '') + '</label>' + inp + (f.help ? '<div class="mut" style="font-size:11.5px;margin-top:2px">' + U.esc(f.help) + '</div>' : '') + '</div>';
    }).join('') + '</div>';
  };
  U.values = function (root) { var o = {}; root.querySelectorAll('[data-k]').forEach(function (el) { o[el.dataset.k] = el.type === 'checkbox' ? el.checked : el.value.trim(); }); return o; };
  // form(title, fields, values, onSave(values) -> string error | void, {save, wide})
  U.form = function (title, fields, v, onSave, o) {
    o = o || {};
    return U.modal(title, (o.intro ? '<p class="t2" style="margin:0 0 10px">' + o.intro + '</p>' : '') + U.fields(fields, v) + '<div class="s-red" id="mdl-err" style="margin-top:8px"></div>', [['Cancel', '', function () {}], [o.save || 'Save', 'pri', function (close, w) {
      var vals = U.values(w), miss = fields.filter(function (f) { return f.req && !vals[f.k]; });
      if (miss.length) { w.querySelector('#mdl-err').textContent = 'Fill in: ' + miss.map(function (f) { return f.label; }).join(', ') + '.'; return false; }
      var err = onSave(vals); if (err) { w.querySelector('#mdl-err').textContent = err; return false; }
    }]], o);
  };
  // header search: patients by name or MRN, claims by number
  U.search = function () {
    var inp = document.getElementById('hd-q'), box = document.getElementById('hd-res'); if (!inp) return;
    var draw = function () {
      var q = inp.value.trim().toLowerCase(); if (!q) { box.style.display = 'none'; return; }
      var pts = D.S.patients.filter(function (p) { return (p.name + ' ' + p.mrn + ' ' + (p.dob || '')).toLowerCase().indexOf(q) >= 0; }).slice(0, 6);
      var cls = D.S.claims.filter(function (c) { return c.id.toLowerCase().indexOf(q) >= 0; }).slice(0, 4);
      box.innerHTML = (pts.map(function (p) { return '<a href="/v4/emr/chart?mrn=' + p.mrn + '">' + U.esc(p.name) + ' <span class="mut mono">' + p.mrn + '</span></a>'; }).join('') +
        cls.map(function (c) { return '<a href="/v4/revenue/claims?claim=' + c.id + '">' + c.id + ' <span class="mut">' + U.esc(D.ptName(c.mrn)) + '</span></a>'; }).join('')) || '<div class="mut" style="padding:6px 10px">No matches</div>';
      box.style.display = 'block';
    };
    inp.addEventListener('input', draw); inp.addEventListener('focus', draw);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { var a = box.querySelector('a'); if (a) location.href = a.href; } });
    document.addEventListener('click', function (e) { if (!e.target.closest('.hd-search')) box.style.display = 'none'; });
  };
  // toast(msg, kind?) kind: 'busy' | 'ok' | 'warn' | 'info' (guessed from the text when not given)
  var TOAST_ICON = {
    ok: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="7.5" fill="var(--grn)"/><path d="M4.6 8.3l2.2 2.2 4.6-4.8" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    warn: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 1.2l7.3 13.3H.7z" fill="var(--amb)"/><path d="M8 6v3.6" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/><circle cx="8" cy="11.9" r=".95" fill="#fff"/></svg>',
    info: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="7.5" fill="var(--blue)"/><circle cx="8" cy="4.9" r="1" fill="#fff"/><path d="M8 7.3v4.4" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>',
    busy: '<span class="toast-spin" aria-hidden="true"></span>' };
  U.toast = function (msg, kind) {
    msg = String(msg == null ? '' : msg);
    kind = kind || (/still|one moment|building|checking|working|loading|sending|starting one/i.test(msg) ? 'busy'
      : /^(signed|saved|approved|added|draft saved|settings saved|card saved|letter drafted|addendum signed|claim check done|re-checked|sample clinic (loaded|removed)|marked|you now|filled|changes discarded)/i.test(msg) ? 'ok'
      : /first|enter |can’t|can't|couldn|isn’t|denied|must|empty|nothing|no patients|choose|select|already|only the|finish|paste|get /i.test(msg) ? 'warn' : 'info');
    var box = document.querySelector('.toasts'); if (!box) { box = document.createElement('div'); box.className = 'toasts'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
    [].slice.call(box.children).forEach(function (o) { if (o.dataset.msg === msg) o.remove(); });   // the same message again replaces itself
    var d = document.createElement('div'); d.className = 'toast t-' + kind; d.dataset.msg = msg;
    var i = msg.indexOf(' ('), main = i > 0 && /\)\.?$/.test(msg) ? msg.slice(0, i) : msg, note = main === msg ? '' : msg.slice(i + 2).replace(/\)\.?$/, '');
    d.innerHTML = '<span class="toast-i">' + TOAST_ICON[kind] + '</span><div class="toast-m">' + U.esc(main) + (note ? '<small>' + U.esc(note) + '</small>' : '') + '</div><button type="button" class="toast-x" aria-label="Dismiss">' + U.I.x + '</button>';
    box.appendChild(d); while (box.children.length > 4) box.firstChild.remove();
    var gone = function () { d.classList.add('out'); setTimeout(function () { d.remove(); }, 180); };
    d.addEventListener('click', gone);
    setTimeout(gone, Math.min(7000, 2800 + msg.length * 35));
    return d;
  };
  // sizes the pane area to the window under whatever chrome the page has (header, toolbar, banner, strip)
  U.fit = function () {
    var tb = document.querySelector('.tb'); if (tb) document.documentElement.style.setProperty('--tb-top', Math.round(tb.getBoundingClientRect().top + window.scrollY + 4) + 'px');   // Althea sits in the toolbar
    var m = document.querySelector('.main'); if (!m) return;
    document.documentElement.style.setProperty('--chrome', Math.round(m.getBoundingClientRect().top + window.scrollY) + 'px');
  };
  U.on = function (root, sel, ev, fn) { root.addEventListener(ev, function (e) { var t = e.target.closest(sel); if (t && root.contains(t)) fn(t, e); }); };
  window.addEventListener('resize', function () { U.fit(); });
  window.U = U;
})();
