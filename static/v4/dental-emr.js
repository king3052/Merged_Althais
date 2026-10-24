/* Althais v4, the dental EMR, part 1: shared helpers (DA), the tooth chart, the perio chart, and the Schedule (operatory
   columns) and Patients pages. Everything here reads and writes the server (/api/dental/..., dental_api.py), not the
   browser's store; the header still comes from U.header. Pages: V.pages.dschedule, V.pages.dpatients.
   The Chart page is in dental-chart part of this file (V.pages.dchart). */
(function () {
  if (!window.V) return;
  var E = U.esc, M = U.money, M0 = U.money0;
  var DA = window.DA = {};

  // ---- server ----
  DA.api = function (method, path, body) {
    return fetch((path.indexOf('/api/') === 0 ? '' : '/api/dental') + path, { method: method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(typeof j.detail === 'string' ? j.detail : (j.detail && j.detail[0] && j.detail[0].msg) || j.error || ('Something went wrong (' + r.status + ')')); e.status = r.status; throw e; }
        return j; }); });
  };
  DA.get = function (p) { return DA.api('GET', p); };
  DA.post = function (p, b) { return DA.api('POST', p, b || {}); };
  DA.patch = function (p, b) { return DA.api('PATCH', p, b || {}); };
  DA.del = function (p) { return DA.api('DELETE', p); };
  DA.fail = function (e) { U.toast(e.message || String(e), 'warn'); };
  DA.setup = null;
  DA.loadSetup = function () { if (DA._setup) return DA._setup; DA._setup = DA.get('/setup').then(function (s) { DA.setup = s; DA.fees = {}; s.fees.forEach(function (f) { DA.fees[f.code] = f; }); DA.provs = {}; s.providers.forEach(function (p) { DA.provs[p.id] = p; }); return s; }); return DA._setup; };
  DA.desc = function (code) { var f = (DA.fees || {})[code]; if (f) return f.desc; var x = (window.DX && DX.FEES || []).filter(function (r) { return r[0] === code; })[0]; return x ? x[1] : ''; };
  DA.fee = function (code) { var f = (DA.fees || {})[code]; return f ? f.fee : 0; };
  DA.us = function (d) { if (!d) return ''; var p = String(d).slice(0, 10).split('-'); return p.length === 3 ? p[1] + '/' + p[2] + '/' + p[0] : d; };
  DA.short = function (d) { return DA.us(d).slice(0, 5); };
  DA.age = function (dob) { if (!dob) return ''; var b = new Date(dob + 'T12:00:00'), n = new Date(), a = n.getFullYear() - b.getFullYear(); if (n.getMonth() < b.getMonth() || (n.getMonth() === b.getMonth() && n.getDate() < b.getDate())) a--; return a; };
  DA.today = function () { var d = new Date(); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); };
  DA.addDays = function (iso, n) { var d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); };
  DA.time = function (iso) { var d = new Date(iso); var h = d.getHours(), m = d.getMinutes(); return (h % 12 || 12) + ':' + ('0' + m).slice(-2) + (h < 12 ? ' AM' : ' PM'); };
  DA.first = function (name) { var p = String(name || '').split(', '); return (p[1] || p[0] || '').split(' ')[0]; };
  DA.nice = function (name) { var p = String(name || '').split(', '); return p.length > 1 ? p[1] + ' ' + p[0] : name; };
  DA.line = function (l) { return E(l.code) + (l.tooth ? ' #' + E(l.tooth) : '') + (l.surface ? ' ' + E(l.surface) : '') + (l.quad ? ' ' + E(l.quad) : ''); };
  DA.statusChip = function (s) {
    var map = { draft: ['Draft', ''], ready: ['Ready', 'blue'], submitted: ['Sent', ''], accepted: ['Accepted', ''], paid: ['Paid', 'grn'], partial: ['Partly paid', 'amb'], denied: ['Denied', 'red'], rejected: ['Rejected', 'red'], void: ['Void', ''], appealed: ['Appealed', 'amb'],
      approved: ['Approved', 'grn'], pending: ['Pending', 'amb'], expired: ['Expired', ''], presented: ['Presented', 'blue'], declined: ['Declined', ''], completed: ['Completed', 'grn'], proposed: ['Proposed', ''], scheduled: ['Scheduled', 'blue'] };
    var m = map[s] || [s, '']; return '<span class="chip ' + m[1] + '">' + E(m[0]) + '</span>';
  };
  DA.mock = function () { return (DA.setup && DA.setup.vendors || []).some(function (v) { return v.mock; }) ? ' <span class="mockflag" title="Third-party services are mocks until vendor contracts are signed. Nothing is transmitted.">MOCK VENDORS</span>' : ''; };
  DA.frame = function (page, title, actions, end) { return U.header(page) + U.toolbar(title, actions || [], end || ''); };
  DA.blank = function (msg) { return '<div class="emptyst">' + msg + '</div>'; };
  DA.needSample = function (page) {
    return '<div class="emptyst"><b>No practice set up yet</b>Add your offices, chairs and providers in Practice setup, or load the synthetic practice to try every screen.<div style="margin-top:12px;display:flex;gap:8px;justify-content:center"><a class="btn" href="/v4/dental/setup">Practice setup</a><button class="btn pri" id="ld-sample">Load synthetic practice</button></div></div>';
  };
  // the synthetic practice is built on the server in the background: start it, then wait until it's there
  DA.waitForSample = function (then) {
    var t = U.toast('Building the synthetic practice. This can take a minute or two…', 'busy');
    var poll = function () { DA.get('/sample/status').then(function (s) {
      if (s.status === 'building') return setTimeout(poll, 3000);
      if (t) t.remove();
      if (s.status === 'done') { U.toast('Sample practice ready: ' + s.patients + ' patients.', 'ok'); setTimeout(then || function () { location.reload(); }, 600); }
      else U.toast('The sample practice couldn\u2019t be built (' + s.status + '). Try again from Practice setup.', 'warn');
    }, function () { setTimeout(poll, 5000); }); };
    poll();
  };
  U.on(document.body, '#ld-sample', 'click', function (b) { b.disabled = true; DA.post('/sample').then(function () { DA.waitForSample(); }, function (e) { b.disabled = false; DA.fail(e); }); });
  // a test clinic's sample practice starts building on its first visit: tell the person and refresh when it's ready
  setTimeout(function () { if (window.D && D.DENTAL) DA.get('/sample/status').then(function (s) { if (s.status === 'building') DA.waitForSample(); }, function () {}); }, 400);

  // patient picker against the server's patient list
  DA.pickPatient = function (title, cb) {
    DA.get('/patients').then(function (list) {
      var w = U.modal(title, '<div class="pick" style="padding:0"><input id="pk" placeholder="Search name, MRN or date of birth"><div id="pkl" style="max-height:340px;overflow:auto;margin-top:6px"></div></div>', [['New patient', '', function () { DA.patientForm(function (mrn) { cb(mrn); }); }], ['Cancel', '', function () {}]], { width: 560 });
      var draw = function () {
        var s = w.querySelector('#pk').value.toLowerCase(), l = list.filter(function (p) { return (p.name + ' ' + p.mrn + ' ' + p.dob).toLowerCase().indexOf(s) >= 0; }).slice(0, 60);
        w.querySelector('#pkl').innerHTML = '<table class="g tight"><tbody>' + (l.map(function (p) { return '<tr class="click" data-pk="' + E(p.mrn) + '"><td>' + E(p.name) + '</td><td class="mono">' + E(p.mrn) + '</td><td>' + DA.us(p.dob) + '</td><td>' + E(p.payer || 'Self-pay') + '</td></tr>'; }).join('') || U.empty(4, 'No match')) + '</tbody></table>';
      };
      draw(); w.querySelector('#pk').oninput = draw;
      w.addEventListener('click', function (e) { var r = e.target.closest('[data-pk]'); if (r) { w.remove(); cb(r.dataset.pk); } });
    }, DA.fail);
  };
  DA.patientForm = function (after) {
    U.form('New patient', [
      { k: 'name', label: 'Name (Last, First)', req: 1, span: 2, ph: 'Garcia, Daniel' }, { k: 'dob', label: 'Date of birth', type: 'date', req: 1 }, { k: 'sex', label: 'Sex', type: 'select', options: ['F', 'M', 'X'] },
      { k: 'phone', label: 'Mobile phone', ph: '(512) 555-0100' }, { k: 'email', label: 'Email', type: 'email' }, { k: 'addr', label: 'Address', span: 2, ph: 'Street, City, ST ZIP' },
      { k: 'payer', label: 'Dental plan', type: 'select', options: (window.DX ? DX.PAYERS : []).concat(['Self-pay']) }, { k: 'member', label: 'Subscriber ID' },
      { k: 'allergies', label: 'Allergies', span: 2, ph: 'Penicillin (hives); NKDA' }, { k: 'problems', label: 'Medical history', span: 2, ph: 'Diabetes; on blood thinners; joint replacement' },
    ], {}, function (v) {
      v.name = V.splitName(v.name);
      DA.post('/patients', v).then(function (r) { U.toast(v.name + ' added, MRN ' + r.mrn + '.', 'ok'); if (after) after(r.mrn); }, DA.fail);
    }, { wide: 1, save: 'Add patient' });
  };

  /* =============================== tooth chart =============================== */
  var TC = DA.teeth = {};
  var UP = [], LO = [];
  for (var i = 1; i <= 16; i++) UP.push(i);
  for (var j = 32; j >= 17; j--) LO.push(j);
  TC.kind = function (t) { var k = window.DX ? DX.toothKind(String(t)) : null; return k ? k[1] || 'molar' : 'molar'; };
  var COLOR = { existing: 'var(--tx-exist)', planned: 'var(--tx-plan)', completed: 'var(--tx-done)', watch: 'var(--tx-watch)' };
  TC.color = function (c) { return c.condition === 'caries' && c.status === 'existing' ? 'var(--tx-cond)' : c.condition === 'watch' ? 'var(--tx-watch)' : COLOR[c.status] || COLOR.existing; };
  // which side of the occlusal box is mesial: toward the midline
  var mesialRight = function (t) { t = +t; return (t >= 1 && t <= 8) || (t >= 25 && t <= 32); };
  var W = 58, X0 = 30;
  function crownPath(xc, y, w, h, kind) {
    var cw = w * (kind === 'anterior' ? .72 : .8), ow = kind === 'anterior' ? w * .82 : w, r = function (n) { return n.toFixed(1); };
    var p = 'M' + r(xc - cw / 2) + ',' + r(y) + ' C' + r(xc - w / 2 - 2) + ',' + r(y + h * .3) + ' ' + r(xc - ow / 2 - 1) + ',' + r(y + h * .78) + ' ' + r(xc - ow / 2 + 3) + ',' + r(y + h);
    if (kind === 'anterior') p += ' Q' + r(xc) + ',' + r(y + h + 3) + ' ' + r(xc + ow / 2 - 3) + ',' + r(y + h);
    else { var n = kind === 'molar' ? 3 : 2, step = (ow - 6) / n; for (var k = 0; k < n; k++) { var a = xc - ow / 2 + 3 + step * k; p += ' Q' + r(a + step / 2) + ',' + r(y + h + 5) + ' ' + r(a + step) + ',' + r(y + h); } }
    return p + ' C' + r(xc + ow / 2 + 1) + ',' + r(y + h * .78) + ' ' + r(xc + w / 2 + 2) + ',' + r(y + h * .3) + ' ' + r(xc + cw / 2) + ',' + r(y) + ' Z';
  }
  function rootPaths(xc, y, w, len, kind, upper) {
    var n = kind === 'molar' ? (upper ? 3 : 2) : kind === 'premolar' && upper ? 2 : 1, cw = w * (kind === 'anterior' ? .62 : .78), rw = cw / n, out = [];
    for (var k = 0; k < n; k++) {
      var x = xc - cw / 2 + rw * k, L = len * (n === 3 && k === 1 ? .82 : 1);
      out.push({ d: 'M' + x.toFixed(1) + ',' + y + ' Q' + (x + rw * .08).toFixed(1) + ',' + (y - L * .75).toFixed(1) + ' ' + (x + rw / 2).toFixed(1) + ',' + (y - L).toFixed(1) + ' Q' + (x + rw * .92).toFixed(1) + ',' + (y - L * .75).toFixed(1) + ' ' + (x + rw).toFixed(1) + ',' + y + ' Z', apex: [x + rw / 2, y - L] });
    }
    return out;
  }
  // geometry: upper teeth drawn with roots up; lower teeth are the same drawing flipped around their row's middle
  var ROW = { up: { root: 72, crown: 72, num: 14, box: 124 }, lo: { box: 196, crownY: 248, num: 352 } };
  function toothSvg(t, conds, sel, picks, upper) {
    var col = upper ? UP.indexOf(t) : LO.indexOf(t), xc = X0 + col * W + W / 2, kind = TC.kind(t);
    var w = kind === 'molar' ? 44 : kind === 'premolar' ? 34 : 30, rootLen = kind === 'anterior' ? 48 : kind === 'premolar' ? 44 : 38, ch = kind === 'anterior' ? 40 : 36;
    var mine = conds.filter(function (c) { return String(c.tooth) === String(t); });
    var RANK = { planned: 0, completed: 1, watch: 2, existing: 3 };
    mine.sort(function (a, b) { return (RANK[a.status] || 9) - (RANK[b.status] || 9); });   // planned work draws over what's there
    var has = function (k, st) { return mine.filter(function (c) { return c.condition === k && (!st || c.status === st); })[0]; };
    var missing = has('missing') && !has('implant'), ext = has('extracted');
    var g = '';
    // the drawing in "upper" coordinates: cervical line at y=72, roots above it, crown below to 72+ch
    var cy = 72, body = '';
    var roots = rootPaths(xc, cy, w, rootLen, kind, upper);
    var imp = has('implant');
    if (imp) body += '<g style="color:' + TC.color(imp) + '"><rect x="' + (xc - 7) + '" y="' + (cy - rootLen) + '" width="14" height="' + rootLen + '" rx="3" fill="currentColor" opacity=".85"/>' + [0, 1, 2, 3, 4].map(function (k) { return '<line x1="' + (xc - 8) + '" x2="' + (xc + 8) + '" y1="' + (cy - rootLen + 6 + k * 8) + '" y2="' + (cy - rootLen + 9 + k * 8) + '" stroke="var(--surface)" stroke-width="1.6"/>'; }).join('') + '</g>';
    else roots.forEach(function (r) { body += '<path class="root" d="' + r.d + '"/>'; });
    var rct = has('rct'); if (rct && !imp) roots.forEach(function (r) { body += '<path d="M' + r.apex[0].toFixed(1) + ',' + (r.apex[1] + 3).toFixed(1) + ' L' + xc + ',' + (cy - 2) + '" stroke="' + TC.color(rct) + '" stroke-width="3" stroke-linecap="round"/>'; });
    var post = has('post_core'); if (post) body += '<path d="M' + xc + ',' + (cy - rootLen * .55) + ' L' + xc + ',' + (cy + 8) + '" stroke="' + TC.color(post) + '" stroke-width="4" stroke-linecap="round"/>';
    var pa = has('periapical'); if (pa) roots.slice(0, 1).forEach(function (r) { body += '<circle cx="' + r.apex[0].toFixed(1) + '" cy="' + (r.apex[1] - 2).toFixed(1) + '" r="7" fill="none" stroke="' + TC.color(pa) + '" stroke-width="2"/>'; });
    var crown = has('crown') || has('implant_crown') || has('bridge') || has('onlay');
    body += '<path class="silo" d="' + crownPath(xc, cy, w, ch, kind) + '"' + (crown ? ' style="fill:' + TC.color(crown) + ';fill-opacity:.82"' : '') + '/>';
    var ven = has('veneer'); if (ven) body += '<path d="' + crownPath(xc, cy + 4, w - 10, ch - 8, kind) + '" style="fill:' + TC.color(ven) + ';fill-opacity:.6"/>';
    var fr = has('fracture'); if (fr) body += '<path d="M' + (xc - w / 2 + 4) + ',' + (cy + ch * .35) + ' l6,6 l5,-7 l6,8 l5,-6 l6,7" fill="none" stroke="' + TC.color(fr) + '" stroke-width="2"/>';
    var den = has('denture'); if (den) body += '<path d="' + crownPath(xc, cy, w, ch, kind) + '" fill="url(#hatch-done)" opacity=".7"/>';
    // lower teeth: the same drawing mirrored, so the biting edge faces the middle of the chart and roots point down
    var tf = upper ? '' : ' transform="translate(0,' + (ROW.lo.crownY + 72 + ch) + ') scale(1,-1)"';
    g += '<g class="tooth' + (sel ? ' sel' : '') + (missing ? ' missing' : '') + '" data-tooth="' + t + '">';
    g += '<rect x="' + (xc - W / 2 + 1) + '" y="' + (upper ? 2 : ROW.lo.box - 4) + '" width="' + (W - 2) + '" height="' + (upper ? 168 : 168) + '" fill="transparent"/>';
    g += '<g' + tf + '>' + body + '</g>';
    if (missing || ext) {
      var xcol = ext ? TC.color(ext) : 'var(--tx-exist)', ytop = upper ? 26 : ROW.lo.crownY + 2, ybot = upper ? 106 : ROW.lo.crownY + 84;
      g += '<path d="M' + (xc - 18) + ',' + ytop + ' L' + (xc + 18) + ',' + ybot + ' M' + (xc + 18) + ',' + ytop + ' L' + (xc - 18) + ',' + ybot + '" stroke="' + xcol + '" stroke-width="' + (ext ? 3 : 2) + '" stroke-linecap="round"/>';
    }
    // occlusal / incisal box with five surfaces
    var bs = 30, bx = xc - bs / 2, by = upper ? ROW.up.box : ROW.lo.box, ins = bs * .3;
    var x0 = bx, x1 = bx + bs, y0 = by, y1 = by + bs, ix0 = bx + ins, ix1 = x1 - ins, iy0 = by + ins, iy1 = y1 - ins;
    var top = upper ? 'B' : 'L', bot = upper ? 'L' : 'B', left = mesialRight(t) ? 'D' : 'M', right = mesialRight(t) ? 'M' : 'D', mid = kind === 'anterior' ? 'I' : 'O';
    var surfs = [[top, x0 + ',' + y0 + ' ' + x1 + ',' + y0 + ' ' + ix1 + ',' + iy0 + ' ' + ix0 + ',' + iy0], [bot, x0 + ',' + y1 + ' ' + x1 + ',' + y1 + ' ' + ix1 + ',' + iy1 + ' ' + ix0 + ',' + iy1],
      [left, x0 + ',' + y0 + ' ' + ix0 + ',' + iy0 + ' ' + ix0 + ',' + iy1 + ' ' + x0 + ',' + y1], [right, x1 + ',' + y0 + ' ' + ix1 + ',' + iy0 + ' ' + ix1 + ',' + iy1 + ' ' + x1 + ',' + y1],
      [mid, ix0 + ',' + iy0 + ' ' + ix1 + ',' + iy0 + ' ' + ix1 + ',' + iy1 + ' ' + ix0 + ',' + iy1]];
    surfs.forEach(function (s) {
      var letter = s[0], fill = '', alt = letter === 'B' ? 'F' : '';
      var on = mine.filter(function (c) { return /^(composite|amalgam|caries|inlay|onlay|sealant|watch)$/.test(c.condition) && (String(c.surface || '').indexOf(letter) >= 0 || (alt && String(c.surface || '').indexOf(alt) >= 0) || (c.condition === 'sealant' && letter === mid)); });
      var top1 = on.filter(function (c) { return c.status === 'planned'; })[0] || on.filter(function (c) { return c.condition === 'caries'; })[0] || on[0];
      if (top1) fill = top1.condition === 'caries' ? 'url(#hatch-caries)' : top1.condition === 'amalgam' && top1.status === 'existing' ? 'var(--tx-exist)' : TC.color(top1);
      var picked = picks && sel && picks.indexOf(letter) >= 0;
      g += '<polygon class="surf' + (picked ? ' pick' : '') + '" data-surf="' + letter + '" data-tooth="' + t + '" points="' + s[1] + '"' + (fill && !picked ? ' style="fill:' + fill + (top1.condition === 'composite' && top1.status === 'existing' ? ';fill-opacity:.55' : '') + '"' : '') + '><title>#' + t + ' ' + letter + '</title></polygon>';
    });
    if (has('sealant')) g += '<text x="' + xc + '" y="' + (by + bs / 2 + 4) + '" text-anchor="middle" font-size="10" font-weight="700" fill="var(--surface)">S</text>';
    var wt = has('watch'); if (wt && !wt.surface) g += '<circle cx="' + xc + '" cy="' + (by + bs / 2) + '" r="' + (bs / 2 + 3) + '" fill="none" stroke="var(--tx-watch)" stroke-width="1.6" stroke-dasharray="3 2"/>';
    g += '<text class="num" x="' + xc + '" y="' + (upper ? ROW.up.num : ROW.lo.num) + '">' + t + '</text>';
    if (sel) g += '<rect class="selbox" x="' + (xc - W / 2 + 2) + '" y="' + (upper ? 1 : ROW.lo.box - 6) + '" width="' + (W - 4) + '" height="' + 170 + '"/>';
    return g + '</g>';
  }
  TC.svg = function (conds, selected, picks) {
    var width = X0 * 2 + W * 16, height = 362, sel = selected || [];
    var s = '<svg class="odo" viewBox="0 0 ' + width + ' ' + height + '" width="' + width + '" height="' + height + '" role="img" aria-label="Tooth chart">' +
      '<defs><pattern id="hatch-caries" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="var(--red-bg)"/><line x1="0" y1="0" x2="0" y2="5" stroke="var(--tx-cond)" stroke-width="2.4"/></pattern>' +
      '<pattern id="hatch-done" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="4" stroke="var(--tx-done)" stroke-width="1.5"/></pattern></defs>' +
      '<text class="arch-l" x="6" y="64" transform="rotate(-90 10 64)">UPPER</text><text class="arch-l" x="6" y="300" transform="rotate(-90 10 300)">LOWER</text>' +
      '<line class="midline" x1="' + (X0 + W * 8) + '" x2="' + (X0 + W * 8) + '" y1="4" y2="' + (height - 4) + '"/>' +
      '<line class="midline" x1="' + X0 + '" x2="' + (X0 + W * 16) + '" y1="' + 175 + '" y2="' + 175 + '"/>' +
      '<text class="arch-l" x="' + (X0 + 4) + '" y="171">PATIENT RIGHT</text><text class="arch-l" x="' + (X0 + W * 16 - 4) + '" y="171" text-anchor="end">PATIENT LEFT</text>';
    UP.forEach(function (t) { s += toothSvg(t, conds, sel.indexOf(t) >= 0, sel.length === 1 ? picks : null, true); });
    LO.forEach(function (t) { s += toothSvg(t, conds, sel.indexOf(t) >= 0, sel.length === 1 ? picks : null, false); });
    return s + '</svg>';
  };
  TC.legend = '<div class="odo-legend"><span><i style="background:var(--tx-exist)"></i>Existing</span><span><i style="background:var(--tx-plan)"></i>Treatment planned</span><span><i style="background:var(--tx-done)"></i>Completed here</span><span><i style="background:repeating-linear-gradient(45deg,var(--tx-cond) 0 2px,var(--red-bg) 2px 4px)"></i>Caries</span><span><i style="border:1.5px dashed var(--tx-watch)"></i>Watch</span></div>';
  // the CDT code for charting a condition as treatment (planned or completed)
  TC.code = function (cond, tooth, surface) {
    var k = TC.kind(tooth), n = Math.max(1, Math.min(4, String(surface || '').replace(/[^MODBLIF]/g, '').length || 1));
    return ({ composite: (k === 'anterior' ? ['D2330', 'D2331', 'D2332', 'D2335'] : ['D2391', 'D2392', 'D2393', 'D2394'])[n - 1], amalgam: ['D2140', 'D2150', 'D2160', 'D2161'][n - 1], crown: 'D2740',
      rct: { anterior: 'D3310', premolar: 'D3320', molar: 'D3330' }[k], implant: 'D6010', implant_crown: 'D6065', extracted: 'D7140', sealant: 'D1351', post_core: 'D2950', veneer: 'D2962', bridge: 'D6240', onlay: 'D2644' })[cond] || '';
  };
  TC.TOOLS = [['caries', 'Caries'], ['composite', 'Composite'], ['amalgam', 'Amalgam'], ['crown', 'Crown'], ['post_core', 'Buildup'], ['rct', 'Root canal'], ['extracted', 'Extract'], ['missing', 'Missing'],
    ['implant', 'Implant'], ['implant_crown', 'Implant crown'], ['bridge', 'Bridge'], ['veneer', 'Veneer'], ['sealant', 'Sealant'], ['fracture', 'Fracture'], ['periapical', 'PA lesion'], ['watch', 'Watch']];
  TC.FINDINGS = { caries: 1, missing: 1, fracture: 1, periapical: 1, watch: 1 };

  /* =============================== perio chart =============================== */
  var PC = DA.perio = {};
  PC.PATH = [];
  for (var a = 1; a <= 16; a++) PC.PATH.push([a, 'F']);
  for (var b2 = 16; b2 >= 1; b2--) PC.PATH.push([b2, 'L']);
  for (var c2 = 17; c2 <= 32; c2++) PC.PATH.push([c2, 'L']);
  for (var d2 = 32; d2 >= 17; d2--) PC.PATH.push([d2, 'F']);
  PC.empty = function () { var r = {}; for (var t = 1; t <= 32; t++) r[t] = { pd: [null, null, null, null, null, null], rec: [null, null, null, null, null, null], bop: [0, 0, 0, 0, 0, 0], sup: [0, 0, 0, 0, 0, 0], mob: null, furc: null, missing: false }; return r; };
  PC.idx = function (side) { return side === 'F' ? [0, 1, 2] : [5, 4, 3]; };
  PC.summary = function (r) {
    var sites = 0, bop = 0, o4 = 0, o5 = 0, o6 = 0;
    Object.keys(r || {}).forEach(function (t) { var v = r[t]; if (v.missing) return; (v.pd || []).forEach(function (d, k) { if (d == null) return; sites++; if ((v.bop || [])[k]) bop++; if (d >= 4) o4++; if (d >= 5) o5++; if (d >= 6) o6++; }); });
    return { sites: sites, bop: sites ? Math.round(100 * bop / sites) : 0, o4: o4, o5: o5, o6: o6 };
  };
  function cell(v, side, prev, cur, key) {
    var ids = side === 'F' ? [0, 1, 2] : [3, 4, 5];
    if (key === 'pd') return '<div class="sites pd">' + ids.map(function (k) { var d = v.pd[k], p = prev && prev.pd ? prev.pd[k] : null; return '<span class="' + (d >= 5 ? 'v5' : d === 4 ? 'v4' : '') + (cur && cur.indexOf(k) >= 0 ? ' cur' : '') + '" title="' + (p != null ? 'Last exam: ' + p + ' mm' : '') + '">' + (d == null ? '' : d) + '</span>'; }).join('') + '</div>';
    if (key === 'rec') return '<div class="sites">' + ids.map(function (k) { var d = v.rec[k]; return '<span>' + (d ? d : '') + '</span>'; }).join('') + '</div>';
    if (key === 'bop') return '<div class="sites bop">' + ids.map(function (k) { return '<span data-bop="' + k + '">' + (v.sup[k] ? '<i class="s"></i>' : v.bop[k] ? '<i></i>' : '') + '</span>'; }).join('') + '</div>';
    return '';
  }
  function graph(v, side, up) {
    var ids = side === 'F' ? [0, 1, 2] : [3, 4, 5], w = 51, h = 52, base = up ? h - 10 : 10, out = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">';
    out += '<line x1="0" x2="' + w + '" y1="' + base + '" y2="' + base + '" stroke="var(--gum)" stroke-width="3"/>';
    ids.forEach(function (k, i) {
      var d = v.pd[k], rec = v.rec[k] || 0; if (d == null) return;
      var x = 6 + i * 15, gm = up ? base - rec * 3.4 : base + rec * 3.4, len = d * 3.4, y = up ? gm - len : gm;
      out += '<rect x="' + x + '" y="' + y.toFixed(1) + '" width="9" height="' + len.toFixed(1) + '" fill="' + (d >= 5 ? 'var(--pd-5)' : d === 4 ? 'var(--pd-4)' : 'var(--pd-ok)') + '" opacity=".85"/>';
      if (rec) out += '<line x1="' + x + '" x2="' + (x + 9) + '" y1="' + gm.toFixed(1) + '" y2="' + gm.toFixed(1) + '" stroke="var(--t)" stroke-width="1.5"/>';
    });
    return out + '</svg>';
  }
  PC.table = function (r, prev, pos) {
    var cur = PC.PATH[pos] || [];
    var curIdx = function (t, side) { return cur[0] === t && cur[1] === side ? PC.idx(side) : null; };
    var row = function (label, teeth, fn, cls) { return '<tr class="' + (cls || '') + '"><th class="rl">' + label + '</th>' + teeth.map(function (t) { var v = r[t]; return v.missing ? '<td class="t miss"></td>' : '<td class="t" data-t="' + t + '">' + fn(v, t) + '</td>'; }).join('') + '</tr>'; };
    var arch = function (teeth, up) {
      var F = function (key) { return function (v, t) { return cell(v, 'F', prev && prev[t], curIdx(t, 'F'), key); }; }, L = function (key) { return function (v, t) { return cell(v, 'L', prev && prev[t], curIdx(t, 'L'), key); }; };
      var mob = row('Mobility', teeth, function (v) { return v.mob ? '<b>' + v.mob + '</b>' : ''; }), furc = row('Furcation', teeth, function (v, t) { return TC.kind(t) === 'molar' ? (v.furc ? '<b style="color:var(--pd-5)">' + ['', 'I', 'II', 'III'][v.furc] + '</b>' : '<span class="mut">·</span>') : ''; });
      var nums = '<tr class="numrow"><th class="rl"></th>' + teeth.map(function (t) { return '<td class="' + (cur[0] === t ? 'cur' : '') + (r[t].missing ? ' miss' : '') + '" data-num="' + t + '">' + t + '</td>'; }).join('') + '</tr>';
      var gF = '<tr class="graph"><th class="rl"></th>' + teeth.map(function (t) { return r[t].missing ? '<td class="miss"></td>' : '<td>' + graph(r[t], 'F', up) + '</td>'; }).join('') + '</tr>';
      var gL = '<tr class="graph"><th class="rl"></th>' + teeth.map(function (t) { return r[t].missing ? '<td class="miss"></td>' : '<td>' + graph(r[t], 'L', !up) + '</td>'; }).join('') + '</tr>';
      var fac = [row('Bleeding', teeth, F('bop')), row('Gingival margin', teeth, F('rec')), row('Probing depth', teeth, F('pd'), 'pdrow F')];
      var lin = [row('Probing depth', teeth, L('pd'), 'pdrow L'), row('Gingival margin', teeth, L('rec')), row('Bleeding', teeth, L('bop'))];
      if (up) return mob + furc + '<tr class="side-h"><th></th><th colspan="16">Facial</th></tr>' + fac.join('') + gF + nums + gL + lin.join('') + '<tr class="side-h"><th></th><th colspan="16">Lingual / palatal</th></tr>';
      return '<tr class="side-h"><th></th><th colspan="16">Lingual</th></tr>' + lin.reverse().join('') + gL + nums + gF + fac.reverse().join('') + '<tr class="side-h"><th></th><th colspan="16">Facial</th></tr>' + mob + furc;
    };
    // the bar graphs grow away from the tooth numbers, toward their readings
    return '<table data-arch="up">' + arch(UP, true) + '</table><div style="height:14px"></div><table data-arch="lo">' + arch(LO, false) + '</table>';
  };

  /* =============================== Schedule =============================== */
  V.pages.dschedule = function () {
    var app = V.app, q = V.q, day = q.get('date') || DA.today(), loc = +q.get('loc') || 0, sel = +q.get('appt') || 0, data = null, PPM = 1.5, provFilter = 0;
    var mins = function (iso) { var d = new Date(iso); return d.getHours() * 60 + d.getMinutes(); };
    var hm = function (m) { var h = Math.floor(m / 60), mm = m % 60; return (h % 12 || 12) + (mm ? ':' + ('0' + mm).slice(-2) : '') + (h < 12 ? ' am' : ' pm'); };
    var hex = function (c, a) { return /^#[0-9a-f]{6}$/i.test(c || '') ? c + a : c; };
    function load() {
      return Promise.all([DA.loadSetup(), DA.get('/schedule?date=' + day + (loc ? '&location_id=' + loc : ''))]).then(function (r) { data = r[1]; loc = data.location ? data.location.id : 0; draw(); }, function (e) { app.innerHTML = DA.frame('dschedule', 'Schedule') + DA.blank(E(e.message)); });
    }
    function apptHtml(a, dayStart) {
      var top = (mins(a.start) - dayStart) * PPM, h = Math.max(18, a.minutes * PPM - 2), f = a.flags || {};
      var ic = '';
      if (a.mrn) {
        ic += f.payer ? (f.elig_active ? '<b class="ok" title="Insurance verified ' + E(f.elig) + '">INS</b>' : '<b class="no" title="' + (f.elig ? 'Coverage inactive' : 'Benefits not verified') + '">INS?</b>') : '<b class="no" title="No insurance on file">SELF</b>';
        if (f.balance > 0) ic += '<b class="bal" title="Balance ' + M(f.balance) + '">$</b>';
        if (f.medical && /thinner|joint|valve|bisphos|alendronate|warfarin|apixaban|diabet/i.test(f.medical)) ic += '<b class="med" title="' + E(f.medical) + '">⚕</b>';
        if (f.allergies && !/^nkda$/i.test(f.allergies)) ic += '<b class="med" title="Allergy: ' + E(f.allergies) + '">A</b>';
      }
      var st = { confirmed: '✓ Confirmed', arrived: 'Arrived', seated: 'In chair', complete: 'Done', no_show: 'No show', cancelled: 'Cancelled', scheduled: '', requested: 'Request' }[a.status] || '';
      var procs = (a.procedures || []).map(function (p) { return p.code + (p.tooth ? ' #' + p.tooth : ''); }).join(', ');
      return '<div class="ap st-' + a.status + (a.mrn ? '' : ' req') + (a.id === sel ? ' sel' : '') + '" draggable="true" data-ap="' + a.id + '" style="top:' + top + 'px;height:' + h + 'px;border-left-color:' + a.color + ';background:' + hex(a.color, '14') + '">' +
        '<div class="who">' + E(a.patient || 'Online request') + '<span class="ic">' + ic + '</span></div>' +
        (h > 30 ? '<div class="what">' + E(a.reason || '') + (procs ? ' · <span class="mono" style="font-size:10.5px">' + E(procs) + '</span>' : '') + '</div>' : '') +
        (h > 46 ? '<div class="tm">' + DA.time(a.start) + ' · ' + a.minutes + ' min · ' + E(a.provider.replace(/,? ?RDH$/, '')) + (st ? ' · ' + st : '') + (a.source !== 'staff' ? ' · ' + (a.source === 'online' ? 'Booked online' : 'Recall') : '') + '</div>' : '') + '</div>';
    }
    function right() {
      var a = (data.appointments || []).filter(function (x) { return x.id === sel; })[0];
      if (!a) {
        var appts = data.appointments.filter(function (x) { return x.status !== 'cancelled'; }), sched = 0;
        appts.forEach(function (x) { (x.procedures || []).forEach(function (p) { sched += DA.fee(p.code); }); });
        var unv = appts.filter(function (x) { return x.mrn && x.flags && x.flags.payer && !x.flags.elig_active; }).length, conf = appts.filter(function (x) { return x.status === 'confirmed' || x.status === 'arrived' || x.status === 'seated' || x.status === 'complete'; }).length;
        return U.pane('Day at a glance', '', '', '<div class="dp-sec">' + U.kv([['Appointments', appts.length], ['Confirmed', conf + ' of ' + appts.length], ['Benefits not verified', unv ? '<span class="s-amb">' + unv + '</span>' : '0'], ['Online requests', appts.filter(function (x) { return !x.mrn; }).length], ['Scheduled production', M0(sched)]]) +
          '<div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap"><button class="mini" id="verify-day">Verify benefits for this day</button><button class="mini" id="remind">Send reminders</button></div></div>' +
          '<div class="dp-sec"><div class="dp-h">Online booking</div><div class="mut" style="font-size:12px;margin-bottom:6px">Patients book into open chair time from your providers\' hours.</div><button class="mini" id="booklink">Copy booking link</button> <a class="mini" style="display:inline-flex;align-items:center" target="_blank" href="/dental/book/' + E(orgKey()) + '">Open booking page</a></div>' +
          '<div class="dp-sec mut" style="font-size:12px">Drag an appointment to move it to another chair or time. Click an empty slot to book.</div>');
      }
      var f = a.flags || {}, acts = [];
      if (!a.mrn) acts.push(['Accept request', 'accept', 1]);
      if (a.status === 'scheduled') acts.push(['Confirm', 'confirmed']);
      if (['scheduled', 'confirmed'].indexOf(a.status) >= 0) acts.push(['Arrived', 'arrived']);
      if (['scheduled', 'confirmed', 'arrived'].indexOf(a.status) >= 0) acts.push(['Seat', 'seated']);
      if (['arrived', 'seated'].indexOf(a.status) >= 0) acts.push(['Complete', 'complete', 1]);
      if (['scheduled', 'confirmed'].indexOf(a.status) >= 0) { acts.push(['No show', 'no_show']); acts.push(['Cancel', 'cancelled']); }
      var r = a.mrn ? '' : '<div class="dp-sec"><div class="dp-h">Online request</div><div class="mut">Someone new booked this online. Accept to make them a patient and keep the time.</div></div>';
      return U.pane(E(a.patient || 'Online request'), '', a.mrn ? '<a href="/v4/dental/chart?mrn=' + encodeURIComponent(a.mrn) + '">Open chart</a>' : '',
        r + '<div class="dp-sec"><div class="dp-h">Appointment</div>' + U.kv([['When', DA.us(a.start.slice(0, 10)) + ', ' + DA.time(a.start) + ' (' + a.minutes + ' min)'], ['Provider', E(a.provider)], ['Status', E(a.status.replace('_', ' '))], ['Reason', E(a.reason || '')], ['Booked', a.source === 'online' ? 'Online' : a.source === 'recall' ? 'From recall text' : 'By staff']]) +
        '<div style="display:flex;gap:5px;flex-wrap:wrap;margin-top:8px">' + acts.map(function (x) { return '<button class="mini' + (x[2] ? ' pri' : '') + '" data-st="' + x[1] + '">' + x[0] + '</button>'; }).join('') + '</div></div>' +
        (a.mrn ? '<div class="dp-sec"><div class="dp-h">Before they sit down</div><div class="ok-list">' +
          (f.payer ? (f.elig_active ? '<div>' + U.I.check + 'Benefits verified ' + DA.us(f.elig) + ' (' + E(f.payer) + ')</div>' : '<div class="w">' + U.warn + 'Benefits not verified (' + E(f.payer) + ')</div>') : '<div class="w">' + U.warn + 'No insurance on file</div>') +
          (f.balance > 0 ? '<div class="w">' + U.warn + 'Balance ' + M(f.balance) + '</div>' : '<div>' + U.I.check + 'No balance</div>') +
          (f.allergies && !/^nkda$/i.test(f.allergies) ? '<div class="w s-red">' + U.err + 'Allergy: ' + E(f.allergies) + '</div>' : '') +
          (f.medical ? '<div class="w">' + U.warn + E(f.medical) + '</div>' : '') + '</div>' +
          '<div style="display:flex;gap:5px;flex-wrap:wrap;margin-top:8px"><button class="mini" id="ap-verify">Verify benefits</button><button class="mini" id="ap-text">Text patient</button><button class="mini" id="ap-pay">Collect payment</button></div></div>' : '') +
        ((a.procedures || []).length ? '<div class="dp-sec"><div class="dp-h">Planned procedures</div>' + U.grid(['Code', 'Tooth', ['Fee', 'num']], a.procedures.map(function (p) { return { cells: ['<span class="mono">' + E(p.code) + '</span> ' + E(DA.desc(p.code)), E(p.tooth || p.quad || ''), M(DA.fee(p.code))] }; }), { tight: 1 }) + '</div>' : '') +
        '<div class="dp-sec"><button class="mini" id="ap-edit">Edit appointment</button> ' + (a.status === 'complete' ? '<button class="mini" id="ap-review">Ask for a review</button>' : '') + '</div>');
    }
    function orgKey() { var u = window.__ALTHAIS_USER__ || {}; return 'org_' + String(u.organization || u.email || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, ''); }
    function draw() {
      var ops = data.operatories, hours = data.hours, provs = {};
      data.providers.forEach(function (p) { provs[p.id] = p; });
      if (!ops.length) { app.innerHTML = DA.frame('dschedule', 'Schedule') + DA.needSample(); return; }
      var dayStart = 7 * 60, dayEnd = 17 * 60;
      hours.forEach(function (h) { var s = +h.start.split(':')[0] * 60 + +h.start.split(':')[1], e = +h.end.split(':')[0] * 60 + +h.end.split(':')[1]; dayStart = Math.min(dayStart, s); dayEnd = Math.max(dayEnd, e); });
      data.appointments.forEach(function (a) { dayStart = Math.min(dayStart, mins(a.start)); dayEnd = Math.max(dayEnd, mins(a.start) + a.minutes); });
      dayStart = Math.floor(dayStart / 60) * 60; dayEnd = Math.ceil(dayEnd / 60) * 60;
      var H = (dayEnd - dayStart) * PPM, cols = '62px repeat(' + ops.length + ', minmax(150px, 1fr))';
      var head = '<div class="ops-head" style="grid-template-columns:' + cols + '"><div></div>' + ops.map(function (o) {
        var hs = hours.filter(function (h) { return h.operatory_id === o.id; }), p = hs[0] && provs[hs[0].provider_id];
        return '<div><span class="kind">' + E(o.kind) + '</span> ' + E(o.name) + (p ? ' <small>· <span style="color:' + p.color + '">●</span> ' + E(p.name.replace(/^Dr\. /, 'Dr. ').replace(/, RDH$/, '')) + '</small>' : ' <small>· open</small>') + '</div>';
      }).join('') + '</div>';
      var times = '';
      for (var m = dayStart; m <= dayEnd; m += 60) times += '<div style="top:' + ((m - dayStart) * PPM) + 'px">' + hm(m) + '</div>';
      var body = '<div class="ops-body" style="grid-template-columns:' + cols + ';height:' + H + 'px"><div class="ops-time">' + times + '</div>' + ops.map(function (o) {
        var hs = hours.filter(function (h) { return h.operatory_id === o.id; }), col = '';
        hs.forEach(function (h) {
          var s = +h.start.split(':')[0] * 60 + +h.start.split(':')[1], e = +h.end.split(':')[0] * 60 + +h.end.split(':')[1];
          col += '<div class="work" style="top:' + ((s - dayStart) * PPM) + 'px;height:' + ((e - s) * PPM) + 'px"></div>';
          if (h.lunch_start) { var ls = +h.lunch_start.split(':')[0] * 60 + +h.lunch_start.split(':')[1], le = +h.lunch_end.split(':')[0] * 60 + +h.lunch_end.split(':')[1]; col += '<div class="lunch" style="top:' + ((ls - dayStart) * PPM) + 'px;height:' + ((le - ls) * PPM) + 'px">Lunch</div>'; }
        });
        for (var mm = dayStart + 60; mm < dayEnd; mm += 60) col += '<div class="hr" style="top:' + ((mm - dayStart) * PPM) + 'px"></div>';
        data.appointments.filter(function (a) { return a.operatory_id === o.id && (!provFilter || a.provider_id === provFilter); }).forEach(function (a) { col += apptHtml(a, dayStart); });
        return '<div class="ops-col' + (hs.length ? '' : ' off') + '" data-op="' + o.id + '" style="background-size:100% ' + (10 * PPM) + 'px">' + col + '</div>';
      }).join('');
      var now = new Date();
      if (day === DA.today()) { var nm = now.getHours() * 60 + now.getMinutes(); if (nm >= dayStart && nm <= dayEnd) body += '<div class="nowline" style="top:' + ((nm - dayStart) * PPM) + 'px;left:62px"></div>'; }
      body += '</div>';
      var unassigned = data.appointments.filter(function (a) { return !a.operatory_id; });
      var dlong = new Date(day + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
      var cnt = {}; data.appointments.forEach(function (a) { if (a.status !== 'cancelled') cnt[a.provider_id] = (cnt[a.provider_id] || 0) + 1; });
      app.innerHTML = DA.frame('dschedule', 'Schedule', [['New appointment', 'plus', { pri: 1, id: 'newap' }], ['New patient', 'user', { id: 'newpt' }], '|', ['Verify benefits', 'check', { id: 'verify-day2' }], ['Send reminders', 'send', { id: 'remind2' }], ['Print day sheet', 'print', { id: 'print' }]],
        (data.locations.length > 1 ? '<select class="mini" id="loc" style="height:24px">' + data.locations.map(function (l) { return '<option value="' + l.id + '"' + (l.id === loc ? ' selected' : '') + '>' + E(l.name) + '</option>'; }).join('') + '</select> ' : '') +
        '<button class="mini" data-nav="-1">‹</button> <button class="mini" data-nav="0">Today</button> <button class="mini" data-nav="1">›</button> <input type="date" class="mini" id="goday" value="' + day + '">' + DA.mock()) +
        '<div class="main" style="grid-template-columns:200px minmax(0,1fr) 320px">' +
          U.pane(dlong.split(',')[0], DA.short(day), '', U.queue([{ h: 'Providers', items: [{ key: '0', label: 'Everyone', n: data.appointments.filter(function (a) { return a.status !== 'cancelled'; }).length, color: 'blue' }].concat(data.providers.filter(function (p) { return cnt[p.id] || data.hours.some(function (h) { return h.provider_id === p.id; }); }).map(function (p) { return { key: String(p.id), label: p.name, n: cnt[p.id] || 0, color: p.color }; })) },
            { h: 'Status', items: [['scheduled', 'Unconfirmed', 'gray'], ['confirmed', 'Confirmed', 'grn'], ['arrived', 'Arrived', 'blue'], ['seated', 'In chair', 'blue'], ['complete', 'Done', 'gray'], ['no_show', 'No show', 'amb']].map(function (s) { return { key: 's:' + s[0], label: s[1], n: data.appointments.filter(function (a) { return a.status === s[0]; }).length, color: s[2] }; }) }], String(provFilter)) +
            '<div class="sum"><div><span>Legend</span><b></b></div><div><span><b class="mockflag" style="color:var(--grn);border-color:var(--grn-line);background:var(--grn-bg)">INS</b> verified</span></div><div><span><b class="mockflag">INS?</b> not verified</span></div><div><span><b class="mockflag" style="color:var(--red);background:var(--red-bg);border-color:var(--red-line)">$</b> balance</span></div><div><span><b class="mockflag" style="color:#fff;background:var(--red);border-color:var(--red)">⚕ A</b> medical / allergy</span></div></div>', { cls: '' }) +
          '<section class="pane"><div class="pbody" id="opsbody"><div class="ops">' + head + body + '</div>' + (unassigned.length ? '<div class="dp-sec">Not in a chair: ' + unassigned.map(function (a) { return '<a href="#" data-sel="' + a.id + '">' + E(a.patient) + ' ' + DA.time(a.start) + '</a>'; }).join(', ') + '</div>' : '') + '</div></section>' +
          right() + '</div>';
      U.fit(); U.search();
      var ob = document.getElementById('opsbody'); if (ob && !draw._scrolled) { ob.scrollTop = Math.max(0, ((day === DA.today() ? new Date().getHours() * 60 : 8 * 60) - dayStart - 60) * PPM); draw._scrolled = 1; }
      draw._dayStart = dayStart;
    }
    function go(d) { day = d; draw._scrolled = 0; history.replaceState(null, '', '/v4/dental/schedule?date=' + day + (loc ? '&loc=' + loc : '')); load(); }
    load();
    U.on(app, '[data-nav]', 'click', function (b) { var n = +b.dataset.nav; go(n === 0 ? DA.today() : DA.addDays(day, n)); });
    U.on(app, '#goday', 'change', function (i) { if (i.value) go(i.value); });
    U.on(app, '#loc', 'change', function (s) { loc = +s.value; draw._scrolled = 0; load(); });
    U.on(app, '[data-q]', 'click', function (el) { var k = el.dataset.q; if (k.indexOf('s:') === 0) { var st = k.slice(2), first = data.appointments.filter(function (a) { return a.status === st; })[0]; if (first) { sel = first.id; draw(); } return; } provFilter = +k; draw(); });
    U.on(app, '.ap', 'click', function (el, e) { e.stopPropagation(); sel = +el.dataset.ap; draw(); });
    U.on(app, '[data-sel]', 'click', function (el, e) { e.preventDefault(); sel = +el.dataset.sel; draw(); });
    var slotAt = function (col, ev) { var r = col.getBoundingClientRect(), y = ev.clientY - r.top, m = draw._dayStart + Math.round(y / PPM / 10) * 10; return m; };
    var isoAt = function (m) { return day + 'T' + ('0' + Math.floor(m / 60)).slice(-2) + ':' + ('0' + (m % 60)).slice(-2); };
    U.on(app, '.ops-col', 'click', function (col, ev) {
      if (ev.target.closest('.ap')) return;
      var m = slotAt(col, ev), op = +col.dataset.op, h = data.hours.filter(function (x) { return x.operatory_id === op; })[0];
      apptForm(null, { start: isoAt(m), operatory_id: op, provider_id: h ? h.provider_id : null, location_id: loc });
    });
    var dragId = 0;
    app.addEventListener('dragstart', function (e) { var a = e.target.closest && e.target.closest('.ap'); if (!a) return; dragId = +a.dataset.ap; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(dragId)); });
    app.addEventListener('dragover', function (e) {
      var col = e.target.closest && e.target.closest('.ops-col'); if (!col || !dragId) return; e.preventDefault();
      var a = data.appointments.filter(function (x) { return x.id === dragId; })[0], m = slotAt(col, e), box = col.querySelector('.drop');
      if (!box) { app.querySelectorAll('.drop').forEach(function (b) { b.remove(); }); box = document.createElement('div'); box.className = 'drop'; col.appendChild(box); }
      box.style.top = ((m - draw._dayStart) * PPM) + 'px'; box.style.height = (a.minutes * PPM) + 'px';
    });
    app.addEventListener('dragend', function () { app.querySelectorAll('.drop').forEach(function (b) { b.remove(); }); dragId = 0; });
    app.addEventListener('drop', function (e) {
      var col = e.target.closest && e.target.closest('.ops-col'); if (!col || !dragId) return; e.preventDefault();
      var m = slotAt(col, e), op = +col.dataset.op, h = data.hours.filter(function (x) { return x.operatory_id === op; })[0], id = dragId; dragId = 0;
      var a = data.appointments.filter(function (x) { return x.id === id; })[0];
      DA.post('/appointments', { id: id, start: isoAt(m), operatory_id: op, provider_id: h && a && DA.provs[h.provider_id] && DA.provs[a.provider_id] && DA.provs[h.provider_id].kind === DA.provs[a.provider_id].kind ? h.provider_id : a.provider_id })
        .then(function () { sel = id; U.toast('Moved to ' + DA.time(isoAt(m)) + '.', 'ok'); load(); }, function (er) { DA.fail(er); load(); });
    });
    function apptForm(a, preset) {
      var v = Object.assign({ minutes: 60, reason: '' }, a || {}, preset || {}), st = v.start || (day + 'T09:00');
      var provOpts = (DA.setup.providers || []).map(function (p) { return [p.id, p.name]; }), opOpts = data.operatories.map(function (o) { return [o.id, o.name + ' (' + o.kind + ')']; });
      var open = function (mrn, pname) {
        U.form(a ? 'Edit appointment' : 'New appointment', [
          { k: 'who', label: 'Patient', ro: 1, def: pname || '', span: 2 }, { k: 'date', label: 'Date', type: 'date', def: st.slice(0, 10), req: 1 }, { k: 'time', label: 'Time', type: 'time', def: st.slice(11, 16), step: 600, req: 1 },
          { k: 'provider_id', label: 'Provider', type: 'select', options: provOpts, def: v.provider_id }, { k: 'operatory_id', label: 'Chair', type: 'select', options: opOpts, def: v.operatory_id },
          { k: 'minutes', label: 'Length (minutes)', type: 'select', options: [[10, '10'], [20, '20'], [30, '30'], [40, '40'], [45, '45'], [60, '60'], [80, '80'], [90, '90'], [120, '120']], def: v.minutes },
          { k: 'reason', label: 'Reason', span: 3, def: v.reason, ph: 'Recall: cleaning, exam, 4BWX / Crown prep #30' },
        ], {}, function (x) {
          DA.post('/appointments', { id: a ? a.id : undefined, mrn: mrn, start: x.date + 'T' + x.time, provider_id: +x.provider_id, operatory_id: +x.operatory_id, minutes: +x.minutes, reason: x.reason, location_id: loc })
            .then(function (r) { sel = r.id; U.toast(a ? 'Saved.' : 'Booked.', 'ok'); if (x.date !== day) go(x.date); else load(); }, DA.fail);
        }, { wide: 1, save: a ? 'Save' : 'Book' });
      };
      if (a) open(a.mrn, a.patient); else DA.pickPatient('Book for which patient?', function (mrn) { DA.get('/patients?q=' + encodeURIComponent(mrn)).then(function (l) { open(mrn, (l.filter(function (p) { return p.mrn === mrn; })[0] || {}).name || mrn); }); });
    }
    var A = function () { return data.appointments.filter(function (x) { return x.id === sel; })[0]; };
    U.on(app, '#newap', 'click', function () { apptForm(null, { location_id: loc }); });
    U.on(app, '#newpt', 'click', function () { DA.patientForm(function (mrn) { location.href = '/v4/dental/chart?mrn=' + encodeURIComponent(mrn); }); });
    U.on(app, '#ap-edit', 'click', function () { apptForm(A()); });
    U.on(app, '[data-st]', 'click', function (b) {
      var a = A(), st = b.dataset.st; if (!a) return;
      if (st === 'accept') return DA.post('/appointments/' + a.id + '/accept-request', {}).then(function (r) { U.toast('Patient created (MRN ' + r.mrn + ') and the time is kept.', 'ok'); load(); }, DA.fail);
      DA.post('/appointments', { id: a.id, status: st }).then(function () { U.toast({ confirmed: 'Confirmed.', arrived: 'Marked arrived.', seated: 'Seated.', complete: 'Visit complete. Post the procedures in the chart.', no_show: 'Marked no show.', cancelled: 'Cancelled.' }[st], 'ok'); if (st === 'complete' && a.mrn) location.href = '/v4/dental/chart?mrn=' + encodeURIComponent(a.mrn) + '&tab=notes&appt=' + a.id; else load(); }, DA.fail);
    });
    var verifyDay = function () { U.toast('Checking benefits for everyone booked…', 'busy'); DA.post('/eligibility/batch', { date: day }).then(function (r) { U.toast('Benefits checked for ' + r.checked.length + ' patients' + (r.skipped.length ? ', ' + r.skipped.length + ' need insurance details first' : '') + '.', 'ok'); load(); }, DA.fail); };
    U.on(app, '#verify-day', 'click', verifyDay); U.on(app, '#verify-day2', 'click', verifyDay);
    var remind = function () { DA.post('/reminders/run').then(function (r) { U.toast(r.sent ? r.sent + ' reminder texts sent.' : 'Everyone due a reminder already has one.', 'ok'); }, DA.fail); };
    U.on(app, '#remind', 'click', remind); U.on(app, '#remind2', 'click', remind);
    U.on(app, '#booklink', 'click', function () { var url = location.origin + '/dental/book/' + orgKey(); (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () { U.toast('Booking link copied.', 'ok'); }, function () { U.ask('Booking link', 'Copy this link', url, function () {}); }); });
    U.on(app, '#ap-verify', 'click', function () { var a = A(); DA.post('/patients/' + encodeURIComponent(a.mrn) + '/eligibility').then(function (b) { U.toast(b.active ? 'Active: ' + M0(b.max_remaining) + ' of ' + M0(b.annual_max) + ' left this year.' : 'Coverage is not active.', b.active ? 'ok' : 'warn'); load(); }, DA.fail); });
    U.on(app, '#ap-text', 'click', function () { var a = A(); U.form('Text ' + a.patient, [{ k: 'body', label: 'Message', type: 'textarea', req: 1, span: 4, def: 'Hi ' + DA.first(a.patient) + ', ' }], {}, function (v) { DA.post('/patients/' + encodeURIComponent(a.mrn) + '/messages', { body: v.body }).then(function () { U.toast('Text sent.', 'ok'); }, DA.fail); }, { save: 'Send' }); });
    U.on(app, '#ap-pay', 'click', function () { var a = A(); DA.takePayment(a.mrn, (a.flags || {}).balance || 0, load); });
    U.on(app, '#ap-review', 'click', function () { DA.post('/appointments/' + A().id + '/review-request').then(function () { U.toast('Review request texted.', 'ok'); }, DA.fail); });
  };

  // card / cash / check at the desk; card goes to the payment processor (mock: tok_visa approves, tok_declined declines)
  DA.takePayment = function (mrn, due, after) {
    U.form('Collect payment', [{ k: 'amount', label: 'Amount', type: 'number', step: '0.01', req: 1, def: due > 0 ? due.toFixed(2) : '' }, { k: 'method', label: 'Method', type: 'select', options: [['card', 'Card (terminal)'], ['cash', 'Cash'], ['check', 'Check']] },
      { k: 'ref', label: 'Check # (if check)' }, { k: 'token', label: 'Card (test token)', type: 'select', options: [['tok_visa', 'Test card: approves'], ['tok_declined', 'Test card: declines']] }], {}, function (v) {
      DA.post('/patients/' + encodeURIComponent(mrn) + '/payments', { amount: +v.amount, method: v.method, ref: v.ref, token: v.token }).then(function (r) { U.toast('Payment of ' + M(+v.amount) + ' recorded. Balance ' + M(r.balance) + '.', 'ok'); if (after) after(); }, DA.fail);
    }, { save: 'Take payment' });
  };

  /* =============================== Patients =============================== */
  V.pages.dpatients = function () {
    var app = V.app, list = [], Q = V.q.get('list') || 'all', s = '';
    var FILTERS = [['all', 'All patients', function () { return true; }], ['recall', 'Recall overdue', function (p) { return p.recall_overdue > 0; }], ['unverified', 'Benefits not verified', function (p) { return p.payer && p.payer !== 'Self-pay' && !p.elig_active; }],
      ['balance', 'Balance due', function (p) { return p.balance > 0; }], ['members', 'Membership plan', function (p) { return p.member_plan; }], ['selfpay', 'No insurance', function (p) { return !p.payer || p.payer === 'Self-pay'; }]];
    function draw() {
      var f = FILTERS.filter(function (x) { return x[0] === Q; })[0][2], rows = list.filter(f).filter(function (p) { return !s || (p.name + ' ' + p.mrn + ' ' + p.dob + ' ' + p.phone).toLowerCase().indexOf(s) >= 0; });
      app.innerHTML = DA.frame('dpatients', 'Patients', [['New patient', 'user', { pri: 1, id: 'newpt' }], '|', ['Import from another system', 'upload', { href: '/v4/dental/setup?sec=import' }]],
        '<input class="mini" id="pq" placeholder="Filter by name, MRN, phone" style="width:220px;height:24px" value="' + E(s) + '">') +
        '<div class="main" style="grid-template-columns:210px minmax(0,1fr)">' +
        U.pane('Lists', '', '', U.queue([{ h: '', items: FILTERS.map(function (x) { return { key: x[0], label: x[1], n: list.filter(x[2]).length, color: x[0] === 'all' ? 'blue' : x[0] === 'recall' || x[0] === 'balance' ? 'amb' : 'lblue' }; }) }], Q)) +
        U.pane('Patients', rows.length + ' shown', '', U.grid(['Patient', 'MRN', 'Age', 'Mobile', 'Dental plan', 'Benefits', 'Recall due', ['Balance', 'num'], 'Plan'], rows.map(function (p) {
          return { attrs: ' data-mrn="' + E(p.mrn) + '"', cells: [V.link(p.name, '/v4/dental/chart?mrn=' + encodeURIComponent(p.mrn)), '<span class="mono">' + E(p.mrn) + '</span>', DA.age(p.dob), E(p.phone || ''), E(p.payer || 'Self-pay'),
            !p.payer || p.payer === 'Self-pay' ? '<span class="mut">n/a</span>' : p.elig_active ? '<span class="s-grn">' + U.I.check + 'Verified ' + DA.short(p.elig) + '</span>' : p.elig ? '<span class="s-red">Inactive</span>' : U.st('Not verified', 'amb'),
            p.recall_overdue > 0 ? '<span class="s-amb">' + DA.us(p.recall_due) + ' (' + p.recall_overdue + 'd late)</span>' : DA.us(p.recall_due), p.balance > 0 ? '<span class="s-red">' + M(p.balance) + '</span>' : M(p.balance), p.member_plan ? '<span class="chip blue">Member</span>' : ''] };
        }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(9, list.length ? 'No patients in this list.' : 'No patients yet.') + '</tbody>') + (list.length ? '' : DA.needSample())) + '</div>';
      U.fit(); U.search();
      var pq = document.getElementById('pq'); pq.oninput = function () { s = pq.value.toLowerCase(); var pos = pq.selectionStart; draw(); var n = document.getElementById('pq'); n.focus(); n.setSelectionRange(pos, pos); };
    }
    DA.get('/patients').then(function (l) { list = l; draw(); }, function (e) { app.innerHTML = DA.frame('dpatients', 'Patients') + DA.blank(E(e.message)); });
    U.on(app, '[data-q]', 'click', function (el) { Q = el.dataset.q; draw(); });
    U.on(app, 'tr[data-mrn]', 'click', function (tr, e) { if (!e.target.closest('a')) location.href = '/v4/dental/chart?mrn=' + encodeURIComponent(tr.dataset.mrn); });
    U.on(app, '#newpt', 'click', function () { DA.patientForm(function (mrn) { location.href = '/v4/dental/chart?mrn=' + encodeURIComponent(mrn); }); });
  };
})();
