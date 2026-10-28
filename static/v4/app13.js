/* v4: Records Inbox (/v4/emr/inbox). Outside records (lab results, imaging reports, X-rays, referrals, records from another
   practice) arrive here from a secure upload link, the clinic's fax number, Open Dental or a records network, and staff
   file each one to a patient. Everything lives on the server, encrypted (records_intake.py); nothing is kept in the
   browser. Medical and dental clinics share this page. */
(function () {
  var E = U.esc;
  var json = function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || j.detail || 'Something went wrong.'); return j; }); };
  var get = function (u) { return fetch(u, { credentials: 'same-origin' }).then(json); };
  var post = function (u, b) { return fetch(u, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) }).then(json); };
  var when = function (iso) { if (!iso) return ''; var d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: '2-digit' }) + ' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); };
  var copy = function (t) { return navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject(); };

  V.pages.inbox = function () {
    var app = V.app, q = new URLSearchParams(location.search);
    var st = { status: 'new', docs: [], counts: {}, types: {}, sel: +q.get('doc') || 0, cur: null, busy: true, err: '', settings: null, pts: [], src: '' };
    var dental = !!(window.D && D.DENTAL);

    function load(keepSel) {
      st.busy = true;
      return Promise.all([get('/api/intake/inbox?status=' + st.status), st.settings ? Promise.resolve(st.settings) : get('/api/intake/settings')]).then(function (x) {
        st.docs = x[0].docs; st.counts = x[0].counts; st.types = x[0].types; st.settings = x[1]; st.busy = false; st.err = '';
        if (!keepSel || !st.docs.some(function (d) { return d.id === st.sel; })) st.sel = shown()[0] ? shown()[0].id : 0;
        draw(); return pick(st.sel);
      }, function (e) { st.busy = false; st.err = e.message; draw(); });
    }
    function shown() { return st.docs.filter(function (d) { return !st.src || d.source === st.src; }); }
    function pick(id) {
      st.sel = id; st.cur = null; draw();
      if (!id) return Promise.resolve();
      return get('/api/intake/docs/' + id).then(function (d) {
        if (st.sel !== id) return;
        if (st.blob) URL.revokeObjectURL(st.blob);                 // a blob URL, so big PDFs open in the browser's viewer
        var raw = atob(d.data.split(',')[1] || ''), arr = new Uint8Array(raw.length); for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        st.blob = d.blob = URL.createObjectURL(new Blob([arr], { type: d.mime }));
        st.cur = d; draw();
      }, function (e) { U.toast(e.message, 'warn'); });
    }

    function preview(d) {
      if (!d) return '<div class="blank">' + (st.sel ? 'Loading…' : (shown().length ? 'Select a record.' : '')) + '</div>';
      if (/^image\//.test(d.mime) && d.mime !== 'image/tiff') return '<div class="fax" style="text-align:center"><img src="' + d.data + '" alt="' + E(d.filename || 'Record') + '" style="max-width:100%"></div>';
      if (d.mime === 'application/pdf') return '<iframe title="Preview" src="' + d.blob + '" style="width:100%;height:100%;min-height:420px;border:0;background:#fff"></iframe>';
      return '<div class="fax"><p>' + E(d.filename) + '</p><p class="mut">TIFF files can’t be previewed in the browser. Use Download.</p></div>';
    }
    function filePanel(d) {
      if (!d) return '';
      var g = d.guess || {}, cands = (g.candidates || []);
      var opts = [];
      cands.forEach(function (c) { opts.push([c.mrn, '★ ' + c.name + (c.dob ? '  DOB ' + c.dob : '') + '  (' + c.mrn + ')']); });
      st.pts.forEach(function (p) { if (!cands.some(function (c) { return c.mrn === p.mrn; })) opts.push([p.mrn, p.name + (p.dob ? '  DOB ' + p.dob : '') + '  (' + p.mrn + ')']); });
      if (d.mrn && !opts.some(function (o) { return o[0] === d.mrn; })) opts.unshift([d.mrn, (d.patient || d.mrn) + '  (' + d.mrn + ')']);
      var read = g.text ? (g.patient_name || g.dob ? 'Read from the file: ' + [g.patient_name, g.dob && 'DOB ' + g.dob].filter(Boolean).join(', ') + '.' : 'The file has text, but no patient name or date of birth was found.')
        : (/^image\//.test(d.mime) ? 'An image: check whose it is before filing.' : 'A scanned file with no text layer: check whose it is before filing.');
      var match = cands.length ? (g.confidence === 'high' ? ' Likely ' + E(cands[0].name) + '.' : ' Possible match: ' + E(cands[0].name) + '.') : '';
      var fields = [{ k: 'mrn', label: 'Patient', type: 'select', options: opts, blank: 'Choose the patient', span: 4, def: d.mrn || (g.confidence === 'high' && cands[0] ? cands[0].mrn : '') },
        { k: 'doc_type', label: 'Kind of record', type: 'select', options: Object.keys(st.types).map(function (k) { return [k, st.types[k]]; }), span: 2, def: d.doc_type },
        { k: 'title', label: 'Title', span: 2, ph: 'e.g. A1c and lipid panel', def: d.title }];
      if (dental && /^image\/(png|jpeg|webp)$/.test(d.mime)) fields.push({ k: 'image_kind', label: 'X-ray type (for claims)', type: 'select', options: [['pa', 'Periapical'], ['bitewing', 'Bitewing'], ['fmx', 'Full mouth series'], ['pano', 'Panoramic'], ['photo', 'Photo']], span: 2 }, { k: 'teeth', label: 'Teeth', span: 2, ph: 'e.g. 30, 31' });
      fields.push({ k: 'note', label: 'Note', type: 'textarea', rows: 2, span: 4, def: d.note });
      var od = st.settings && st.settings.opendental && st.settings.opendental.connected && /^OD\d+$/.test(d.mrn || '');
      return '<div class="dp-sec" style="border-top:1px solid var(--b)"><div class="mut" style="font-size:12px;margin-bottom:8px">' + E(read) + match + '</div>' + U.fields(fields, {}) + '</div>' +
        '<div class="dp-f">' + (d.status === 'new' ? '<button class="btn" id="dismiss">Not for us</button><button class="btn pri" id="filedoc">File to patient</button>'
          : '<button class="btn" id="reopen">Back to inbox</button>' + (od && d.status === 'filed' ? '<button class="btn" id="topush">Copy to Open Dental</button>' : '') + '<button class="btn pri" id="filedoc">Save</button>') + '</div>';
    }

    function draw() {
      var rows = shown(), d = st.cur, c = st.counts, s = st.settings || {};
      var bySrc = {}; st.docs.forEach(function (x) { bySrc[x.source] = (bySrc[x.source] || 0) + 1; });
      var status = '<div class="mut" style="font-size:11.5px;padding:8px 10px;line-height:1.5">' +
        'Fax: ' + (s.fax_numbers && s.fax_numbers.length ? s.fax_numbers.map(function (n) { return '(' + n.slice(0, 3) + ') ' + n.slice(3, 6) + '-' + n.slice(6); }).join(', ') + (s.fax_ready ? '' : ' (waiting for the fax line)') : 'no number yet') + '<br>' +
        'Open Dental: ' + (s.opendental && s.opendental.connected ? (s.opendental.files_ready ? 'connected' : 'connected; file drop not set up') : 'not connected') + '<br>' +
        'Records network: ' + E(s.network ? s.network.label : '') + '</div>';
      app.innerHTML = U.header('inbox') +
        U.toolbar('Records Inbox', [['Send an upload link', 'clip', { pri: 1, id: 'mklink' }], ['Add files', 'upload', { id: 'addfiles' }], '|', ['Open Dental documents', 'download', { id: 'odpull' }], ['Search records network', 'search', { id: 'netq' }], '|', ['Upload links', 'mail', { id: 'links' }], ['Fax settings', 'fax', { id: 'faxset' }], ['Refresh', 'refresh', { id: 'reload' }]]) +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr) 440px">' +
          U.pane('Inbox', '', '', U.queue([{ items: [{ key: 'new', label: 'To file', n: c.new || 0, color: 'blue' }, { key: 'filed', label: 'Filed', n: c.filed || 0, color: 'grn' }, { key: 'dismissed', label: 'Not for us', n: c.dismissed || 0, color: 'gray' }] },
            { h: 'Came from', items: [{ key: 'src:', label: 'Everywhere', n: st.docs.length, color: null }].concat(['upload', 'fax', 'opendental', 'network', 'staff'].filter(function (k) { return bySrc[k]; }).map(function (k) { return { key: 'src:' + k, label: { upload: 'Upload links', fax: 'Fax', opendental: 'Open Dental', network: 'Records network', staff: 'Added by staff' }[k], n: bySrc[k], color: null, indent: 1 }; })) }],
            st.src ? 'src:' + st.src : st.status) + status) +
          '<section class="pane"><div class="ph">' + ({ new: 'To file', filed: 'Filed', dismissed: 'Not for us' }[st.status]) + ' <span class="sub">' + rows.length + '</span></div><div class="pbody">' +
            (st.err ? '<div class="banner-note s-red">' + E(st.err) + '</div>' : '') +
            U.grid(['Received', 'From', 'Kind', st.status === 'new' ? 'Likely patient' : 'Patient', 'Size'], rows.map(function (x) {
              var g = x.guess || {}, who = x.patient || (g.candidates && g.candidates[0] ? (g.confidence === 'high' ? '' : '? ') + g.candidates[0].name : (g.patient_name ? '? ' + g.patient_name : ''));
              return { sel: x.id === st.sel, attrs: ' data-doc="' + x.id + '"', cells: [when(x.received_at), '<div>' + E(x.source_label) + '</div><div class="mut" style="font-size:11.5px">' + E(x.sender || x.filename) + '</div>', E(x.doc_type_label) + (x.duplicate_of ? ' <span class="s-amb" title="Same file as another record">duplicate</span>' : ''), E(who || '—'), U.size(x.size)] };
            }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, st.busy ? 'Loading…' : st.status === 'new' ? 'Nothing waiting. Send an upload link or give out the clinic’s fax number.' : 'None.') + '</tbody>') +
          '</div></section>' +
          '<section class="pane"><div class="ph">' + (d ? E(d.title || d.doc_type_label || 'Record') : 'Preview') + (d ? ' <span class="sub" style="text-transform:none">' + E((d.filename ? d.filename + ' · ' : '') + d.source_label) + '</span><span class="end"><button class="mini" id="dl">Download</button></span>' : '') + '</div>' +
            '<div class="pbody" style="background:var(--bg)">' + preview(d) + '</div>' + filePanel(d) + '</section></div>';
      U.fit(); U.search();
    }
    V.redraw = draw;
    get('/api/intake/patients').then(function (p) { st.pts = p; if (st.cur) draw(); });
    load();

    U.on(app, '[data-q]', 'click', function (el) {
      var k = el.dataset.q;
      if (k.indexOf('src:') === 0) { st.src = k.slice(4); st.sel = shown()[0] ? shown()[0].id : 0; draw(); pick(st.sel); return; }
      st.status = k; st.src = ''; load();
    });
    U.on(app, 'tr[data-doc]', 'click', function (tr) { pick(+tr.dataset.doc); });
    U.on(app, '#reload', 'click', function () { st.settings = null; load(true); });
    U.on(app, '#dl', 'click', function () { var d = st.cur; if (!d) return; var a = document.createElement('a'); a.href = d.blob || d.data; a.download = d.filename || ('record-' + d.id); a.click(); });

    var vals = function () { return U.values(app.querySelector('.main > section:last-child .dp-sec')); };
    U.on(app, '#filedoc', 'click', function () {
      var v = vals(); if (!v.mrn) return U.toast('Choose the patient first.');
      v.teeth = String(v.teeth || '').split(/[\s,]+/).filter(Boolean);
      post('/api/intake/docs/' + st.sel + '/file', v).then(function (r) {
        U.toast('Filed to ' + (r.doc.patient || v.mrn) + (r.image_id ? '. Added to the patient’s X-rays, so it can go on a claim.' : '.'), 'ok'); load(true);
      }, function (e) { U.toast(e.message, 'warn'); });
    });
    U.on(app, '#dismiss', 'click', function () {
      U.ask('Not For This Clinic', 'Why? (optional)', '', function (why) { post('/api/intake/docs/' + st.sel + '/dismiss', { reason: why }).then(function () { U.toast('Moved to Not for us.'); load(); }, function (e) { U.toast(e.message, 'warn'); }); }, { ok: 'Move it', ph: 'Wrong practice, duplicate, junk fax…' });
    });
    U.on(app, '#reopen', 'click', function () { post('/api/intake/docs/' + st.sel + '/reopen').then(function () { U.toast('Back in To file.'); load(); }); });
    U.on(app, '#topush', 'click', function () {
      U.toast('Copying to Open Dental…', 'busy');
      post('/api/intake/docs/' + st.sel + '/opendental').then(function (r) { U.toast('Copied to the patient’s Imaging module in Open Dental' + (r.doc_num ? ' (document ' + r.doc_num + ')' : '') + '.', 'ok'); }, function (e) { U.toast(e.message, 'warn'); });
    });

    // upload links
    U.on(app, '#mklink', 'click', function () {
      U.form('Send An Upload Link', [{ k: 'mrn', label: 'For one patient (optional)', type: 'select', options: st.pts.map(function (p) { return [p.mrn, p.name + '  (' + p.mrn + ')']; }), blank: 'Any patient (staff file each one)', span: 4 },
        { k: 'label', label: 'Who it’s for (only your staff see this)', span: 4, ph: 'e.g. Northside Dental, X-rays for Rivera' }, { k: 'days', label: 'Works for', type: 'select', options: [['3', '3 days'], ['7', '7 days'], ['14', '14 days'], ['30', '30 days']], span: 2, def: '7' }], {},
        function (v) {
          post('/api/intake/links', v).then(function (r) {
            var url = r.url || (location.origin + r.path);
            U.modal('Upload Link Ready', '<p class="cf">Send this to the other office or the patient. Anyone with it can send files to your inbox until it expires; they can’t see anything. It isn’t shown again.</p>' +
              '<input id="lnk" readonly style="width:100%;font:12px var(--mono)" value="' + E(url) + '">', [['Close', '', function () {}], ['Copy link', 'pri', function () { copy(url).then(function () { U.toast('Link copied.', 'ok'); }, function () { app.ownerDocument.getElementById('lnk').select(); }); return false; }]], { width: 520 });
          }, function (e) { U.toast(e.message, 'warn'); });
        }, { save: 'Create link', intro: 'They upload PDFs or images from any browser, no account needed. Files arrive here encrypted, for your staff to file.' });
    });
    U.on(app, '#links', 'click', function () {
      get('/api/intake/links').then(function (L) {
        var w = U.modal('Upload Links', U.grid(['For', 'Patient', 'Made by', 'Expires', 'Files', ''], L.map(function (l) { return { cells: [E(l.label || '—'), E(l.patient || 'Any'), E(l.created_by), when(l.expires_at), l.files, l.active ? '<button class="mini" data-revoke="' + l.id + '">Turn off</button>' : '<span class="mut">off</span>'] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No links yet.') + '</tbody>'), [['Close', '', function () {}]], { wide: 1 });
        w.addEventListener('click', function (e) { var b = e.target.closest('[data-revoke]'); if (!b) return; post('/api/intake/links/' + b.dataset.revoke + '/revoke').then(function () { b.outerHTML = '<span class="mut">off</span>'; U.toast('Link turned off.'); }); });
      }, function (e) { U.toast(e.message, 'warn'); });
    });

    // staff adding files they got some other way
    U.on(app, '#addfiles', 'click', function () {
      U.pickFile('.pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp', true, function (files) {
        var fd = new FormData(); files.slice(0, 10).forEach(function (f) { fd.append('files', f); });
        U.toast('Adding ' + files.length + ' file' + (files.length === 1 ? '' : 's') + '…', 'busy');
        fetch('/api/intake/staff-upload', { method: 'POST', credentials: 'same-origin', body: fd }).then(json).then(function (r) { U.toast('Added ' + r.ids.length + ' to the inbox.', 'ok'); st.status = 'new'; st.src = ''; load(); }, function (e) { U.toast(e.message, 'warn'); });
      });
    });

    // fax numbers
    U.on(app, '#faxset', 'click', function () {
      var s = st.settings || {}, admin = (window.__ALTHAIS_USER__ || {}).role === 'admin';
      U.form('Fax Into Althais', [{ k: 'nums', label: 'The clinic’s fax number(s), comma separated', span: 4, ph: '(512) 555-0100', def: (s.fax_numbers || []).join(', '), ro: !admin }], {}, function (v) {
        if (!admin) return 'Only a clinic admin can change this.';
        post('/api/intake/settings', { fax_numbers: v.nums.split(/[,;]+/).map(function (x) { return x.trim(); }).filter(Boolean) }).then(function () { st.settings = null; U.toast('Fax numbers saved.', 'ok'); load(true); }, function (e) { U.toast(e.message, 'warn'); });
      }, { intro: (s.fax_ready ? 'Faxes sent to these numbers land in this inbox as PDFs.' : 'Althais’s fax line isn’t switched on yet. Once it is, faxes to these numbers land here as PDFs.') + ' Use a number from Althais’s fax provider (it signs a BAA), not a number on a desktop fax machine.' });
    });

    // Open Dental: the patient's documents
    U.on(app, '#odpull', 'click', function () {
      if (!(st.settings && st.settings.opendental && st.settings.opendental.connected)) return U.toast('Connect Open Dental first (Practice setup › Import data).', 'warn');
      var odp = st.pts.filter(function (p) { return /^OD\d+$/.test(p.mrn); });
      if (!odp.length) return U.toast('No patients from Open Dental yet. Pull them in from Practice setup first.', 'warn');
      U.form('Open Dental Documents', [{ k: 'mrn', label: 'Patient', type: 'select', options: odp.map(function (p) { return [p.mrn, p.name + '  (' + p.mrn + ')']; }), req: 1, span: 4 }], {}, function (v) {
        get('/api/intake/opendental/' + encodeURIComponent(v.mrn) + '/documents').then(function (r) {
          var w = U.modal('Open Dental Documents', (r.files_ready ? '' : '<div class="banner-note s-amb">Althais’s file drop for Open Dental isn’t set up yet, so files can be listed but not copied in.</div>') +
            U.grid(['Date', 'Description', 'Category', 'Teeth', ''], r.documents.map(function (x) { return { cells: [E(x.date), E(x.description || x.file), E(x.category), E(x.teeth), x.imported ? '<span class="s-grn">copied</span>' : '<button class="mini" data-od="' + x.doc_num + '"' + (r.files_ready ? '' : ' disabled') + '>Copy in</button>'] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'No documents in Open Dental for this patient.') + '</tbody>'), [['Close', '', function () { load(true); }]], { wide: 1 });
          w.addEventListener('click', function (e) {
            var b = e.target.closest('[data-od]'); if (!b) return; b.disabled = true; b.textContent = 'Copying…';
            post('/api/intake/opendental/' + encodeURIComponent(v.mrn) + '/documents/' + b.dataset.od).then(function () { b.outerHTML = '<span class="s-grn">copied</span>'; }, function (er) { b.disabled = false; b.textContent = 'Copy in'; U.toast(er.message, 'warn'); });
          });
        }, function (e) { U.toast(e.message, 'warn'); });
      }, { save: 'Show documents' });
    });

    // records network
    U.on(app, '#netq', 'click', function () {
      var n = st.settings && st.settings.network;
      if (!n || !n.connected) return U.confirm('Records Network Isn’t Connected', '<p class="cf">Lab results from Quest and Labcorp, and records from hospitals and other practices through the national networks, come in once Althais signs with a records network (and its BAA). Until then, use upload links and fax.</p>', 'OK', function () {}, { cancel: 'Close' });
      U.form('Search Records Network', [{ k: 'mrn', label: 'Patient', type: 'select', options: st.pts.map(function (p) { return [p.mrn, p.name + '  (' + p.mrn + ')']; }), req: 1, span: 4 }], {}, function (v) {
        U.toast('Searching ' + n.label + '…', 'busy');
        post('/api/intake/network/' + encodeURIComponent(v.mrn)).then(function (r) { U.toast(r.added ? 'Found ' + r.found + ', added ' + r.added + ' to To file.' : r.found ? 'Nothing new: already in the inbox.' : 'Nothing found for this patient.', r.added ? 'ok' : 'info'); st.status = 'new'; st.src = ''; load(); }, function (e) { U.toast(e.message, 'warn'); });
      }, { save: 'Search', intro: 'Asks ' + E(n.label) + ' for this patient’s records. Anything found lands in To file for you to check.' });
    });
  };
})();
