/* Althais v4, the dental EMR, part 2: the patient's Chart (V.pages.dchart). One page, tabbed the way a dental office works:
   Chart (tooth chart and procedure list), Perio (six-point charting by keyboard or voice), Treatment plans (phases, insurance
   estimates, pre-auths, presenting to the patient), Imaging (mount, viewer, AI findings from an FDA-cleared vendor),
   Notes & coding (ambient drafting, CDT suggestions to approve, posting procedures and the claim), Insurance (eligibility and
   benefits), Account (ledger, payments, text-to-pay, payment plans, membership), Rx & labs, Forms & history.
   Server: /api/dental/patients/<mrn> and friends (dental_api.py). */
(function () {
  if (!window.V || !window.DA) return;
  var E = U.esc, M = U.money, M0 = U.money0, TC = DA.teeth, PC = DA.perio;
  var NAMES = { 1: 'Upper right third molar', 2: 'Upper right second molar', 3: 'Upper right first molar', 4: 'Upper right second premolar', 5: 'Upper right first premolar', 6: 'Upper right canine', 7: 'Upper right lateral incisor', 8: 'Upper right central incisor',
    9: 'Upper left central incisor', 10: 'Upper left lateral incisor', 11: 'Upper left canine', 12: 'Upper left first premolar', 13: 'Upper left second premolar', 14: 'Upper left first molar', 15: 'Upper left second molar', 16: 'Upper left third molar',
    17: 'Lower left third molar', 18: 'Lower left second molar', 19: 'Lower left first molar', 20: 'Lower left second premolar', 21: 'Lower left first premolar', 22: 'Lower left canine', 23: 'Lower left lateral incisor', 24: 'Lower left central incisor',
    25: 'Lower right central incisor', 26: 'Lower right lateral incisor', 27: 'Lower right canine', 28: 'Lower right first premolar', 29: 'Lower right second premolar', 30: 'Lower right first molar', 31: 'Lower right second molar', 32: 'Lower right third molar' };
  var COND_LABEL = { caries: 'Caries', composite: 'Composite', amalgam: 'Amalgam', crown: 'Crown', missing: 'Missing', extracted: 'Extraction', implant: 'Implant', implant_crown: 'Implant crown', rct: 'Root canal', fracture: 'Fracture', sealant: 'Sealant', watch: 'Watch',
    bridge: 'Bridge', veneer: 'Veneer', periapical: 'Periapical lesion', impacted: 'Impacted', inlay: 'Inlay', onlay: 'Onlay', post_core: 'Buildup / post', denture: 'Denture', abrasion: 'Abrasion', recession: 'Recession', bone_loss: 'Bone loss' };
  var NOTE_TPL = {
    'Recall exam': 'S: Here for recall. No complaints.\nO: Periodic exam. Soft tissue within normal limits; oral cancer screening negative. Light plaque and calculus. Four bitewings taken: no new caries.\nTreatment: Adult prophylaxis completed. OHI given.\nP: Recall 6 months.',
    'Restorative': 'S: \nO: Caries on #__ __.\nAnesthesia: 1 carpule 2% lidocaine 1:100k epi, infiltration.\nTreatment: #__ __ composite restoration placed, shade A2. Occlusion checked.\nP: ',
    'Scaling and root planing': 'O: Probing depths 4-6 mm with bleeding on probing, __ quadrant. Radiographic bone loss.\nAnesthesia: \nTreatment: Scaling and root planing, __ quadrant. OHI given.\nP: Re-evaluate in 4-6 weeks; SRP remaining quadrants.',
    'Extraction': 'S: \nO: #__ non-restorable.\nAnesthesia: \nTreatment: Simple extraction #__. Hemostasis achieved. Post-op instructions given verbally and in writing.\nP: ',
    'Emergency': 'S: Chief complaint: \nO: Limited exam. #__ \nA: \nTreatment: \nP: ',
  };

  V.pages.dchart = function () {
    var app = V.app, q = V.q, mrn = q.get('mrn'), tab = q.get('tab') || 'chart', X = null, sel = [], picks = [], mode = 'plan', activePlan = 0;
    var perio = { exam: null, readings: null, prev: null, pos: 0, dirty: false }, ben = null, rec = null, me = window.__ALTHAIS_USER__ || {};
    var visit = { lines: [], note: '', noteId: null, transcript: '', sugs: [], appt: +q.get('appt') || 0 };
    if (!mrn) {
      app.innerHTML = DA.frame('dchart', 'Chart') + '<div class="main" style="grid-template-columns:minmax(0,1fr)">' + U.pane('Choose a patient', '', '', '<div class="emptyst"><b>Open a chart</b><button class="btn pri" id="pick">Find a patient</button></div>') + '</div>';
      U.fit(); U.search();
      U.on(app, '#pick', 'click', function () { DA.pickPatient('Open chart', function (m) { location.href = '/v4/dental/chart?mrn=' + encodeURIComponent(m); }); });
      DA.pickPatient('Open chart', function (m) { location.href = '/v4/dental/chart?mrn=' + encodeURIComponent(m); });
      return;
    }
    if (V.seen) V.seen('patient', mrn);
    var P = function (path) { return '/patients/' + encodeURIComponent(mrn) + (path || ''); };
    function load() {
      return Promise.all([DA.loadSetup(), DA.get(P())]).then(function (r) { X = r[1]; if (!activePlan) { var op = X.plans.filter(function (p) { return ['draft', 'presented', 'accepted', 'partial'].indexOf(p.status) >= 0; })[0]; activePlan = op ? op.id : 0; } draw(); },
        function (e) { app.innerHTML = DA.frame('dchart', 'Chart') + DA.blank('<b>' + (e.status === 403 ? 'You don’t have access to the clinical chart' : 'Couldn’t open this chart') + '</b>' + E(e.message)); U.fit(); });
    }
    var cov = function () { return (X.coverage || []).filter(function (c) { return c.rank === 'primary'; })[0]; };
    var dentists = function () { return DA.setup.providers.filter(function (p) { return p.kind === 'dentist'; }); };
    var myProv = function () { var p = DA.setup.providers.filter(function (x) { return x.user_email && x.user_email === me.email; })[0]; return p || dentists()[0] || DA.setup.providers[0] || {}; };
    function banner() {
      var p = X.patient, c = cov(), b = c && c.benefits, alerts = p.medical_alerts || [], al = String(p.allergies || '').split(/;|\n/).map(function (x) { return x.trim(); }).filter(function (x) { return x && !/^nkda$/i.test(x); });
      var next = (X.appointments || []).filter(function (a) { return a.start > new Date().toISOString().slice(0, 16) && ['scheduled', 'confirmed'].indexOf(a.status) >= 0; }).sort(function (a, b) { return a.start.localeCompare(b.start); })[0];
      var chips = [];
      chips.push(al.length ? '<span class="chip red">' + U.err + 'Allergy: ' + E(al.join(', ')) + '</span>' : '<span class="chip">No known allergies</span>');
      alerts.forEach(function (a) { chips.push('<span class="chip amb">⚕ ' + E(a) + '</span>'); });
      if (p.mh_review_pending) chips.push('<span class="chip amb">Medical history updated by patient ' + DA.short(p.mh_updated) + ': review</span>');
      if (c && c.payer && c.payer !== 'Self-pay') chips.push(c.active ? '<span class="chip grn"><span class="dot"></span>' + E(c.payer) + (b && b.max_remaining != null ? ' · ' + M0(b.max_remaining) + ' left' : '') + '</span>' : c.checked_at ? '<span class="chip red">' + E(c.payer) + ': not active</span>' : '<span class="chip amb">' + E(c.payer) + ': benefits not verified</span>');
      else chips.push(X.membership ? '<span class="chip blue">' + E(X.membership.plan.name) + '</span>' : '<span class="chip">No insurance</span>');
      if (X.balance > 0.005) chips.push('<span class="chip red">Balance ' + M(X.balance) + '</span>');
      chips.push(X.recall.overdue_days > 0 ? '<span class="chip amb">Recall ' + X.recall.overdue_days + ' days overdue</span>' : '<span class="chip">Recall ' + DA.short(X.recall.due) + '</span>');
      if (next) chips.push('<span class="chip blue">Next: ' + DA.short(next.start) + ' ' + DA.time(next.start) + '</span>');
      return '<div class="dban"><span class="av">' + E(U.initials2(DA.nice(p.name))) + '</span><div><div class="nm">' + E(p.name) + '</div><div class="mt">' + E(p.sex || '') + ', ' + X.age + ' · DOB ' + DA.us(p.dob) + ' · MRN <span class="mono">' + E(mrn) + '</span>' + (p.phone ? ' · ' + E(p.phone) : '') + '</div></div>' +
        '<div class="chips">' + chips.join('') + '</div><div class="end"><button class="btn" id="b-book">Book</button><button class="btn" id="b-text">Text</button><button class="btn" id="b-pay">Collect</button><button class="btn" id="b-switch">Switch patient</button></div></div>';
    }
    function tabs() {
      var open = X.plans.reduce(function (n, p) { return n + p.items.filter(function (i) { return ['proposed', 'accepted', 'scheduled'].indexOf(i.status) >= 0; }).length; }, 0);
      var T = [['chart', 'Chart'], ['perio', 'Perio', X.perio.length || ''], ['plans', 'Treatment plans', open || ''], ['imaging', 'Imaging', X.images.length || ''], ['notes', 'Notes & coding', X.suggestions.length ? X.suggestions.length + ' to review' : ''], ['insurance', 'Insurance'], ['account', 'Account'], ['rx', 'Rx & labs'], ['forms', 'Forms & history']];
      return '<div class="dtabs">' + T.map(function (t) { return '<button data-tab="' + t[0] + '" class="' + (t[0] === tab ? 'on' : '') + '">' + t[1] + (t[2] ? '<span class="n">' + t[2] + '</span>' : '') + '</button>'; }).join('') + '</div>';
    }
    function draw() {
      var body = { chart: chartTab, perio: perioTab, plans: plansTab, imaging: imagingTab, notes: notesTab, insurance: insuranceTab, account: accountTab, rx: rxTab, forms: formsTab }[tab]();
      app.innerHTML = U.header('dchart') + banner() + tabs() + body;
      U.fit(); U.search();
      if (tab === 'insurance' && !ben && cov()) DA.get(P('/benefits')).then(function (b) { ben = b; if (tab === 'insurance') draw(); });
      if (tab === 'perio' && !perio.readings) openExam(X.perio[0] ? X.perio[0].id : null);
      if (tab === 'imaging') hydrateThumbs();
    }

    /* ---------------- Chart tab ---------------- */
    function rowsList() {
      var rows = [], provName = function (id) { return (X.providers[id] || {}).name || ''; };
      X.plans.forEach(function (p) { p.items.forEach(function (i) { if (i.status === 'completed') return; rows.push({ k: 'tp', id: i.id, date: '', tooth: i.tooth, surf: i.surface || i.quad, code: i.code, st: i.status === 'declined' ? 'Declined' : 'TP', prov: provName(p.provider_id), fee: i.fee, cls: i.status === 'declined' ? 'mut' : 's-red' }); }); });
      X.procedures.forEach(function (p) { rows.push({ k: 'c', date: p.date, tooth: p.tooth, surf: p.surface || p.quad, code: p.code, st: p.source === 'import' ? 'C (imported)' : 'C', prov: provName(p.provider_id), fee: p.fee, cls: 's-blue' }); });
      X.conditions.filter(function (c) { return c.status === 'existing' || c.status === 'watch'; }).forEach(function (c) { rows.push({ k: 'e', id: c.id, date: c.date, tooth: c.tooth, surf: c.surface, code: '', desc: COND_LABEL[c.condition] || c.condition, st: TC.FINDINGS[c.condition] ? 'Cond' : 'EO', prov: '', fee: null, cls: 'mut' }); });
      if (sel.length) rows = rows.filter(function (r) { return sel.indexOf(+r.tooth) >= 0; });
      return rows.sort(function (a, b) { return (a.k === 'tp' ? '9' : a.date || '').localeCompare(b.k === 'tp' ? '9' : b.date || '') * -1; });
    }
    function chartTab() {
      var plans = X.plans.filter(function (p) { return ['draft', 'presented', 'accepted', 'partial'].indexOf(p.status) >= 0; });
      var selTxt = sel.length ? sel.map(function (t) { return '#' + t; }).join(', ') + (picks.length && sel.length === 1 ? ' ' + picks.join('') : '') : 'Click a tooth (shift-click for more), then a surface';
      var bar = '<div class="chartbar"><span class="grp"><button data-mode="existing" class="exist' + (mode === 'existing' ? ' on' : '') + '">Existing</button><button data-mode="plan" class="plan' + (mode === 'plan' ? ' on' : '') + '">Treatment plan</button><button data-mode="complete" class="done' + (mode === 'complete' ? ' on' : '') + '">Complete today</button></span>' +
        (mode === 'plan' ? '<select class="mini" id="actplan" style="height:25px">' + plans.map(function (p) { return '<option value="' + p.id + '"' + (p.id === activePlan ? ' selected' : '') + '>' + E(p.name) + '</option>'; }).join('') + '<option value="0"' + (!activePlan ? ' selected' : '') + '>+ New plan</option></select>' : '') +
        '<span class="tb-sep"></span>' + TC.TOOLS.map(function (t) { return '<button class="cbtn" data-tool="' + t[0] + '" title="' + (mode === 'existing' || TC.FINDINGS[t[0]] ? 'Chart ' : mode === 'plan' ? 'Plan ' : 'Complete ') + t[1].toLowerCase() + '">' + t[1] + '</button>'; }).join('') +
        '<span class="sel-info">' + E(selTxt) + (sel.length ? ' <a href="#" id="clrsel">clear</a>' : '') + '</span></div>';
      var rows = rowsList();
      var list = U.grid(['Date', 'Tooth', 'Surf / quad', 'Code', 'Description', 'Status', 'Provider', ['Fee', 'num'], ''], rows.map(function (r) {
        return { cells: [r.date ? DA.us(r.date) : '', r.tooth ? '#' + E(r.tooth) : '', E(r.surf || ''), '<span class="mono">' + E(r.code) + '</span>', E(r.desc || DA.desc(r.code)), '<b class="' + r.cls + '">' + r.st + '</b>', E(r.prov), r.fee == null ? '' : M(r.fee),
          r.k === 'tp' && r.st === 'TP' ? '<button class="mini" data-done-item="' + r.id + '">Complete</button> <button class="mini" data-rm-item="' + r.id + '">' + U.I.x + '</button>' : r.k === 'e' ? '<button class="mini" data-rm-cond="' + r.id + '">' + U.I.x + '</button>' : ''] };
      }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(9, 'Nothing charted' + (sel.length ? ' on this tooth' : '') + ' yet.') + '</tbody>');
      return '<div class="main" style="grid-template-columns:minmax(0,1fr) 300px;height:calc(100vh - var(--chrome, 82px))">' +
        '<section class="pane">' + bar + '<div class="pbody"><div class="odo-wrap">' + TC.svg(X.conditions, sel, picks) + '</div>' + TC.legend + U.sect('Procedures and conditions', sel.length ? 'on ' + sel.map(function (t) { return '#' + t; }).join(', ') : rows.length + '', list) + '</div></section>' +
        toothPane() + '</div>';
    }
    function toothPane() {
      if (sel.length !== 1) {
        var miss = X.conditions.filter(function (c) { return c.condition === 'missing' && c.status === 'existing'; }).map(function (c) { return c.tooth; });
        var watch = X.conditions.filter(function (c) { return c.condition === 'watch' || c.condition === 'caries'; });
        return U.pane('Chart summary', '', '', '<div class="dp-sec">' + U.kv([['Missing teeth', miss.length ? miss.map(function (t) { return '#' + t; }).join(', ') : 'None'], ['Open findings', watch.length ? watch.map(function (c) { return '#' + c.tooth + ' ' + (COND_LABEL[c.condition] || '').toLowerCase(); }).join(', ') : 'None'], ['Last perio chart', X.perio[0] ? DA.us(X.perio[0].date) + ' · ' + (X.perio[0].summary || {}).sites_4 + ' sites 4mm+' : 'None on file'], ['Last images', X.images[0] ? DA.us(X.images[0].taken) + ' ' + X.images[0].kind : 'None']]) + '</div>' +
          '<div class="dp-sec"><div class="dp-h">How to chart</div><div class="mut" style="font-size:12px;line-height:1.55">1. Pick the mode: <b style="color:var(--tx-exist)">Existing</b> work, <b style="color:var(--tx-plan)">Treatment plan</b>, or <b style="color:var(--tx-done)">Complete today</b>.<br>2. Click a tooth, then its surfaces in the box under it.<br>3. Click what you found or did. The CDT code is picked from the tooth and surfaces.</div></div>');
      }
      var t = sel[0], cs = X.conditions.filter(function (c) { return +c.tooth === t; }), done = X.procedures.filter(function (p) { return +p.tooth === t; }), imgs = X.images.filter(function (i) { return (i.teeth || []).indexOf(String(t)) >= 0; });
      return U.pane('#' + t, NAMES[t] || '', '', '<div class="dp-sec"><div class="dp-h">On this tooth</div>' + (cs.length ? cs.map(function (c) { return '<div style="display:flex;align-items:center;gap:6px;padding:3px 0"><span style="width:9px;height:9px;border-radius:2px;background:' + TC.color(c) + '"></span>' + E(COND_LABEL[c.condition] || c.condition) + (c.surface ? ' <span class="mono">' + E(c.surface) + '</span>' : '') + ' <span class="mut">' + E(c.status) + (c.code ? ' · ' + E(c.code) : '') + '</span><button class="mini" style="margin-left:auto" data-rm-cond="' + c.id + '">' + U.I.x + '</button></div>'; }).join('') : '<span class="mut">Nothing charted</span>') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">History</div>' + (done.length ? U.kv(done.map(function (p) { return [DA.us(p.date), '<span class="mono">' + E(p.code) + '</span> ' + E(p.surface || '') + ' ' + E(DA.desc(p.code))]; })) : '<span class="mut">No procedures on file</span>') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Images</div>' + (imgs.length ? imgs.slice(0, 4).map(function (i) { return '<a href="#" data-openimg="' + i.id + '">' + E(i.kind) + ' ' + DA.us(i.taken) + '</a>'; }).join('<br>') : '<span class="mut">None</span>') + '</div>');
    }
    function chartTool(tool) {
      if (!sel.length) return U.toast('Click a tooth first.', 'warn');
      var needSurf = /^(composite|amalgam|caries)$/.test(tool);
      if (needSurf && sel.length === 1 && !picks.length) return U.toast('Click the surfaces in the box under the tooth first.', 'warn');
      var surf = sel.length === 1 ? picks.join('') : '';
      var finding = TC.FINDINGS[tool] || mode === 'existing';
      var jobs;
      if (finding) jobs = sel.map(function (t) { return DA.post(P('/conditions'), { tooth: t, surface: needSurf ? surf || 'O' : '', condition: tool, status: 'existing' }); });
      else {
        var lines = sel.map(function (t) { return { tooth: String(t), surface: needSurf || tool === 'sealant' ? surf : '', code: TC.code(tool, t, surf) }; }).filter(function (l) { return l.code; });
        if (!lines.length) return U.toast('That isn’t a procedure to plan; charting it as a finding.', 'info');
        if (mode === 'plan') jobs = [activePlan ? DA.post('/plans/' + activePlan + '/items', { items: lines }) : DA.post(P('/plans'), { name: 'Treatment plan', provider_id: myProv().id, items: lines }).then(function (r) { activePlan = r.id; })];
        else jobs = [DA.post(P('/procedures'), { lines: lines, provider_id: myProv().id, date: DA.today(), claim: !!(cov() && cov().payer) })];
      }
      Promise.all(jobs).then(function () { U.toast(finding ? (COND_LABEL[tool] || tool) + ' charted.' : mode === 'plan' ? 'Added to the treatment plan.' : 'Completed and posted to the ledger' + (cov() && cov().payer ? '; claim created.' : '.'), 'ok'); picks = []; load(); }, DA.fail);
    }

    /* ---------------- Perio tab ---------------- */
    function openExam(id) {
      var blank = function () { var r = PC.empty(); X.conditions.forEach(function (c) { if ((c.condition === 'missing' || c.condition === 'extracted') && c.status !== 'planned' && r[c.tooth]) r[c.tooth].missing = true; }); return r; };
      var prevId = X.perio.filter(function (p) { return p.id !== id; })[0];
      var getPrev = prevId ? DA.get('/perio/' + prevId.id).then(function (x) { return x.readings; }, function () { return null; }) : Promise.resolve(null);
      var getCur = id ? DA.get('/perio/' + id) : Promise.resolve(null);
      Promise.all([getCur, getPrev]).then(function (r) {
        perio.exam = r[0]; perio.readings = r[0] && r[0].readings ? r[0].readings : blank(); perio.prev = id ? r[1] : (X.perio[0] ? null : null); perio.pos = 0; perio.dirty = false;
        if (!id && X.perio[0]) DA.get('/perio/' + X.perio[0].id).then(function (x) { perio.prev = x.readings; if (tab === 'perio') draw(); });
        skipMissing(); if (tab === 'perio') draw();
      }, DA.fail);
    }
    function skipMissing() { while (perio.pos < PC.PATH.length && perio.readings[PC.PATH[perio.pos][0]].missing) perio.pos++; }
    var buf = [];
    function perioTab() {
      if (!perio.readings) return '<div class="main" style="grid-template-columns:1fr">' + U.pane('Perio', '', '', DA.blank('Loading…')) + '</div>';
      var s = PC.summary(perio.readings), cur = PC.PATH[perio.pos], ex = perio.exam;
      var stage = ex && ex.summary ? ex.summary.stage_suggested : '';
      var srp = ex && ex.summary && ex.summary.srp ? ex.summary.srp : [];
      var exams = X.perio.map(function (p) { return '<option value="' + p.id + '"' + (ex && ex.id === p.id ? ' selected' : '') + '>' + DA.us(p.date) + (p.source === 'voice' ? ' (voice)' : '') + '</option>'; }).join('');
      return '<div class="main" style="grid-template-columns:minmax(0,1fr);height:calc(100vh - var(--chrome, 82px))"><section class="pane">' +
        '<div class="chartbar"><b>Perio chart</b><select class="mini" id="pexam" style="height:25px">' + (ex ? '' : '<option selected>New exam, ' + DA.us(DA.today()) + '</option>') + exams + '</select><button class="cbtn" id="pnew">New exam</button><button class="cbtn" id="psave" style="' + (perio.dirty ? 'background:var(--blue);color:#fff;border-color:var(--blue)' : '') + '">' + U.I.save + 'Save exam</button>' +
          '<span class="tb-sep"></span><span class="mut" style="font-size:12px">Type three depths per tooth side; it moves on by itself. Click a bleeding cell to mark it.</span>' + (perio.prev ? '<span class="sel-info">Hover a depth to see the last exam</span>' : '') + '</div>' +
        '<div class="voice"><button class="mic' + (rec ? ' on' : '') + '" id="pmic" title="Dictate">' + U.I.mic + '</button><span>Next: <span class="pos">' + (cur ? '#' + cur[0] + ' ' + (cur[1] === 'F' ? 'facial' : 'lingual') : 'done') + '</span></span>' +
          '<input class="mini" id="pcmd" placeholder="Or type what you’d say: 3 2 3, 4 3 5 bleeding, missing, mobility 1, tooth 14" style="flex:1;min-width:260px;height:26px"><span class="heard" id="pheard">' + (rec ? 'Listening…' : (window.SpeechRecognition || window.webkitSpeechRecognition ? 'Hands-free: click the mic and say the numbers' : 'This browser has no speech recognition; type the commands instead')) + '</span></div>' +
        '<div class="perio-summ"><div><span>Sites 4 mm+</span><b class="' + (s.o4 ? 's-amb' : '') + '">' + s.o4 + '</b></div><div><span>Sites 5 mm+</span><b class="' + (s.o5 ? 's-red' : '') + '">' + s.o5 + '</b></div><div><span>Sites 6 mm+</span><b class="' + (s.o6 ? 's-red' : '') + '">' + s.o6 + '</b></div><div><span>Bleeding on probing</span><b class="' + (s.bop >= 30 ? 's-red' : s.bop >= 10 ? 's-amb' : '') + '">' + s.bop + '%</b></div>' +
          '<div><span>Suggested (dentist confirms)</span><b style="font-size:13px">' + E(stage || (s.sites ? 'Save to see' : '')) + '</b>' + (srp.length ? '<div style="margin-top:3px"><button class="mini" id="psrp">Plan ' + srp.map(function (x) { return x.quad + ' ' + x.code; }).join(', ') + '</button></div>' : '') + '</div></div>' +
        '<div class="pbody"><div class="perio" id="ptab">' + PC.table(perio.readings, perio.prev, perio.pos) + '</div></div></section></div>';
    }
    function perioEnter(d) {
      var cur = PC.PATH[perio.pos]; if (!cur) return;
      buf.push(d);
      var idx = PC.idx(cur[1]), v = perio.readings[cur[0]];
      v.pd[idx[buf.length - 1]] = d; perio.dirty = true;
      if (buf.length === 3) { buf = []; perio.pos++; skipMissing(); }
      redrawPerio();
    }
    function redrawPerio() { var t = document.getElementById('ptab'); if (!t) return draw(); var sc = t.parentElement.scrollTop; draw(); var n = document.getElementById('ptab'); if (n) n.parentElement.scrollTop = sc; }
    function voiceText(text) {
      DA.post('/perio/voice', { text: text, readings: perio.readings, position: perio.pos }).then(function (r) {
        perio.readings = r.readings; perio.pos = r.position; perio.dirty = true; perio.source = 'voice'; buf = [];
        redrawPerio(); var h = document.getElementById('pheard'); if (h) h.textContent = r.log.slice(-4).join(' · ') || ('Heard: ' + text);
      }, DA.fail);
    }
    function toggleMic() {
      var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) return U.toast('Speech recognition isn’t available in this browser. Type the commands in the box instead.', 'warn');
      if (rec) { rec.stop(); rec = null; draw(); return; }
      rec = new SR(); rec.continuous = true; rec.interimResults = false; rec.lang = 'en-US';
      rec.onresult = function (e) { var t = ''; for (var i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) t += e.results[i][0].transcript + ' '; if (t.trim()) voiceText(t); };
      rec.onend = function () { if (rec) try { rec.start(); } catch (x) { rec = null; draw(); } };
      rec.onerror = function (e) { if (e.error === 'not-allowed') { rec = null; U.toast('The microphone is blocked for this site.', 'warn'); draw(); } };
      rec.start(); draw();
    }

    /* ---------------- Treatment plans ---------------- */
    function plansTab() {
      var c = cov(), b = c && c.benefits;
      var benBar = b && c.active ? '<div class="dp-sec" style="display:flex;gap:18px;align-items:center;background:var(--surface)"><div style="flex:1;max-width:420px"><div style="display:flex;justify-content:space-between;font-size:12px"><span>' + E(c.payer) + ' annual maximum</span><b>' + M0(b.max_remaining) + ' of ' + M0(b.annual_max) + ' left</b></div><div class="meter' + (b.max_remaining < b.annual_max * .25 ? ' bad' : '') + '"><i style="width:' + Math.round(100 * (b.max_remaining || 0) / (b.annual_max || 1)) + '%"></i></div></div><div class="mut" style="font-size:12px">Deductible left ' + M0(b.deductible_remaining || 0) + ' · Basic ' + ((b.coinsurance || {}).basic || 0) + '% · Major ' + ((b.coinsurance || {}).major || 0) + '%</div></div>' : '';
      var cards = X.plans.slice().sort(function (a, b) { return (b.created || '').localeCompare(a.created || ''); }).map(function (p) {
        var items = p.items, phases = {}; items.forEach(function (i) { (phases[i.phase] = phases[i.phase] || []).push(i); });
        var open = items.filter(function (i) { return i.status !== 'completed' && i.status !== 'declined'; });
        var tot = function (k) { return open.reduce(function (s, i) { return s + (k === 'fee' ? i.fee : k === 'ins' ? i.est_insurance : i.est_patient); }, 0); };
        var body = '';
        Object.keys(phases).sort().forEach(function (ph) {
          var its = phases[ph], ptot = its.filter(function (i) { return i.status !== 'declined' && i.status !== 'completed'; }).reduce(function (s, i) { return s + i.est_patient; }, 0);
          body += '<tr class="phase-h"><td colspan="11">PHASE ' + ph + ' · patient portion ' + M(ptot) + '</td></tr>' + its.map(function (i) {
            var pa = X.preauths.filter(function (x) { return x.id === i.preauth_id; })[0];
            return '<tr><td class="cb"><input type="checkbox" data-pick-item="' + i.id + '"' + (i.status === 'completed' || i.status === 'declined' ? ' disabled' : '') + '></td><td><span class="prio ' + i.priority + '">' + E(i.priority) + '</span></td><td>' + (i.tooth ? '#' + E(i.tooth) : E(i.quad || '')) + (i.surface ? ' <span class="mono">' + E(i.surface) + '</span>' : '') + '</td><td class="mono">' + E(i.code) + '</td><td class="wrap">' + E(DA.desc(i.code)) + '</td><td class="num">' + M(i.fee) + '</td><td class="num">' + M(i.est_insurance) + '</td><td class="num"><b>' + M(i.est_patient) + '</b></td><td class="wrap mut" style="font-size:11.5px;max-width:180px">' + E(i.est_note || '') + '</td><td>' + DA.statusChip(i.status) + (pa ? ' <span title="Pre-auth ' + E(pa.ref || '') + '">Pre-auth ' + DA.statusChip(pa.status) + '</span>' : '') + '</td>' +
              '<td>' + (i.status !== 'completed' && i.status !== 'declined' ? '<button class="mini" data-done-item="' + i.id + '">Complete</button> <button class="mini" data-rm-item="' + i.id + '">' + U.I.x + '</button>' : '') + '</td></tr>';
          }).join('');
        });
        return '<div class="txp"><div class="txp-h"><span class="t">' + E(p.name) + '</span>' + DA.statusChip(p.status) + (p.signed ? '<span class="chip grn">Signed</span>' : '') + '<span class="mut" style="font-size:12px">' + E((X.providers[p.provider_id] || {}).name || '') + (p.presented_at ? ' · presented ' + DA.us(p.presented_at) : '') + '</span>' +
          '<span class="end"><button class="mini" data-plan-est="' + p.id + '">Re-estimate</button><button class="mini" data-plan-pa="' + p.id + '">Request pre-auth</button><button class="mini" data-plan-book="' + p.id + '">Schedule</button><button class="mini pri" data-plan-present="' + p.id + '">Present to patient</button></span></div>' +
          '<table class="g tight"><thead><tr><th class="cb"></th><th>Priority</th><th>Tooth</th><th>Code</th><th>Procedure</th><th class="num">Fee</th><th class="num">Insurance est.</th><th class="num">Patient est.</th><th>Note</th><th>Status</th><th></th></tr></thead><tbody>' + (body || U.empty(11, 'No procedures yet. Chart them on the Chart tab in Treatment plan mode.')) + '</tbody></table>' +
          '<div class="txp-tot"><div><span>Total fees</span><b>' + M(tot('fee')) + '</b></div><div><span>Insurance estimate</span><b class="s-grn">' + M(tot('ins')) + '</b></div><div><span>Patient estimate</span><b>' + M(tot('pt')) + '</b></div></div></div>';
      }).join('');
      return '<div class="main" style="grid-template-columns:minmax(0,1fr);height:calc(100vh - var(--chrome, 82px))"><section class="pane"><div class="chartbar"><b>Treatment plans</b><button class="cbtn" id="newplan">' + U.I.plus + 'New plan</button><span class="mut" style="font-size:12px">Add procedures from the Chart tab. Estimates use this patient’s verified benefits' + (X.membership ? ' and membership' : '') + '.</span></div>' + benBar +
        '<div class="pbody">' + (cards || DA.blank('<b>No treatment plans</b>Chart findings in Treatment plan mode to build one.')) + '</div></section></div>';
    }
    function bookItems(planId) {
      var p = X.plans.filter(function (x) { return x.id === planId; })[0], ids = [].slice.call(app.querySelectorAll('[data-pick-item]:checked')).map(function (i) { return +i.dataset.pickItem; });
      var items = p.items.filter(function (i) { return ids.length ? ids.indexOf(i.id) >= 0 : (i.status === 'accepted' || i.status === 'proposed'); });
      if (!items.length) return U.toast('Tick the procedures to schedule.', 'warn');
      var minutes = Math.min(180, Math.max(30, items.reduce(function (s, i) { return s + ((DA.fees[i.code] || {}).minutes || 30); }, 0)));
      DA.get('/schedule').then(function (s) {
        U.form('Schedule ' + items.length + ' procedure' + (items.length === 1 ? '' : 's'), [{ k: 'date', label: 'Date', type: 'date', req: 1, def: DA.addDays(DA.today(), 7) }, { k: 'time', label: 'Time', type: 'time', step: 600, req: 1, def: '09:00' },
          { k: 'provider_id', label: 'Provider', type: 'select', options: DA.setup.providers.map(function (x) { return [x.id, x.name]; }), def: p.provider_id }, { k: 'operatory_id', label: 'Chair', type: 'select', options: s.operatories.map(function (o) { return [o.id, o.name]; }) },
          { k: 'minutes', label: 'Minutes', type: 'number', def: minutes }], {}, function (v) {
          DA.post('/appointments', { mrn: mrn, start: v.date + 'T' + v.time, provider_id: +v.provider_id, operatory_id: +v.operatory_id, minutes: +v.minutes, location_id: s.location ? s.location.id : null, reason: items.map(function (i) { return DA.desc(i.code).split(',')[0] + (i.tooth ? ' #' + i.tooth : ''); }).join(', '),
            procedures: items.map(function (i) { return { code: i.code, tooth: i.tooth, surface: i.surface, quad: i.quad }; }), plan_item_ids: items.map(function (i) { return i.id; }) })
            .then(function () { U.toast('Booked ' + DA.us(v.date) + ' at ' + v.time + '.', 'ok'); load(); }, DA.fail);
        }, { save: 'Book' });
      });
    }

    /* ---------------- Imaging ---------------- */
    var imgCache = {};
    function imagingTab() {
      var byDate = {};
      X.images.forEach(function (i) { (byDate[i.taken] = byDate[i.taken] || []).push(i); });
      var bw = X.images.filter(function (i) { return i.kind === 'bitewing'; }).slice(0, 4), pano = X.images.filter(function (i) { return i.kind === 'pano'; })[0], pas = X.images.filter(function (i) { return i.kind === 'pa'; }).slice(0, 4);
      var slot = function (i, w, h, label) { return '<div class="slot" style="width:' + w + 'px;height:' + h + 'px"' + (i ? ' data-openimg="' + i.id + '"' : '') + '>' + (i ? '<img data-img="' + i.id + '" alt="' + E(i.kind) + '">' + '<span class="lab">' + E(i.kind) + ' ' + DA.short(i.taken) + '</span>' + (i.ai && (i.ai.findings || []).length && i.ai_review !== 'dismissed' ? '<span class="aiflag">AI ' + i.ai.findings.length + '</span>' : '') : E(label)) + '</div>'; };
      var mount = '<div class="mount" style="grid-template-columns:repeat(4, 150px)">' + [0, 1, 2, 3].map(function (k) { return slot(pas[k], 150, 110, 'PA'); }).join('') +
        '<div style="grid-column:span 4;display:flex;gap:6px;justify-content:center">' + [0, 1, 2, 3].map(function (k) { return slot(bw[k], 150, 110, 'Bitewing'); }).join('') + '</div>' +
        '<div style="grid-column:span 4;display:flex;justify-content:center">' + slot(pano, 618, 200, 'Panoramic') + '</div></div>';
      var thumbs = Object.keys(byDate).sort().reverse().map(function (d) { return '<div class="sect-h" style="background:none;border:0">' + DA.us(d) + '</div><div class="thumbs">' + byDate[d].map(function (i) { return '<div class="th" data-openimg="' + i.id + '"><div class="im"><img data-img="' + i.id + '" alt=""></div><div class="cap"><span>' + E(i.kind) + (i.synthetic ? ' <span class="mockflag">SYN</span>' : '') + '</span><span class="mut">' + (i.ai_status === 'complete' ? 'AI ' + (i.ai.findings || []).length : '') + '</span></div></div>'; }).join('') + '</div>'; }).join('');
      return '<div class="main" style="grid-template-columns:minmax(0,1fr) 300px;height:calc(100vh - var(--chrome, 82px))"><section class="pane"><div class="chartbar"><b>Imaging</b><button class="cbtn" id="acquire">' + U.I.plus + 'Take images</button><button class="cbtn" id="iupload">' + U.I.upload + 'Upload</button><span class="mut" style="font-size:12px">Device bridges are mocks until each manufacturer’s bridge is installed; they draw synthetic images from the chart.</span></div><div class="pbody">' + mount + (thumbs || DA.blank('No images yet.')) + '</div></section>' +
        U.pane('AI radiograph review', '', '', '<div class="dp-sec mut" style="font-size:12px;line-height:1.5">Findings come from an FDA-cleared vendor (Overjet, Pearl or VideaHealth, once contracted). Althais doesn’t diagnose: the dentist accepts or dismisses each finding. Accepted caries are added to the chart.</div>' +
          X.images.filter(function (i) { return i.ai_status === 'complete'; }).map(function (i) { return '<div class="dp-sec"><div class="dp-h">' + E(i.kind) + ' ' + DA.us(i.taken) + ' <span class="end">' + (i.ai_review ? DA.statusChip(i.ai_review === 'accepted' ? 'approved' : 'declined') : '<a href="#" data-openimg="' + i.id + '">Review</a>') + '</span></div>' + ((i.ai.findings || []).map(function (f) { return '<div>#' + E(f.tooth) + ' ' + E(f.finding) + (f.surface ? ' ' + E(f.surface) : '') + ' <span class="mut">' + Math.round(f.confidence * 100) + '%</span></div>'; }).join('') || '<span class="mut">No findings</span>') + '</div>'; }).join('')) + '</div>';
    }
    function hydrateThumbs() {
      app.querySelectorAll('img[data-img]').forEach(function (im) {
        var id = +im.dataset.img;
        var set = function (x) { im.src = x.mime === 'image/svg+xml' && x.data.indexOf('<svg') === 0 ? 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(x.data) : x.data; };
        if (imgCache[id]) return set(imgCache[id]);
        DA.get('/images/' + id).then(function (x) { imgCache[id] = x; set(x); });
      });
    }
    function openImage(id) {
      var show = function (x) {
        var src = x.mime === 'image/svg+xml' && x.data.indexOf('<svg') === 0 ? 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(x.data) : x.data, ai = x.ai || {};
        var w = U.modal(x.kind + ' · ' + DA.us(x.taken) + (x.synthetic ? ' · synthetic' : ''), '<div class="viewer"><div class="stage"><img id="vimg" src="' + src + '" alt=""></div><div class="side">' +
          '<label>Brightness</label><input type="range" id="vb" min="40" max="200" value="100"><label>Contrast</label><input type="range" id="vc" min="40" max="250" value="100"><label>Zoom</label><input type="range" id="vz" min="100" max="300" value="100"><label class="chk" style="margin-top:8px;color:var(--t)"><input type="checkbox" id="vi"> Invert</label>' +
          '<div class="dp-h" style="margin-top:14px">AI findings</div>' + (x.ai_status === 'complete' ? '<div class="mut" style="font-size:11.5px">' + E(ai.vendor || '') + '</div>' + ((ai.findings || []).map(function (f) { return '<div class="find"><b>' + E(f.finding) + '</b> #' + E(f.tooth) + (f.surface ? ' ' + E(f.surface) : '') + '<span class="mut" style="float:right">' + Math.round(f.confidence * 100) + '%</span></div>'; }).join('') || '<div class="mut">No findings.</div>') +
            ((ai.bone_levels || []).length ? '<div class="mut" style="margin-top:6px;font-size:11.5px">Bone levels: ' + ai.bone_levels.map(function (b) { return '#' + b.tooth + ' ' + b.bone_level_mm + 'mm'; }).join(', ') + '</div>' : '') +
            (x.ai_review ? '<div style="margin-top:8px">' + DA.statusChip(x.ai_review === 'accepted' ? 'approved' : 'declined') + '</div>' : '<div style="display:flex;gap:6px;margin-top:8px"><button class="mini pri" id="aiacc">Accept findings</button><button class="mini" id="aidis">Dismiss</button></div>')
            : '<button class="mini" id="airun">Send to AI vendor</button>') + '</div></div>', [['Close', '', function () {}]], { wide: 1 });
        w.querySelector('.mdl').classList.add('xl');
        var upd = function () { var im = w.querySelector('#vimg'); im.style.filter = 'brightness(' + w.querySelector('#vb').value + '%) contrast(' + w.querySelector('#vc').value + '%)' + (w.querySelector('#vi').checked ? ' invert(1)' : ''); im.style.transform = 'scale(' + w.querySelector('#vz').value / 100 + ')'; };
        ['#vb', '#vc', '#vz', '#vi'].forEach(function (s) { w.querySelector(s).addEventListener('input', upd); });
        var act = function (sel, fn) { var b = w.querySelector(sel); if (b) b.onclick = fn; };
        act('#airun', function () { DA.post('/images/' + id + '/ai').then(function (r) { imgCache[id] = Object.assign(imgCache[id] || x, r); w.remove(); load().then(function () { openImage(id); }); }, DA.fail); });
        act('#aiacc', function () { DA.patch('/images/' + id, { ai_review: 'accepted' }).then(function () { U.toast('Findings accepted; caries added to the chart.', 'ok'); w.remove(); load(); }, DA.fail); });
        act('#aidis', function () { DA.patch('/images/' + id, { ai_review: 'dismissed' }).then(function () { w.remove(); load(); }, DA.fail); });
      };
      DA.get('/images/' + id).then(function (x) { imgCache[id] = x; show(x); }, DA.fail);
    }

    /* ---------------- Notes & coding ---------------- */
    function notesTab() {
      if (!visit.lines.length && visit.appt) {
        var ap = X.appointments.filter(function (a) { return a.id === visit.appt; })[0];
        if (ap) visit.lines = (ap.procedures || []).filter(function (p) { return p.code; }).map(function (p) { return { code: p.code, tooth: p.tooth || '', surface: p.surface || '', quad: p.quad || '' }; });
      }
      var sugs = X.suggestions;
      var notes = X.notes.map(function (n) { return '<div class="hrow" data-note="' + n.id + '"><div><div><b>' + DA.us(n.date) + '</b> ' + E(n.kind === 'medical_history' ? 'Medical history' : n.kind === 'ambient_draft' ? 'Draft from recording' : 'Progress note') + '</div><div class="s">' + E((n.text || '').split('\n')[0].slice(0, 70)) + '</div></div><div class="r">' + (n.status === 'signed' ? '<span class="s-grn">Signed</span>' : '<span class="s-amb">Draft</span>') + '</div></div>'; }).join('');
      var lineRows = visit.lines.map(function (l, k) { return '<tr><td><input class="mini mono" data-vl="' + k + '" data-f="code" value="' + E(l.code) + '" style="width:64px"></td><td><input class="mini" data-vl="' + k + '" data-f="tooth" value="' + E(l.tooth) + '" style="width:44px"></td><td><input class="mini" data-vl="' + k + '" data-f="surface" value="' + E(l.surface) + '" style="width:54px"></td><td><input class="mini" data-vl="' + k + '" data-f="quad" value="' + E(l.quad) + '" style="width:40px"></td><td class="wrap">' + E(DA.desc(l.code)) + '</td><td class="num">' + M(DA.fee(l.code)) + '</td><td><button class="mini" data-vl-rm="' + k + '">' + U.I.x + '</button></td></tr>'; }).join('');
      return '<div class="main" style="grid-template-columns:260px minmax(0,1fr) 340px;height:calc(100vh - var(--chrome, 82px))">' +
        U.pane('Notes', X.notes.length + '', '<a href="#" id="newnote">New</a>', notes || '<div class="emptyst">No notes yet.</div>') +
        '<section class="pane"><div class="chartbar"><b>Visit note</b><select class="mini" id="ntpl" style="height:25px"><option value="">Template…</option>' + Object.keys(NOTE_TPL).map(function (k) { return '<option>' + E(k) + '</option>'; }).join('') + '</select>' +
          '<button class="cbtn" id="nrec">' + U.I.mic + (rec && rec.ambient ? 'Stop recording' : 'Record visit') + '</button><button class="cbtn" id="ndraft">Draft note from recording</button><button class="cbtn" id="ncheck">Check coding</button>' +
          '<span class="sel-info">' + (visit.transcript ? visit.transcript.split(' ').length + ' words recorded' : 'Recording drafts the note; the dentist reviews and signs') + '</span></div>' +
          '<div class="pbody" style="display:flex;flex-direction:column"><textarea class="ed" id="ntext" style="flex:1;min-height:260px;border:0;border-bottom:1px solid var(--b);padding:10px 12px;resize:none;font-size:13px;line-height:1.55" placeholder="Findings by tooth and surface, anesthetic, what was done, next visit">' + E(visit.note) + '</textarea>' +
          U.sect('Procedures to post today', visit.lines.length + '', '<table class="g tight"><thead><tr><th>Code</th><th>Tooth</th><th>Surf</th><th>Quad</th><th>Procedure</th><th class="num">Fee</th><th></th></tr></thead><tbody>' + (lineRows || U.empty(7, 'Add procedures, or approve the coding suggestions.')) + '</tbody></table><div style="padding:8px 10px;display:flex;gap:6px"><button class="mini" id="vladd">' + U.I.plus + 'Add line</button><span style="margin-left:auto"></span><button class="btn" id="nsave">Save draft</button><button class="btn pri" id="nsign">Sign note and post procedures</button></div>') + '</div></section>' +
        U.pane('Coding review', sugs.length ? sugs.length + ' to review' : '', '', '<div class="dp-sec mut" style="font-size:12px">Compares the note with the procedures: what’s documented but not billed, and what’s billed lower than documented. Suggestions only; a dentist approves each one.</div><div class="dpad">' +
          (sugs.map(function (s) { return '<div class="sug ' + s.kind + '"><div><span class="c">' + E(s.code) + '</span>' + (s.tooth ? ' #' + E(s.tooth) : '') + (s.surface ? ' ' + E(s.surface) : '') + (s.quad ? ' ' + E(s.quad) : '') + ' <span class="chip ' + (s.kind === 'undercoded' ? 'red' : s.kind === 'missed' ? 'amb' : 'blue') + '">' + E(s.kind === 'missed' ? 'Missed' : s.kind === 'undercoded' ? 'Undercoded' : 'AI suggestion') + '</span></div><div>' + E(s.message) + '</div>' + (s.evidence ? '<div class="ev">“' + E(s.evidence) + '”</div>' : '') + '<div class="acts"><button class="mini pri" data-sug="' + s.id + '" data-d="approved">Approve</button><button class="mini" data-sug="' + s.id + '" data-d="rejected">Reject</button><span class="mut" style="margin-left:auto;font-size:11px">' + E(s.source) + '</span></div></div>'; }).join('') || '<div class="mut">Nothing to review. Write or record the note, then Check coding.</div>') + '</div>') + '</div>';
    }
    function noteText() { var t = document.getElementById('ntext'); if (t) visit.note = t.value; return visit.note; }
    function ambient() {
      var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (rec && rec.ambient) { rec.stop(); rec = null; draw(); return; }
      if (!SR) { U.toast('No speech recognition in this browser: using the mock transcriber (a sample visit).', 'info'); return draftNote(); }
      rec = new SR(); rec.ambient = true; rec.continuous = true; rec.interimResults = false; rec.lang = 'en-US';
      rec.onresult = function (e) { for (var i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) visit.transcript += e.results[i][0].transcript + '. '; var s = app.querySelector('.sel-info'); if (s) s.textContent = visit.transcript.split(' ').length + ' words recorded'; };
      rec.onend = function () { if (rec && rec.ambient) try { rec.start(); } catch (x) { rec = null; } };
      rec.start(); noteText(); draw();
    }
    function draftNote() {
      noteText();
      if (rec && rec.ambient) { rec.stop(); rec = null; }
      DA.post(P('/notes/ambient'), { transcript: visit.transcript || undefined, appt_id: visit.appt || undefined, provider_id: myProv().id }).then(function (d) {
        visit.note = d.note; visit.noteId = d.note_id; visit.transcript = d.transcript;
        var have = visit.lines.map(function (l) { return l.code + '|' + l.tooth; });
        d.procedures.forEach(function (p) { if (have.indexOf(p.code + '|' + p.tooth) < 0) visit.lines.push({ code: p.code, tooth: p.tooth, surface: p.surface, quad: p.quad }); });
        U.toast('Draft note ready. Review every line before signing.', 'ok'); load();
      }, DA.fail);
    }
    function codeCheck() {
      noteText();
      DA.post(P('/code-check'), { note: visit.note, lines: visit.lines, note_id: visit.noteId }).then(function (r) { U.toast(r.suggestions.length ? r.suggestions.length + ' coding suggestion' + (r.suggestions.length === 1 ? '' : 's') + ' to review' + (r.ai ? '' : ' (rules only; the AI coder isn’t configured)') + '.' : 'The procedures match the note.', r.suggestions.length ? 'info' : 'ok'); load(); }, DA.fail);
    }

    /* ---------------- Insurance ---------------- */
    function insuranceTab() {
      var c = cov() || {}, b = ben;
      var form = U.fields([{ k: 'payer', label: 'Dental plan', type: 'select', options: (window.DX ? DX.PAYERS : []).concat(c.payer && (window.DX ? DX.PAYERS : []).indexOf(c.payer) < 0 ? [c.payer] : []).concat(['Self-pay']), def: c.payer }, { k: 'payer_id', label: 'Payer ID', def: c.payer_id },
        { k: 'member_id', label: 'Subscriber ID', def: c.member_id }, { k: 'group', label: 'Group #', def: c.group }, { k: 'subscriber', label: 'Patient is the', type: 'select', options: [['self', 'Subscriber'], ['spouse', 'Spouse'], ['child', 'Child'], ['other', 'Other']], def: c.subscriber || 'self' }], {});
      var bv = '';
      if (b && b.checked_at) {
        var pct = b.annual_max ? Math.round(100 * (b.max_remaining || 0) / b.annual_max) : 0;
        bv = '<div class="ben"><div><span>Coverage</span><b class="' + (b.active ? 's-grn' : 's-red') + '">' + (b.active ? 'Active' : 'Not active') + '</b><small>' + E(b.plan_name || '') + '</small></div><div><span>Annual maximum left</span><b>' + M0(b.max_remaining) + '</b><small>of ' + M0(b.annual_max) + ' · used ' + M0(b.max_used) + '</small><div class="meter' + (pct < 25 ? ' bad' : pct < 50 ? ' warn' : '') + '"><i style="width:' + pct + '%"></i></div></div>' +
          '<div><span>Deductible left</span><b>' + M0(b.deductible_remaining) + '</b><small>of ' + M0(b.deductible) + '</small></div><div><span>Checked</span><b style="font-size:14px">' + DA.us(b.checked_at) + '</b><small>' + (b.environment === 'mock' ? '<span class="mockflag">MOCK 271</span>' : 'via clearinghouse') + '</small></div></div>' +
          '<div class="cov">' + Object.keys(b.coinsurance || {}).map(function (k) { return '<div><span>' + E(k.replace('_', ' ')) + '</span><b>' + b.coinsurance[k] + '%</b></div>'; }).join('') + (b.ortho_lifetime_max ? '<div><span>Ortho lifetime</span><b>' + M0(b.ortho_lifetime_max) + '</b></div>' : '') + '</div>' +
          U.sect('Frequency limits', 'from the payer and this patient’s history', U.grid(['Service', 'Plan allows', 'Last done', 'Covered today?', 'Next covered'], (b.frequency || []).map(function (f) { return { cells: [E(f.label), E(f.rule), f.last ? DA.us(f.last) : '<span class="mut">never</span>', f.ok ? '<span class="s-grn">' + U.I.check + 'Yes</span>' : '<span class="s-red">No</span>', f.next ? DA.us(f.next) : ''] }; }), { tight: 1 })) +
          ((b.waiting_periods || []).length ? U.sect('Waiting periods', '', '<div class="dpad">' + b.waiting_periods.map(function (w) { return E(w.waiting) + ': ' + (w.months || '?') + ' months'; }).join('<br>') + '</div>') : '') +
          ((b.messages || []).length ? U.sect('What the payer said', '', '<div class="dpad mono" style="font-size:11.5px">' + b.messages.map(E).join('<br>') + '</div>') : '');
      } else bv = DA.blank(c.payer && c.payer !== 'Self-pay' ? '<b>Benefits not verified</b>Check eligibility to see the remaining maximum, deductible, coverage by category and frequency limits.' : '<b>No dental insurance</b>Consider a membership plan on the Account tab.');
      var claims = X.claims.filter(function (c) { return c.kind === 'claim'; }).map(function (cl) { return { attrs: ' data-claim="' + cl.id + '"', cells: ['<span class="mono">' + E(cl.claim_no) + '</span>', DA.us(cl.dos), cl.lines.map(DA.line).join(', '), E(cl.payer), DA.statusChip(cl.status), M(cl.total), M(cl.paid)] }; });
      return '<div class="main" style="grid-template-columns:330px minmax(0,1fr);height:calc(100vh - var(--chrome, 82px))">' +
        U.pane('Dental plan', 'primary', '', '<div class="dp-sec" id="covform">' + form + '<div style="display:flex;gap:6px;margin-top:10px"><button class="btn" id="covsave">Save</button><button class="btn pri" id="verify">Verify eligibility & benefits</button></div></div>' +
          '<div class="dp-sec"><div class="dp-h">Pre-authorizations</div>' + (X.preauths.length ? X.preauths.map(function (p) { return '<div>' + DA.statusChip(p.status) + ' ' + E(p.payer) + ' · ' + M(p.amount) + (p.approved_amount ? ' → ' + M(p.approved_amount) : '') + '</div>'; }).join('') : '<span class="mut">None</span>') + '</div>') +
        '<section class="pane"><div class="pbody">' + bv + U.sect('Claims', claims.length + '', U.grid(['Claim', 'DOS', 'Procedures', 'Payer', 'Status', ['Billed', 'num'], ['Paid', 'num']], claims, { click: 1, tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(7, 'No claims yet.') + '</tbody>')) + '</div></section></div>';
    }

    /* ---------------- Account ---------------- */
    function accountTab() {
      var pend = X.claims.filter(function (c) { return c.kind === 'claim' && ['draft', 'ready', 'submitted', 'accepted'].indexOf(c.status) >= 0; }).reduce(function (s, c) { return s + c.est_insurance; }, 0);
      var due = Math.max(0, X.balance - pend), run = 0;
      var led = X.ledger.slice().reverse().map(function (e) { run += e.amount; return [e, run]; }).reverse();
      var mem = X.membership;
      return '<div class="main" style="grid-template-columns:minmax(0,1fr) 320px;height:calc(100vh - var(--chrome, 82px))"><section class="pane">' +
        '<div class="kpis" style="grid-template-columns:repeat(4,1fr)"><div><span>Account balance</span><b>' + M(X.balance) + '</b></div><div><span>Expected from insurance</span><b class="s-grn">' + M(pend) + '</b></div><div><span>Patient owes now</span><b class="' + (due > 0 ? 's-red' : '') + '">' + M(due) + '</b></div><div><span>Membership</span><b style="font-size:14px">' + (mem ? E(mem.plan.name) : '<span class="mut">None</span>') + '</b>' + (mem ? '<small>renews ' + DA.us(mem.renews) + '</small>' : '') + '</div></div>' +
        '<div class="chartbar"><button class="cbtn" id="a-pay">Collect payment</button><button class="cbtn" id="a-ttp">Text-to-pay link</button><button class="cbtn" id="a-plan">Payment plan</button><button class="cbtn" id="a-mem">Membership</button><button class="cbtn" id="a-adj">Adjustment</button></div>' +
        '<div class="pbody">' + U.grid(['Date', 'Type', 'Description', 'Ref', ['Amount', 'num'], ['Balance', 'num']], led.map(function (x) { var e = x[0]; return { cells: [DA.us(e.date), E(e.kind.replace('_', ' ')), E(e.memo || ''), '<span class="mono">' + E(e.ref || '') + '</span>', '<span class="' + (e.amount < 0 ? 's-grn' : '') + '">' + M(e.amount) + '</span>', M(x[1])] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No activity.') + '</tbody>') + '</div></section>' +
        U.pane('Payment plans', X.payment_plans.length + '', '', X.payment_plans.map(function (p) { var paid = p.schedule.filter(function (s) { return s.paid_at; }).reduce(function (s, x) { return s + x.amount; }, 0), nx = p.schedule.filter(function (s) { return !s.paid_at; })[0]; return '<div class="dp-sec"><div class="dp-h">' + M(p.total) + ' over ' + p.months + ' months ' + DA.statusChip(p.status) + '</div><div class="meter"><i style="width:' + Math.round(100 * paid / p.total) + '%"></i></div><div class="mut" style="font-size:12px;margin-top:4px">' + M(paid) + ' paid' + (nx ? ' · next ' + M(nx.amount) + ' on ' + DA.us(nx.due) : '') + (p.autopay ? ' · autopay' : '') + '</div>' + (nx ? '<button class="mini" style="margin-top:6px" data-ppcharge="' + p.id + '">Charge next installment</button>' : '') + '</div>'; }).join('') || '<div class="dp-sec mut">None. Spread a balance over months with Payment plan.</div>') + '</div>';
    }

    /* ---------------- Rx & labs ---------------- */
    function rxTab() {
      return '<div class="main" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);height:calc(100vh - var(--chrome, 82px))">' +
        U.pane('Prescriptions', X.rx.length + '', '<a href="#" id="newrx">New prescription</a>', U.grid(['Date', 'Drug', 'Directions', 'Qty', 'Status'], X.rx.map(function (r) { return { cells: [DA.us(r.created), E(r.drug) + (r.controlled ? ' <span class="chip red">C-' + E(r.schedule) + '</span>' : ''), '<span class="wrap">' + E(r.sig) + '</span>', E(r.qty), DA.statusChip(r.status === 'sent' ? 'approved' : r.status)] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'None.') + '</tbody>') +
          '<div class="dp-sec mut" style="font-size:12px">E-prescribing goes through the eRx vendor (a mock until contracted). Controlled substances (EPCS) need the dentist’s DEA number and a fresh two-step login code.</div>') +
        U.pane('Lab cases', X.labs.length + '', '<a href="#" id="newlab">New lab case</a>', U.grid(['Item', 'Tooth', 'Lab', 'Sent', 'Due', 'Status'], X.labs.map(function (l) { return { cells: [E(l.item), E(l.tooth), E(l.lab), DA.us(l.sent), '<span class="' + (l.status === 'sent' && l.due < DA.today() ? 's-red' : '') + '">' + DA.us(l.due) + '</span>', E(l.status.replace('_', ' '))] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'None.') + '</tbody>')) + '</div>';
    }

    /* ---------------- Forms & history ---------------- */
    function formsTab() {
      var p = X.patient;
      return '<div class="main" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);height:calc(100vh - var(--chrome, 82px))">' +
        U.pane('Medical history', p.mh_updated ? 'updated ' + DA.us(p.mh_updated) : '', p.mh_review_pending ? '<button class="mini pri" id="mhok">Mark reviewed</button>' : (p.mh_reviewed ? 'reviewed ' + DA.us(p.mh_reviewed) : ''),
          '<div class="dp-sec">' + U.kv([['Allergies', '<span class="' + (p.allergies && !/^nkda$/i.test(p.allergies) ? 's-red' : '') + '">' + E(p.allergies || 'None recorded') + '</span>'], ['Medical alerts', E((p.medical_alerts || []).join(', ') || 'None')], ['Conditions', E(p.problems || 'None')], ['Medications', E(String(p.meds || 'None').replace(/\n/g, '; '))], ['Texts', p.sms_opt_out ? '<span class="s-red">Opted out (STOP)</span>' : 'OK']]) + '</div>' +
          '<div class="dp-sec"><div class="dp-h">Forms <span class="end"><a href="#" id="sendforms">Send forms</a></span></div>' + (X.forms.map(function (f) { return '<div>' + DA.us(f.created) + ' · form ' + f.template_id + ' ' + DA.statusChip(f.status === 'applied' ? 'completed' : f.status === 'sent' ? 'pending' : f.status) + '</div>'; }).join('') || '<span class="mut">None sent</span>') + '</div>') +
        U.pane('Appointments', X.appointments.length + '', '', U.grid(['Date', 'Time', 'Provider', 'Reason', 'Status'], X.appointments.map(function (a) { return { cells: [DA.us(a.start), DA.time(a.start), E(a.provider), E(a.reason), E(a.status.replace('_', ' '))] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'None.') + '</tbody>')) + '</div>';
    }

    /* ---------------- events ---------------- */
    load();
    U.on(app, '[data-tab]', 'click', function (b) { if (rec && !rec.ambient) { rec.stop(); rec = null; } tab = b.dataset.tab; history.replaceState(null, '', '/v4/dental/chart?mrn=' + encodeURIComponent(mrn) + '&tab=' + tab + (visit.appt ? '&appt=' + visit.appt : '')); draw(); });
    U.on(app, '[data-mode]', 'click', function (b) { mode = b.dataset.mode; draw(); });
    U.on(app, '#actplan', 'change', function (s) { activePlan = +s.value; });
    U.on(app, '.odo .surf', 'click', function (el, e) {
      e.stopPropagation(); var t = +el.dataset.tooth, sf = el.dataset.surf;
      if (sel.length !== 1 || sel[0] !== t) { sel = [t]; picks = []; }
      var i = picks.indexOf(sf); if (i >= 0) picks.splice(i, 1); else picks.push(sf);
      picks = 'MODBLIF'.split('').filter(function (x) { return picks.indexOf(x) >= 0; });
      draw();
    });
    U.on(app, '.odo .tooth', 'click', function (el, e) {
      var t = +el.dataset.tooth;
      if (e.shiftKey || e.metaKey || e.ctrlKey) { var i = sel.indexOf(t); if (i >= 0) sel.splice(i, 1); else sel.push(t); picks = []; }
      else { sel = sel.length === 1 && sel[0] === t ? [] : [t]; picks = []; }
      draw();
    });
    U.on(app, '#clrsel', 'click', function (a, e) { e.preventDefault(); sel = []; picks = []; draw(); });
    U.on(app, '[data-tool]', 'click', function (b) { chartTool(b.dataset.tool); });
    U.on(app, '[data-rm-cond]', 'click', function (b, e) { e.stopPropagation(); DA.del('/conditions/' + b.dataset.rmCond).then(load, DA.fail); });
    U.on(app, '[data-rm-item]', 'click', function (b) { U.confirm('Remove from plan', '<p class="cf">Take this procedure off the treatment plan?</p>', 'Remove', function () { DA.del('/plan-items/' + b.dataset.rmItem).then(load, DA.fail); }, { danger: 1 }); });
    U.on(app, '[data-done-item]', 'click', function (b) {
      var id = +b.dataset.doneItem, it = null; X.plans.forEach(function (p) { p.items.forEach(function (i) { if (i.id === id) it = i; }); });
      DA.post(P('/procedures'), { lines: [{ code: it.code, tooth: it.tooth, surface: it.surface, quad: it.quad, fee: it.fee, plan_item_id: it.id }], provider_id: myProv().id, date: DA.today(), claim: !!(cov() && cov().payer) })
        .then(function (r) { U.toast('Completed ' + it.code + (it.tooth ? ' #' + it.tooth : '') + (r.claim ? '; claim ' + r.claim.claim_no + ' created.' : '.'), 'ok'); load(); }, DA.fail);
    });
    U.on(app, '[data-openimg]', 'click', function (el, e) { e.preventDefault(); openImage(+el.dataset.openimg); });
    // perio entry
    U.on(app, '#pexam', 'change', function (s) { openExam(+s.value || null); });
    U.on(app, '#pnew', 'click', function () { openExam(null); });
    U.on(app, '#psave', 'click', function () {
      DA.post(P('/perio'), { id: perio.exam ? perio.exam.id : undefined, readings: perio.readings, provider_id: myProv().id, source: perio.source || 'manual' }).then(function (x) { perio.exam = x; perio.dirty = false; U.toast('Perio exam saved.', 'ok'); load(); }, DA.fail);
    });
    U.on(app, '.pdrow td.t', 'click', function (td, e) {
      var t = +td.dataset.t, side = td.parentElement.classList.contains('F') ? 'F' : 'L', k = PC.PATH.findIndex ? PC.PATH.findIndex(function (x) { return x[0] === t && x[1] === side; }) : -1;
      if (k >= 0) { perio.pos = k; buf = []; redrawPerio(); }
    });
    U.on(app, '.bop span[data-bop]', 'click', function (sp) { var t = +sp.closest('td').dataset.t, k = +sp.dataset.bop, v = perio.readings[t]; if (v.bop[k]) { v.bop[k] = 0; v.sup[k] = v.sup[k] ? 0 : 0; } else v.bop[k] = 1; perio.dirty = true; redrawPerio(); });
    U.on(app, '[data-num]', 'click', function (td) { var t = +td.dataset.num; U.ask('#' + t, 'Mobility (0-3), furcation (0-3) or "missing"', '', function (v) { var r = perio.readings[t]; if (/miss/i.test(v)) r.missing = !r.missing; else { var n = v.split(/[ ,]+/); r.mob = +n[0] || null; if (n[1] != null) r.furc = +n[1] || null; } perio.dirty = true; redrawPerio(); }, { ok: 'Set', ph: 'e.g. 1 2' }); });
    document.addEventListener('keydown', function (e) {
      if (tab !== 'perio' || e.target.closest('input,textarea,select') || e.metaKey || e.ctrlKey) return;
      if (/^[0-9]$/.test(e.key)) { e.preventDefault(); perioEnter(+e.key); }
      else if (e.key === 'Backspace') { e.preventDefault(); if (buf.length) { buf.pop(); } else if (perio.pos > 0) { perio.pos--; } redrawPerio(); }
      else if (e.key === 'b' || e.key === 'B') { var p = PC.PATH[Math.max(0, perio.pos - 1)]; if (p) { PC.idx(p[1]).forEach(function (k) { perio.readings[p[0]].bop[k] = 1; }); perio.dirty = true; redrawPerio(); } }
    });
    U.on(app, '#pcmd', 'keydown', function (i, e) { if (e.key === 'Enter' && i.value.trim()) { voiceText(i.value); i.value = ''; } });
    U.on(app, '#pmic', 'click', toggleMic);
    U.on(app, '#psrp', 'click', function () {
      var srp = perio.exam.summary.srp; var lines = srp.map(function (x) { return { code: x.code, quad: x.quad }; });
      (activePlan ? DA.post('/plans/' + activePlan + '/items', { items: lines }) : DA.post(P('/plans'), { name: 'Periodontal therapy', provider_id: myProv().id, items: lines })).then(function () { U.toast('Scaling and root planing added to the treatment plan.', 'ok'); tab = 'plans'; load(); }, DA.fail);
    });
    // plans
    U.on(app, '#newplan', 'click', function () { U.ask('New treatment plan', 'Name', 'Treatment plan', function (n) { DA.post(P('/plans'), { name: n, provider_id: myProv().id }).then(function (r) { activePlan = r.id; tab = 'chart'; mode = 'plan'; U.toast('Plan started. Chart procedures in Treatment plan mode.', 'ok'); load(); }, DA.fail); }, { ok: 'Create' }); });
    U.on(app, '[data-plan-est]', 'click', function (b) { DA.post('/plans/' + b.dataset.planEst + '/estimate').then(function (r) { U.toast('Re-estimated with ' + (r.basis === 'insurance' ? 'current benefits' : r.basis === 'membership' ? 'the membership plan' : 'office fees') + ': patient ' + M(r.patient) + '.', 'ok'); load(); }, DA.fail); });
    U.on(app, '[data-plan-present]', 'click', function (b) { DA.post('/plans/' + b.dataset.planPresent + '/present').then(function (r) { var url = location.origin + r.url; window.open(r.url, '_blank'); U.toast('Presentation opened. Patient portion ' + M(r.estimate.patient) + '. The patient can review and sign there.', 'ok'); load(); }, DA.fail); });
    U.on(app, '[data-plan-pa]', 'click', function (b) {
      var p = X.plans.filter(function (x) { return x.id === +b.dataset.planPa; })[0], ids = [].slice.call(app.querySelectorAll('[data-pick-item]:checked')).map(function (i) { return +i.dataset.pickItem; });
      var items = p.items.filter(function (i) { return ids.length ? ids.indexOf(i.id) >= 0 : (i.status === 'proposed' || i.status === 'accepted') && /^D(27|29|3|43|6|72)/.test(i.code); });
      if (!items.length) return U.toast('Tick the procedures to pre-authorize.', 'warn');
      DA.post('/preauths', { item_ids: items.map(function (i) { return i.id; }) }).then(function (r) { U.toast('Predetermination sent (' + (r.ref || r.status) + '). Track it under Insurance › Pre-auths.', 'ok'); load(); }, DA.fail);
    });
    U.on(app, '[data-plan-book]', 'click', function (b) { bookItems(+b.dataset.planBook); });
    // imaging
    U.on(app, '#acquire', 'click', function () {
      DA.get('/devices').then(function (devs) {
        U.form('Take images', [{ k: 'device', label: 'Device', type: 'select', options: devs.map(function (d) { return [d.id, d.name]; }) }, { k: 'kind', label: 'Kind', type: 'select', options: [['bitewing', 'Bitewings (right)'], ['bitewing_l', 'Bitewings (left)'], ['pa', 'Periapical'], ['pano', 'Panoramic'], ['cbct', 'CBCT'], ['photo', 'Intraoral photo'], ['scan', 'Intraoral scan']] }, { k: 'teeth', label: 'Teeth (PA)', ph: '30, 31' }], {}, function (v) {
          var kind = v.kind === 'bitewing_l' ? 'bitewing' : v.kind, teeth = v.kind === 'bitewing_l' ? ['12', '13', '14', '15', '18', '19', '20', '21'] : v.teeth ? v.teeth.split(/[ ,]+/).filter(Boolean) : null;
          DA.post(P('/images'), { device: v.device, kind: kind, teeth: teeth }).then(function () { U.toast('Image captured.', 'ok'); load(); }, DA.fail);
        }, { save: 'Capture' });
      });
    });
    U.on(app, '#iupload', 'click', function () { U.pickFile('image/png,image/jpeg,image/webp', false, function (f) { U.readFile(f[0]).then(function (d) { DA.post(P('/images'), { data: d, kind: 'photo' }).then(function () { U.toast('Uploaded.', 'ok'); load(); }, DA.fail); }); }); });
    // notes
    U.on(app, '#ntpl', 'change', function (s) { if (!s.value) return; noteText(); visit.note = (visit.note ? visit.note + '\n\n' : '') + NOTE_TPL[s.value]; draw(); });
    U.on(app, '#newnote', 'click', function (a, e) { e.preventDefault(); visit = { lines: [], note: '', noteId: null, transcript: '', appt: visit.appt }; draw(); });
    U.on(app, '[data-note]', 'click', function (el) { var n = X.notes.filter(function (x) { return x.id === +el.dataset.note; })[0]; if (!n) return; if (n.status === 'signed') return U.modal('Note ' + DA.us(n.date), '<pre style="white-space:pre-wrap;font:inherit;margin:0">' + E(n.text) + '</pre><div class="mut" style="margin-top:8px">Signed by ' + E(n.signed_by) + '</div>', [['Close', '', function () {}]]); visit.note = n.text; visit.noteId = n.id; draw(); });
    U.on(app, '#nrec', 'click', ambient);
    U.on(app, '#ndraft', 'click', draftNote);
    U.on(app, '#ncheck', 'click', codeCheck);
    U.on(app, '[data-sug]', 'click', function (b) {
      var s = X.suggestions.filter(function (x) { return x.id === +b.dataset.sug; })[0], d = b.dataset.d;
      DA.post('/suggestions/' + s.id, { decision: d }).then(function () {
        if (d === 'approved') { visit.lines = visit.lines.filter(function (l) { return !(s.billed && l.code === s.billed && (!s.tooth || l.tooth === s.tooth)); }); visit.lines.push({ code: s.code, tooth: s.tooth, surface: s.surface, quad: s.quad }); }
        noteText(); U.toast(d === 'approved' ? s.code + ' added to today’s procedures.' : 'Rejected.', 'ok'); load();
      }, DA.fail);
    });
    U.on(app, '[data-vl]', 'change', function (i) { visit.lines[+i.dataset.vl][i.dataset.f] = i.value.trim().toUpperCase(); noteText(); draw(); });
    U.on(app, '[data-vl-rm]', 'click', function (b) { visit.lines.splice(+b.dataset.vlRm, 1); noteText(); draw(); });
    U.on(app, '#vladd', 'click', function () { noteText(); visit.lines.push({ code: 'D', tooth: '', surface: '', quad: '' }); draw(); });
    var saveNote = function (sign) { noteText(); return DA.post(P('/notes'), { id: visit.noteId || undefined, text: visit.note, kind: 'progress', appt_id: visit.appt || undefined, provider_id: myProv().id, sign: sign, transcript: visit.transcript || undefined }).then(function (n) { visit.noteId = n.id; return n; }); };
    U.on(app, '#nsave', 'click', function () { saveNote(false).then(function () { U.toast('Draft saved.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#nsign', 'click', function () {
      if (!noteText().trim()) return U.toast('Write the note first.', 'warn');
      var lines = visit.lines.filter(function (l) { return /^D\d{4}$/.test(l.code); });
      saveNote(true).then(function () { return lines.length ? DA.post(P('/procedures'), { lines: lines, provider_id: myProv().id, date: DA.today(), claim: !!(cov() && cov().payer) }) : null; })
        .then(function (r) { U.toast('Note signed' + (lines.length ? ', ' + lines.length + ' procedure' + (lines.length === 1 ? '' : 's') + ' posted' + (r && r.claim ? ', claim ' + r.claim.claim_no + ' ' + (r.claim.status === 'ready' ? 'ready to send' : 'needs a look') : '') : '') + '.', 'ok'); visit = { lines: [], note: '', noteId: null, transcript: '', appt: 0 }; load(); }, DA.fail);
    });
    // insurance
    U.on(app, '#covsave', 'click', function () { DA.post(P('/coverage'), U.values(document.getElementById('covform'))).then(function () { ben = null; U.toast('Saved. Verify benefits again for the new details.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#verify', 'click', function () {
      var v = U.values(document.getElementById('covform'));
      DA.post(P('/coverage'), v).then(function () { U.toast('Checking eligibility (270)…', 'busy'); return DA.post(P('/eligibility')); }).then(function (b) { ben = b; U.toast(b.active ? 'Active. ' + M0(b.max_remaining) + ' of ' + M0(b.annual_max) + ' left this year.' : 'The payer says coverage isn’t active.', b.active ? 'ok' : 'warn'); load(); }, DA.fail);
    });
    U.on(app, 'tr[data-claim]', 'click', function (tr) { location.href = '/v4/dental/claims?claim=' + tr.dataset.claim; });
    // account
    U.on(app, '#a-pay', 'click', function () { DA.takePayment(mrn, X.balance, load); });
    U.on(app, '#b-pay', 'click', function () { DA.takePayment(mrn, X.balance, load); });
    U.on(app, '#a-ttp', 'click', function () { U.ask('Text-to-pay', 'Amount to request (blank = what the patient owes now)', '', function (v) { DA.post(P('/text-to-pay'), { amount: +v || undefined }).then(function (r) { U.toast('Payment link texted for ' + M(r.amount) + '.', 'ok'); load(); }, DA.fail); }, { ok: 'Send link', type: 'number', step: '0.01' }); });
    U.on(app, '#a-plan', 'click', function () {
      U.form('Payment plan', [{ k: 'total', label: 'Amount', type: 'number', step: '0.01', def: Math.max(0, X.balance).toFixed(2), req: 1 }, { k: 'down', label: 'Down payment', type: 'number', step: '0.01', def: '0' }, { k: 'months', label: 'Months', type: 'select', options: ['3', '6', '9', '12', '18', '24'], def: '6' }, { k: 'autopay', label: 'Autopay with the test card on file', type: 'check' }], {}, function (v) {
        DA.post(P('/payment-plans'), { total: +v.total, down: +v.down, months: +v.months, autopay: v.autopay, token: v.autopay ? 'tok_visa' : '' }).then(function () { U.toast('Payment plan set up.', 'ok'); load(); }, DA.fail);
      }, { save: 'Create plan' });
    });
    U.on(app, '[data-ppcharge]', 'click', function (b) { DA.post('/payment-plans/' + b.dataset.ppcharge + '/charge').then(function () { U.toast('Installment charged.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#a-mem', 'click', function () {
      var plans = DA.setup.membership_plans;
      if (!plans.length) return U.toast('Add membership plans in Practice setup first.', 'warn');
      U.form('Membership plan', [{ k: 'plan_id', label: 'Plan', type: 'select', options: plans.map(function (p) { return [p.id, p.name + ' (' + M0(p.monthly) + '/mo or ' + M0(p.annual) + '/yr)']; }) }, { k: 'billing', label: 'Billing', type: 'select', options: [['monthly', 'Monthly'], ['annual', 'Annual']] }], {}, function (v) {
        DA.post(P('/membership'), { plan_id: +v.plan_id, billing: v.billing }).then(function () { U.toast('Membership started. Treatment estimates now use member pricing.', 'ok'); load(); }, DA.fail);
      }, { save: 'Join' });
    });
    U.on(app, '#a-adj', 'click', function () { U.form('Adjustment', [{ k: 'amount', label: 'Amount (negative = credit)', type: 'number', step: '0.01', req: 1 }, { k: 'memo', label: 'Reason', req: 1, span: 2 }], {}, function (v) { DA.post(P('/adjustments'), { amount: +v.amount, memo: v.memo }).then(function () { U.toast('Adjustment posted.', 'ok'); load(); }, DA.fail); }); });
    // rx & labs
    U.on(app, '#newrx', 'click', function (a, e) {
      e.preventDefault();
      DA.get('/rx/formulary').then(function (f) {
        U.form('New prescription', [{ k: 'drug', label: 'Drug', type: 'select', options: f.map(function (x) { return [x.drug, x.drug + (x.schedule ? ' (C-' + x.schedule + ')' : '')]; }), span: 2 }, { k: 'prescriber_id', label: 'Prescriber', type: 'select', options: dentists().map(function (d) { return [d.id, d.name]; }) }, { k: 'refills', label: 'Refills', type: 'number', def: '0' },
          { k: 'pharmacy', label: 'Pharmacy', span: 2, ph: 'Pharmacy name and city' }, { k: 'code', label: 'Two-step code (controlled only)', ph: '6 digits' }], {}, function (v) {
          var d = f.filter(function (x) { return x.drug === v.drug; })[0];
          var send = function (override) { DA.post(P('/rx'), Object.assign({}, v, { sig: d.sig, qty: d.qty, schedule: d.schedule, override_allergy: override })).then(function (r) { U.toast(r.message, 'ok'); load(); }, function (er) { if (er.status === 409) U.confirm('Allergy alert', '<p class="cf">' + E(er.message) + '</p>', 'Prescribe anyway', function () { send(true); }, { danger: 1 }); else DA.fail(er); }); };
          send(false);
        }, { wide: 1, save: 'Send to pharmacy' });
      });
    });
    U.on(app, '#newlab', 'click', function (a, e) { e.preventDefault(); U.form('New lab case', [{ k: 'item', label: 'Item', req: 1, ph: 'Crown, e.max' }, { k: 'tooth', label: 'Tooth' }, { k: 'shade', label: 'Shade', ph: 'A2' }, { k: 'lab', label: 'Lab', req: 1 }, { k: 'due', label: 'Due back', type: 'date', req: 1 }, { k: 'cost', label: 'Lab fee', type: 'number', step: '0.01' }], {}, function (v) { DA.post('/labs', Object.assign(v, { mrn: mrn, cost: +v.cost || 0 })).then(function () { U.toast('Lab case added.', 'ok'); load(); }, DA.fail); }); });
    // forms
    U.on(app, '#mhok', 'click', function () { DA.post(P('/mh-reviewed')).then(function () { U.toast('Medical history marked reviewed.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#sendforms', 'click', function (a, e) { e.preventDefault(); DA.get('/forms').then(function (f) { U.form('Send forms', f.templates.map(function (t) { return { k: 't' + t.id, label: t.name, type: 'check', span: 2 }; }), {}, function (v) { var ids = f.templates.filter(function (t) { return v['t' + t.id]; }).map(function (t) { return t.id; }); if (!ids.length) return 'Pick at least one form.'; DA.post(P('/forms'), { template_ids: ids }).then(function (r) { U.toast(r.sent.length + ' form link' + (r.sent.length === 1 ? '' : 's') + ' texted.', 'ok'); load(); }, DA.fail); }, { save: 'Text the link' }); }); });
    // banner
    U.on(app, '#b-switch', 'click', function () { DA.pickPatient('Switch patient', function (m) { location.href = '/v4/dental/chart?mrn=' + encodeURIComponent(m); }); });
    U.on(app, '#b-book', 'click', function () { location.href = '/v4/dental/schedule'; });
    U.on(app, '#b-text', 'click', function () { U.form('Text ' + X.patient.name, [{ k: 'body', label: 'Message', type: 'textarea', req: 1, span: 4, def: 'Hi ' + DA.first(X.patient.name) + ', ' }], {}, function (v) { DA.post(P('/messages'), { body: v.body }).then(function () { U.toast('Text sent.', 'ok'); }, DA.fail); }, { save: 'Send' }); });
    window.addEventListener('beforeunload', function (e) { if (perio.dirty) { e.preventDefault(); e.returnValue = ''; } });
  };
})();
