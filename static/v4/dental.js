/* Althais v4, dental version: the data and the claim rules a dental clinic's app runs on. Loaded before store.js;
   store.js switches to it when the clinic is set up as dental in /admin (window.__ALTHAIS_USER__.practice).
   The rules are the same as the server's dental_rules.py (tests/test_dental_js.py runs both on the same claims), so a
   claim scores the same here, in the extension and in Connect EMR. CDT descriptions here are short labels in our own
   words, not the ADA's descriptors. */
(function () {
  var X = {};
  var CDT = X.CDT = /^D\d{4}$/;

  // ---- teeth (ADA Universal numbering) ----
  var range = function (a, b) { var o = []; for (var i = a; i <= b; i++) o.push(i); return o; };
  var PERM_MOLAR = [1, 2, 3, 14, 15, 16, 17, 18, 19, 30, 31, 32], PERM_PREMOLAR = [4, 5, 12, 13, 20, 21, 28, 29];
  var PRIM_ANTERIOR = 'CDEFGHMNOPQR', PRIM_MOLAR = 'ABIJKLST';
  var QUADS = ['UR', 'UL', 'LR', 'LL'], ARCHES = ['UA', 'LA'], SURFACES = 'MODBLIF';
  var normTooth = X.normTooth = function (t) { return String(t == null ? '' : t).replace(/[#\s]/g, '').toUpperCase(); };
  var normSurface = X.normSurface = function (s) { var o = ''; String(s == null ? '' : s).replace(/[^A-Za-z]/g, '').toUpperCase().split('').forEach(function (ch) { if (o.indexOf(ch) < 0) o += ch; }); return o; };
  // ['perm'|'prim'|'super', 'anterior'|'premolar'|'molar'|null], or null when it isn't a tooth number
  var toothKind = X.toothKind = function (t) {
    if (!t) return null;
    if (/^\d+$/.test(t)) {
      var n = +t;
      if (n >= 1 && n <= 32) return ['perm', PERM_MOLAR.indexOf(n) >= 0 ? 'molar' : PERM_PREMOLAR.indexOf(n) >= 0 ? 'premolar' : 'anterior'];
      if (n >= 51 && n <= 82) return ['super', null];
      return null;
    }
    var all = PRIM_ANTERIOR + PRIM_MOLAR;
    if (t.length === 1 && all.indexOf(t) >= 0) return ['prim', PRIM_ANTERIOR.indexOf(t) >= 0 ? 'anterior' : 'molar'];
    if (t.length === 2 && t[1] === 'S' && all.indexOf(t[0]) >= 0) return ['super', null];
    return null;
  };

  // ---- code ranges ----
  var num = function (code) { return CDT.test(code || '') ? +code.slice(1) : -1; };
  var inR = function (code) { var n = num(code), rs = [].slice.call(arguments, 1); return rs.some(function (r) { return n >= r[0] && n <= r[1]; }); };
  var CATS = [[[100, 999], 'diagnostic'], [[1000, 1999], 'preventive'], [[2000, 2999], 'restorative'], [[3000, 3999], 'endodontics'], [[4000, 4999], 'periodontics'],
    [[5000, 5899], 'removable prosthodontics'], [[5900, 5999], 'maxillofacial prosthetics'], [[6000, 6199], 'implant services'], [[6200, 6999], 'fixed prosthodontics'],
    [[7000, 7999], 'oral surgery'], [[8000, 8999], 'orthodontics'], [[9000, 9999], 'adjunctive services']];
  var category = X.category = function (code) { var n = num(code); for (var i = 0; i < CATS.length; i++) if (n >= CATS[i][0][0] && n <= CATS[i][0][1]) return CATS[i][1]; return ''; };
  var set = function (a) { var o = {}; a.forEach(function (x) { o[x] = 1; }); return o; };
  var ROUTINE_EXAMS = set(['D0120', 'D0150', 'D0180']), CLEANINGS = set(['D1110', 'D1120']), PERIO_MAINT = set(['D4910']), SRP = set(['D4341', 'D4342']);
  var BITEWINGS = set(['D0270', 'D0272', 'D0273', 'D0274']), FULL_SERIES = set(['D0210', 'D0330']), FLUORIDE = set(['D1206', 'D1208']), SEALANT = set(['D1351']);
  var needsTooth = X.needsTooth = function (c) { return inR(c, [2000, 2999], [3000, 3999], [6000, 6199], [6200, 6999], [7111, 7250], [1351, 1355], [4249, 4249], [4381, 4381]); };
  var needsQuad = X.needsQuad = function (c) { return inR(c, [4210, 4212], [4240, 4241], [4260, 4261], [4341, 4342], [7310, 7321]); };
  var needsArch = X.needsArch = function (c) { return inR(c, [5110, 5226]); };
  var SURF_COUNT = {};
  [[['D2140', 'D2330', 'D2391', 'D2410', 'D2510', 'D2610', 'D2650'], [1, 1]], [['D2150', 'D2331', 'D2392', 'D2420', 'D2520', 'D2620', 'D2651', 'D2542', 'D2642', 'D2662'], [2, 2]],
   [['D2160', 'D2332', 'D2393', 'D2430', 'D2530', 'D2630', 'D2652', 'D2543', 'D2643', 'D2663'], [3, 3]], [['D2161', 'D2335', 'D2394', 'D2544', 'D2644', 'D2664'], [4, 5]]]
    .forEach(function (g) { g[0].forEach(function (c) { SURF_COUNT[c] = g[1]; }); });
  X.SURF_COUNT = SURF_COUNT;
  var FAMILIES = [['D2140', 'D2150', 'D2160', 'D2161'], ['D2330', 'D2331', 'D2332', 'D2335'], ['D2391', 'D2392', 'D2393', 'D2394'], ['D2410', 'D2420', 'D2430', null], ['D2510', 'D2520', 'D2530', null], ['D2610', 'D2620', 'D2630', null]];
  var matchingCode = X.matchingCode = function (code, n) { for (var i = 0; i < FAMILIES.length; i++) if (FAMILIES[i].indexOf(code) >= 0) return n >= 1 ? FAMILIES[i][Math.min(n, 4) - 1] : null; return null; };
  var ANTERIOR_ONLY = set(['D2330', 'D2331', 'D2332', 'D2335', 'D3310']), PREMOLAR_ONLY = set(['D3320']), MOLAR_ONLY = set(['D3330']), POSTERIOR_ONLY = set(['D2391', 'D2392', 'D2393', 'D2394']), PERMANENT_ONLY = set(['D3310', 'D3320', 'D3330']);
  var EXTRACTION = [7111, 7250], CROWN = [2710, 2799];
  var DIRECT_FILL = function (c) { return inR(c, [2140, 2161], [2330, 2394]); };
  var ATTACH = [
    [[[2710, 2799], [2950, 2950]], 'a pre-op X-ray and a short narrative (why a crown, e.g. fracture or large failing filling)'],
    [[[6010, 6199], [6200, 6999]], 'X-rays and a narrative, and often a pre-authorization'],
    [[[4341, 4342]], 'a periodontal chart with pocket depths (usually 4 mm or more) and recent X-rays'],
    [[[7210, 7250]], 'an X-ray and a narrative for the surgical extraction'],
    [[[3310, 3348]], 'a pre-op X-ray'],
    [[[8010, 8999]], 'a pre-authorization and orthodontic records'],
    [[[2999, 2999], [9999, 9999], [6999, 6999], [7999, 7999], [4999, 4999]], 'a narrative describing the procedure (unspecified codes are always reviewed)']];
  var PREAUTH = [[2710, 2799], [6010, 6199], [6200, 6999], [5110, 5226], [8010, 8999]];
  X.isDentalCode = function (c) { return CDT.test(String(c || '').toUpperCase()); };

  var age = function (dob, on) {
    var b = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dob || ''), d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(on || ''); if (!b || !d) return null;
    var a = +d[1] - +b[1]; if (+d[2] < +b[2] || (+d[2] === +b[2] && +d[3] < +b[3])) a--; return a;
  };
  var keys = function (o) { return Object.keys(o).sort(); };
  var both = function (codes, group) { return codes.filter(function (x) { return group[x]; }).sort(); };

  /* One claim: c = {cpt: [codes], lines: [{code, tooth, surface, quad}], tooth_column, dob, dos}. Returns
     [{severity: 'error'|'warn'|'info', code, message, tooth?}], in the same order and words as dental_rules.check. */
  X.check = function (c) {
    var out = [];
    var add = function (sev, code, msg, tooth) { var f = { severity: sev, code: code, message: msg }; if (tooth) f.tooth = tooth; out.push(f); };
    var lines = (c.lines || []).filter(function (l) { return String(l.code || '').trim(); }).map(function (l) {
      return { code: String(l.code).trim().toUpperCase(), tooth: normTooth(l.tooth), surface: normSurface(l.surface), quad: String(l.quad || '').trim().toUpperCase() };
    });
    var codes = (c.cpt || []).map(function (x) { return String(x).toUpperCase(); });
    lines.forEach(function (l) { if (codes.indexOf(l.code) < 0) codes.push(l.code); });
    var detail = !!lines.length && c.tooth_column !== false;
    codes.forEach(function (code) { if (code[0] === 'D' && !CDT.test(code)) add('error', 'bad_cdt', code + " isn't a dental (CDT) code. CDT codes are D followed by four digits, e.g. D2392."); });

    var missing = [];
    lines.forEach(function (l) {
      var code = l.code, t = l.tooth, s = l.surface, q = l.quad;
      if (!CDT.test(code)) return;
      var kind = t ? toothKind(t) : null;
      if (t && !kind) { add('error', 'bad_tooth', 'Tooth ' + t + ' on ' + code + " isn't a valid tooth number. Use 1–32 for permanent teeth or A–T for primary teeth.", t); return; }
      if (needsTooth(code) && !t) { missing.push(code); if (detail) add('error', 'no_tooth', code + ' needs a tooth number. Dental plans deny ' + category(code) + ' codes without one.'); }
      if (needsQuad(code) && !(QUADS.indexOf(q) >= 0 || (t && kind))) add('error', 'no_quad', code + ' needs a quadrant (UR, UL, LR or LL).');
      if (needsArch(code) && ARCHES.indexOf(q) < 0 && ['U', 'L', 'UPPER', 'LOWER'].indexOf(q) < 0) add('error', 'no_arch', code + ' needs the arch (upper or lower).');
      if (SURF_COUNT[code]) {
        var lo = SURF_COUNT[code][0], hi = SURF_COUNT[code][1];
        if (!s) add('error', 'no_surface', code + ' on tooth ' + (t || '?') + ' needs its surfaces (M, O, D, B/F, L, I). It\'s a ' + lo + (hi > lo ? '+' : '') + '-surface code.', t);
        else {
          var bad = s.split('').filter(function (ch) { return SURFACES.indexOf(ch) < 0; });
          if (bad.length) add('error', 'bad_surface', 'Surface ' + bad.join('') + ' on tooth ' + t + ' (' + code + ") isn't a surface letter. Use M, O, D, B or F, L, I.", t);
          else if (!(s.length >= lo && s.length <= Math.max(hi, lo)) && !(hi >= 4 && s.length >= 4)) {
            var right = matchingCode(code, s.length);
            add('error', 'surface_count', code + ' is a ' + lo + (hi > lo ? '+' : '') + '-surface code, but tooth ' + t + ' has ' + s.length + ' surfaces (' + s + ').' + (right ? ' Bill ' + right + ' instead.' : ' Bill the code that matches the number of surfaces.'), t);
          }
          if (kind && kind[1] === 'anterior' && s.indexOf('O') >= 0) add('error', 'bad_surface', 'Tooth ' + t + ' is a front tooth, which has no occlusal (O) surface. Use I (incisal) instead.', t);
          if (kind && (kind[1] === 'premolar' || kind[1] === 'molar') && s.indexOf('I') >= 0) add('error', 'bad_surface', 'Tooth ' + t + ' is a back tooth, which has no incisal (I) surface. Use O (occlusal) instead.', t);
        }
      }
      if (kind) {
        var group = kind[1];
        if (ANTERIOR_ONLY[code] && group !== 'anterior') add('warn', 'tooth_type', code + ' is for front teeth, but tooth ' + t + ' is a ' + group + '. Check the code or the tooth number.', t);
        if (POSTERIOR_ONLY[code] && group === 'anterior') add('warn', 'tooth_type', code + ' is for back teeth, but tooth ' + t + ' is a front tooth. Use the front-tooth composite code (D2330–D2335).', t);
        if (PREMOLAR_ONLY[code] && group !== 'premolar') add('error', 'tooth_type', code + ' is the premolar root canal code, but tooth ' + t + ' is a ' + group + '.', t);
        if (MOLAR_ONLY[code] && group !== 'molar') add('error', 'tooth_type', code + ' is the molar root canal code, but tooth ' + t + ' is a ' + group + '.', t);
        if (PERMANENT_ONLY[code] && kind[0] === 'prim') add('error', 'tooth_type', code + ' is for permanent teeth; tooth ' + t + ' is a primary (baby) tooth. Primary root canal codes are different (D3230/D3240).', t);
      }
    });
    if (!detail) missing = codes.filter(function (x) { return CDT.test(x) && needsTooth(x); });
    if (missing.length && !detail) add('info', 'tooth_unseen', "Tooth numbers weren't found on this screen for " + keys(set(missing)).join(', ') + '. Check them before sending; dental plans deny these without one.');

    // same tooth, same visit
    var byTooth = {}, order = [];
    lines.forEach(function (l) { if (l.tooth) { if (!byTooth[l.tooth]) { byTooth[l.tooth] = []; order.push(l.tooth); } byTooth[l.tooth].push(l); } });
    order.forEach(function (t) {
      var ls = byTooth[t], cs = ls.map(function (l) { return l.code; });
      var ext = cs.filter(function (x) { return inR(x, EXTRACTION); });
      if (ext.length && cs.length > 1) { var others = cs.filter(function (x) { return ext.indexOf(x) < 0; }); if (others.length) add('error', 'same_tooth', 'Tooth ' + t + ' is extracted (' + ext[0] + ') on this claim but also has ' + others.join(', ') + ". A tooth that's taken out can't be restored at the same visit.", t); }
      if (cs.some(function (x) { return inR(x, CROWN); }) && cs.some(DIRECT_FILL)) add('warn', 'same_tooth', 'Tooth ' + t + ' has a crown and a filling at the same visit. Plans usually pay a core buildup (D2950) under a crown, not a filling.', t);
      var seen = {};
      ls.forEach(function (l) { var k = l.code + '|' + l.surface; if (seen[k]) add('error', 'dup_line', l.code + ' is billed twice on tooth ' + t + (l.surface ? ' (' + l.surface + ')' : '') + '.', t); seen[k] = 1; });
    });

    // same visit combinations
    var cs = keys(set(codes)), has = function (g) { return both(cs, g); };
    if (has(ROUTINE_EXAMS).length > 1) add('warn', 'two_exams', has(ROUTINE_EXAMS).join(' and ') + ' are both exams. Plans pay one exam per visit.');
    if (has(CLEANINGS).length && has(PERIO_MAINT).length) add('warn', 'clean_and_maint', has(CLEANINGS).join(', ') + ' and D4910 at one visit. A patient gets either a regular cleaning or periodontal maintenance, not both.');
    if (has(CLEANINGS).length && has(SRP).length) add('warn', 'clean_and_srp', has(CLEANINGS).join(', ') + ' with scaling and root planing (' + has(SRP).join(', ') + ') on the same day is usually denied or paid as one.');
    if (cs.indexOf('D0210') >= 0 && has(BITEWINGS).length) add('warn', 'fmx_and_bw', 'A full-mouth series (D0210) already includes bitewings, so ' + has(BITEWINGS).join(', ') + ' at the same visit is usually denied.');
    var a = age(c.dob, c.dos);
    if (a != null) {
      if (has(FLUORIDE).length && a >= 19) add('warn', 'age_limit', 'Fluoride (' + has(FLUORIDE).join(', ') + ') for a ' + a + '-year-old. Many plans cover it only for children (often under 14 or 19).');
      if (has(SEALANT).length && a >= 16) add('warn', 'age_limit', 'Sealants (D1351) for a ' + a + '-year-old. Many plans cover them only for children and teens.');
      if (has(CLEANINGS).length && cs.indexOf('D1120') >= 0 && a >= 14) add('warn', 'age_limit', 'D1120 is the child cleaning code, but the patient is ' + a + '. Adults use D1110.');
      if (cs.indexOf('D1110') >= 0 && a < 12) add('info', 'age_limit', 'D1110 is the adult cleaning code and the patient is ' + a + '. Many plans use D1120 for children under 14.');
    }
    var asked = {};
    cs.forEach(function (code) {
      for (var i = 0; i < ATTACH.length; i++) {
        if (inR.apply(null, [code].concat(ATTACH[i][0])) && !asked[ATTACH[i][1]]) { asked[ATTACH[i][1]] = 1; add('warn', 'attach', code + ': send ' + ATTACH[i][1] + " with this claim, or it's likely to come back pending."); break; }
      }
    });
    var major = cs.filter(function (x) { return inR.apply(null, [x].concat(PREAUTH)); });
    if (major.length) add('info', 'preauth', major.join(', ') + ': many plans want a pre-authorization before this work. Check whether this plan needs one.');
    return out;
  };

  // frequency limits across a patient's visits: claims [{dos, cpt, lines}] -> {index: [flags]}
  var FREQ = [['exam', ROUTINE_EXAMS, 2, 365, 'exams'], ['cleaning', CLEANINGS, 2, 365, 'regular cleanings'], ['bitewings', BITEWINGS, 1, 365, 'sets of bitewing X-rays'],
    ['fullseries', FULL_SERIES, 1, 3 * 365, 'full-mouth or panoramic X-rays'], ['fluoride', FLUORIDE, 2, 365, 'fluoride treatments'], ['maint', PERIO_MAINT, 4, 365, 'periodontal maintenance visits']];
  var th = function (n) { return n % 100 >= 10 && n % 100 <= 20 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' })[n % 10] || 'th'; };
  var days = function (a, b) { return Math.round((Date.parse(a + 'T12:00:00Z') - Date.parse(b + 'T12:00:00Z')) / 864e5); };
  X.historyFlags = function (claims) {
    var out = {}, visits = [];
    claims.forEach(function (c, i) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(c.dos || '')) return;
      var codes = {}; (c.cpt || []).forEach(function (x) { codes[String(x).toUpperCase()] = 1; }); (c.lines || []).forEach(function (l) { codes[String(l.code || '').toUpperCase()] = 1; });
      if (!Object.keys(codes).some(function (x) { return CDT.test(x); })) return;
      visits.push({ d: c.dos, i: i, codes: codes });
    });
    visits.sort(function (a, b) { return a.d < b.d ? -1 : a.d > b.d ? 1 : a.i - b.i; });
    var push = function (i, f) { (out[i] = out[i] || []).push(f); };
    FREQ.forEach(function (f) {
      var hits = visits.filter(function (v) { return Object.keys(v.codes).some(function (x) { return f[1][x]; }); });
      hits.forEach(function (h, k) {
        var win = hits.slice(0, k + 1).filter(function (x) { return days(h.d, x.d) < f[3]; });
        if (win.length > f[2]) {
          var span = f[3] === 365 ? '12 months' : '3 years';
          push(h.i, { severity: 'warn', code: 'frequency', message: "This is the patient's " + win.length + th(win.length) + ' visit with ' + f[4] + ' within ' + span + ' (first on ' + win[0].d + '). Most plans cover ' + f[2] + ' per ' + span + ', so this one may be denied.' });
        }
      });
    });
    var crown = {}, srp = {};
    visits.forEach(function (v) {
      (claims[v.i].lines || []).forEach(function (l) {
        var code = String(l.code || '').toUpperCase(), t = normTooth(l.tooth), q = String(l.quad || '').toUpperCase();
        if (inR(code, CROWN) && t) { if (crown[t] && days(v.d, crown[t]) < 5 * 365) push(v.i, { severity: 'warn', code: 'frequency', message: 'Tooth ' + t + ' already had a crown on ' + crown[t] + '. Most plans replace a crown only after 5 years.', tooth: t }); crown[t] = v.d; }
        if (SRP[code] && q) { if (srp[q] && days(v.d, srp[q]) < 2 * 365) push(v.i, { severity: 'warn', code: 'frequency', message: 'Quadrant ' + q + ' already had scaling and root planing on ' + srp[q] + '. Most plans allow it once every 2 years.' }); srp[q] = v.d; }
      });
    });
    return out;
  };

  /* ---- what the dental version of the app shows ---- */
  X.PAYERS = ['Delta Dental PPO', 'MetLife Dental', 'Cigna Dental', 'Aetna Dental', 'Guardian Dental', 'UHC Dental', 'Humana Dental', 'TX Medicaid (Dental)', 'Self-pay'];
  X.VISITS = ['Recall / hygiene', 'Exam', 'Restorative', 'Emergency', 'New patient', 'Consult'];
  // fee schedule: [code, short label (our words), fee]
  X.FEES = [['D0120', 'Periodic exam', 65], ['D0140', 'Limited exam, problem-focused', 85], ['D0150', 'Comprehensive exam', 95], ['D0210', 'Full-mouth X-rays', 140],
    ['D0220', 'Periapical X-ray, first', 32], ['D0230', 'Periapical X-ray, each more', 26], ['D0272', 'Bitewings, two images', 55], ['D0274', 'Bitewings, four images', 75], ['D0330', 'Panoramic X-ray', 120],
    ['D1110', 'Adult cleaning', 110], ['D1120', 'Child cleaning', 80], ['D1206', 'Fluoride varnish', 40], ['D1351', 'Sealant, per tooth', 50],
    ['D2140', 'Amalgam, one surface', 120], ['D2150', 'Amalgam, two surfaces', 150], ['D2160', 'Amalgam, three surfaces', 180],
    ['D2330', 'Front composite, one surface', 140], ['D2331', 'Front composite, two surfaces', 175], ['D2332', 'Front composite, three surfaces', 205],
    ['D2391', 'Back composite, one surface', 160], ['D2392', 'Back composite, two surfaces', 210], ['D2393', 'Back composite, three surfaces', 250], ['D2394', 'Back composite, four or more', 290],
    ['D2740', 'Crown, porcelain/ceramic', 1150], ['D2750', 'Crown, porcelain fused to metal', 1100], ['D2950', 'Core buildup', 260],
    ['D3310', 'Root canal, front tooth', 850], ['D3320', 'Root canal, premolar', 980], ['D3330', 'Root canal, molar', 1200],
    ['D4341', 'Scaling and root planing, 4+ teeth, per quadrant', 240], ['D4342', 'Scaling and root planing, 1 to 3 teeth', 170], ['D4910', 'Periodontal maintenance', 140],
    ['D7140', 'Extraction, erupted tooth', 185], ['D7210', 'Surgical extraction', 320], ['D9110', 'Palliative (emergency) treatment', 95]];
  // the dental checks, as cards on the Payer rules page; each edit's id starts with one of these
  var R = function (id, title, applies, lvl, text, how) { return { id: id, payer: 'All dental plans', pub: '', eff: '', as: 'Claim edit', title: title, applies: applies, lvl: lvl, req: title, text: text, how: how, src: 'ADA claim form and common plan limits' }; };
  X.RULES = [
    R('DEN-TOOTH', 'Tooth number on every per-tooth code', 'Fillings, crowns, root canals, implants, extractions, sealants', 'red', 'Restorative, endodontic, implant, extraction and sealant codes need a valid tooth number (1 to 32, or A to T for primary teeth).', ['Checks every procedure line for a tooth number', 'Checks the number is a real tooth']),
    R('DEN-SURF', 'Surfaces match the filling code', 'Amalgam, composite, inlay and onlay codes', 'red', 'Filling codes are billed by the number of surfaces. The surfaces on the line must match the code, and a front tooth has no occlusal surface, a back tooth no incisal one.', ['Counts the surfaces on each line', 'Names the code that matches when they differ']),
    R('DEN-QUAD', 'Quadrant or arch when the code needs one', 'Scaling and root planing, gingivectomy, alveoloplasty, dentures', 'red', 'Per-quadrant codes need UR, UL, LR or LL; denture codes need the arch.', ['Checks each line for its quadrant or arch']),
    R('DEN-TOOTHTYPE', 'Code fits the tooth', 'Front and back composites, root canals', 'red', 'Front-tooth composite codes go on front teeth, back-tooth codes on back teeth; root canal codes differ for front teeth, premolars, molars and primary teeth.', ['Compares each code with the kind of tooth it is billed on']),
    R('DEN-SAMETOOTH', 'Same-tooth conflicts', 'Any two lines on one tooth', 'red', 'A tooth extracted at a visit can’t be restored at the same visit; a crown and a filling on one tooth is usually paid as a buildup; the same code twice on one tooth is a duplicate.', ['Groups the lines by tooth']),
    R('DEN-SAMEDAY', 'Same-visit combinations', 'Exams, cleanings, periodontal care, X-rays', 'amb', 'One exam per visit; a regular cleaning or periodontal maintenance, not both; a full-mouth series already includes bitewings.', ['Looks at all the codes on the claim together']),
    R('DEN-FREQ', 'Frequency limits', 'Exams, cleanings, X-rays, fluoride, crowns, scaling and root planing', 'amb', 'Most plans pay two exams and two cleanings a year, bitewings once a year, a full-mouth or panoramic series every 3 years, a crown on a tooth every 5 years and scaling and root planing per quadrant every 2 years.', ['Checks this patient’s other claims in Althais']),
    R('DEN-AGE', 'Age limits', 'Fluoride, sealants, child cleaning', 'amb', 'Fluoride and sealants are often covered only for children and teens; the child cleaning code is for children.', ['Uses the patient’s date of birth and the date of service']),
    R('DEN-ATTACH', 'X-rays and narratives', 'Crowns, buildups, implants, bridges, root canals, scaling and root planing, surgical extractions', 'amb', 'Plans pend these without the X-ray, periodontal chart or narrative they review. Mark the claim when they are attached.', ['Flags the claim until the attachment is marked sent']),
    R('DEN-TF', 'Timely filing', 'All claims', 'amb', 'TX Medicaid needs claims within 95 days of the date of service; most dental plans within 12 months.', ['Flags unsent claims near the deadline']),
  ];
  // finding code -> the rule card it belongs to, the short label on the claim, and the level
  X.GROUP = { no_tooth: 'TOOTH', bad_tooth: 'TOOTH', no_surface: 'SURF', bad_surface: 'SURF', surface_count: 'SURF', no_quad: 'QUAD', no_arch: 'QUAD', tooth_type: 'TOOTHTYPE',
    same_tooth: 'SAMETOOTH', dup_line: 'SAMETOOTH', two_exams: 'SAMEDAY', clean_and_maint: 'SAMEDAY', clean_and_srp: 'SAMEDAY', fmx_and_bw: 'SAMEDAY', age_limit: 'AGE', frequency: 'FREQ', attach: 'ATTACH', bad_cdt: 'CODE' };
  X.LABEL = { no_tooth: 'Tooth number', bad_tooth: 'Tooth number', no_surface: 'Surfaces', bad_surface: 'Surfaces', surface_count: 'Surface count', no_quad: 'Quadrant', no_arch: 'Arch',
    tooth_type: 'Code vs tooth', same_tooth: 'Same tooth', dup_line: 'Duplicate line', two_exams: 'Two exams', clean_and_maint: 'Same-day cleaning', clean_and_srp: 'Same-day cleaning',
    fmx_and_bw: 'X-rays bundled', age_limit: 'Age limit', frequency: 'Frequency limit', attach: 'Attachment', bad_cdt: 'Not a CDT code' };

  // "D2392 #14 MO; D4341 UR; D0120" -> lines [{code, tooth, surface, quad, units}]
  X.parseProcs = function (text) {
    return String(text || '').split(/[;\n]+|,(?=\s*D\d{4})/i).map(function (part) {
      var t = part.trim().toUpperCase(); if (!t) return null;
      var m = /^(D\d{4})\b(.*)$/.exec(t.replace(/\s+/g, ' ')); if (!m) return { code: t.split(/\s+/)[0], tooth: '', surface: '', quad: '', units: 1 };
      var rest = m[2].trim(), l = { code: m[1], tooth: '', surface: '', quad: '', units: 1 };
      rest.split(/[\s,]+/).filter(Boolean).forEach(function (w) {
        w = w.replace(/^#/, '');
        if (/^(UR|UL|LR|LL|UA|LA)$/.test(w)) l.quad = w;
        else if (!l.tooth && (/^\d{1,2}$/.test(w) || /^[A-T]S?$/.test(w) && toothKind(w))) l.tooth = w;
        else if (/^[MODBLIF]{1,5}$/.test(w)) l.surface = normSurface(w);
      });
      return l;
    }).filter(Boolean);
  };
  // one line as text: "D2392 #14 MO"
  X.lineText = function (l) { return l.code + (l.tooth ? ' #' + l.tooth : '') + (l.surface ? ' ' + l.surface : '') + (l.quad ? ' ' + l.quad : ''); };

  window.DX = X;
})();
