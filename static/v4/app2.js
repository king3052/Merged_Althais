/* v4 working preview, part 2: the encounter (Virtual SOAP). Editable SOAP note, browser speech recording,
   codes from /api/code-note (or added by hand), payer-rule checks with cited evidence, orders, and signing,
   which creates the claim. */
(function () {
  var app = V.app, E = U.esc, M = U.money, I = U.I, S = D.S;
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;

  var SRC = { althea_typed: 'Typed to Althea', althea_spoken: 'Said to Althea', command: 'Althea command (not used in the note)', paste: 'Pasted' };
  // the claim a visit would produce: the accepted codes, or until any are accepted, the suggestions not rejected
  function visitClaim(e) {
    var acc = e.codes.filter(function (c) { return c.state === 'acc'; }); if (!acc.length) acc = e.codes.filter(function (c) { return c.state !== 'rej'; });
    return { encId: e.id, mrn: e.mrn, dos: e.date, prov: e.prov, lines: acc.filter(function (c) { return D.isProc(c.type); }).map(function (c) { return D.lineOf({ code: c.code, mod: c.mod || '', units: 1, tooth: c.tooth, surface: c.surface, quad: c.quad }); }), dx: acc.filter(function (c) { return !D.isProc(c.type); }).map(function (c) { return c.code; }), stage: 'edits', resolved: e.ignored || {} };
  }
  function visitEdits(e) { var c = visitClaim(e); c._enc = V.withAdds(e, e.pendingAdds || []); return D.checkClaim(c).filter(function (x) { return !(e.ignored || {})[x.id]; }); }
  V.visitClaim = visitClaim;
  // sign a visit: the Sign Note window (claim check, codes, charge, Althea's additions, orders), then sign and create the claim.
  // Used by the encounter and by Revenue › Claims (Waiting for sign-off).
  V.signVisit = function (e, after, opts) {
    opts = opts || {};
    var p = D.pt(e.mrn) || { name: '(removed patient)', mrn: e.mrn };
      var adds = e.pendingAdds || [];
      var ed = visitEdits(e), cpt = e.codes.filter(function (c) { return c.state === 'acc' && D.isProc(c.type); });
      var dx = e.codes.filter(function (c) { return c.state === 'acc' && !D.isProc(c.type); }), k = e.check, pc = visitClaim(e);
      var row = function (c) { return '<div class="so-code"><b>' + E(D.DENTAL && D.isProc(c.type) ? DX.lineText(c) : c.code + (c.mod ? '-' + c.mod : '')) + '</b><span>' + E(c.desc && c.desc !== c.code ? c.desc : '') + '</span></div>'; };
      var col = k ? V.scoreColor(k.score) : 'mut';
      var body = '<div class="so">' +
        '<div class="so-who">Signing as <b>' + E(S.clinic.user) + '</b> · ' + E(p.name) + ' · ' + E(D.us ? D.us(e.date) : e.date) + '</div>' +
        (cpt.length ? '<div class="so-sec"><div class="so-h">Claim</div>' +
          (k ? '<div class="so-score"><b class="s-' + col + '">' + k.score + '%</b><div class="so-bar"><i style="width:' + k.score + '%;background:var(--' + col + ')"></i></div><span>' + (k.score >= 85 ? 'Ready To Submit' : k.score >= 65 ? 'Usable, Some Risk' : 'Not Ready') + ' · Checked' + (k.at ? ' ' + new Date(k.at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : '') + '</span></div>' : '') +
          '<div class="so-codes">' + cpt.map(row).join('') + dx.map(row).join('') + '</div>' +
          '<div class="so-tot"><span>Estimated charge</span><b>' + U.money(D.charge(pc)) + '</b></div>' +
          (ed.length ? '<div class="so-warn">' + U.warn + 'Held with ' + ed.length + ' edit' + (ed.length === 1 ? '' : 's') + ' to resolve.</div>' : '') + '</div>'
          : '<div class="so-sec"><div class="so-warn">' + U.warn + 'No visit code, so no claim will be created.</div></div>') +
        (adds.length ? '<div class="so-sec"><div class="so-h">Added to the note<span>' + adds.length + ', quoted from the visit</span></div><div class="so-adds">' + adds.map(function (a) {
          return '<div class="so-add"><em>' + E(a.section) + '</em><div>' + E(a.text) + (a.source_quote ? '<small title="' + E(a.source_quote) + '">“' + E(a.source_quote) + '”</small>' : '') + '</div></div>'; }).join('') + '</div></div>' : '') +
        (e.orders.length ? '<div class="so-sec"><div class="so-h">Orders<span>' + e.orders.length + '</span></div><div class="so-codes">' + e.orders.map(function (o) { return '<div class="so-code"><b>' + E(o.type || 'Order') + '</b><span>' + E(o.name || '') + '</span></div>'; }).join('') + '</div></div>' : '') +
        '<div class="so-foot">Signing approves the note' + (cpt.length ? ' and the claim' : '') + ', recorded with your name and the time.</div></div>';
      if (opts.silent) return doSign();
      return U.modal('Sign Note', body, [['Cancel', '', function () {}], [cpt.length ? 'Sign And Approve Claim' : 'Sign Note', 'pri', function () { doSign(); }]], { width: 540 });
      function doSign() {
      adds.forEach(function (a) { var k = { subjective: 's', objective: 'o', assessment: 'a', plan: 'p' }[a.section] || 'a'; e[k] = (e[k] ? String(e[k]).replace(/\s+$/, '') + '\n' : '') + a.text; });
      e.pendingAdds = [];
      e.status = 'Signed'; e.signedAt = new Date().toISOString(); e.signedBy = S.clinic.user; e.orders.forEach(function (o) { o.status = 'Signed'; });
      S.appts.forEach(function (a) { if (a.encId === e.id) a.visit = 'Checked out'; });
      var made = null;
      if (cpt.length) {
        var c = visitClaim(e); c.id = D.nextClaimId(); c.payer = p.payer; c.created = new Date().toISOString(); c.resolved = Object.assign({}, e.ignored || {}); D.scrub(c); c.hadEdits = c.stage === 'edits';
        if (e.check) { c.check = e.check; c.checkKey = V.checkKey(c); }
        D.history(c, 'Built by Althea' + (e.check ? ' (' + e.check.score + '% claim check)' : '') + ', approved and signed by ' + S.clinic.user + ', ' + (c.stage === 'edits' ? c.edits.length + ' edit(s) to resolve' : 'all checks passed')); S.claims.push(c); made = c;
      }
      D.log('Note signed: ' + p.name + (made ? ', claim ' + made.id : '')); D.save();
      if (made) { made.approvedAt = new Date().toISOString(); made.approvedBy = S.clinic.user; }
      if (made && made.stage === 'ready' && V.queueClaim) V.queueClaim(made);   // approved: Althais submits it with today's batch
      if (after) after(made);
      if (!opts.silent) U.toast('Signed.' + (made ? ' Claim ' + made.id + (made.stage === 'edits' ? ' is in Edits To Resolve.' : ' is approved; Althais submits it today.') : ''));
      }
  };

  // Pasted or dictated text -> SOAP fields, without the AI: section headings when there are any, otherwise each
  // sentence goes where its content belongs (exam findings and results -> Objective, diagnoses -> Assessment,
  // orders and follow-up -> Plan, the rest -> Subjective). Vitals are read out too. Used whenever the AI can't answer.
  V.splitSoap = function (text) {
    var t = String(text || '').replace(/\r/g, ''), f = { hpi: [], exam: [], assessment: [], plan: [] }, m;
    var num = function (re) { var x = t.match(re); return x ? x : null; };
    if ((m = num(/\b(?:BP|blood pressure)\b[:\s]*(?:is|of|was)?\s*(\d{2,3})\s*(?:\/|over)\s*(\d{2,3})/i))) f.bp = m[1] + '/' + m[2];
    if ((m = num(/\b(?:HR|heart rate|pulse)\b[:\s]*(?:is|of|was)?\s*(\d{2,3})\b/i))) f.hr = m[1];
    if ((m = num(/\b(?:temp(?:erature)?|T)\b[:\s]*(?:is|of|was)?\s*(\d{2,3}(?:\.\d)?)\s*(?:°?\s*F)?/i)) && +m[1] > 90 && +m[1] < 110) f.temp = m[1];
    if ((m = num(/\b(?:SpO2|O2 sat(?:uration)?|oxygen saturation|sats?)\b[:\s]*(?:is|of|was)?\s*(\d{2,3})\s*%?/i)) && +m[1] <= 100) f.spo2 = m[1];
    var HEAD = /(^|\n|[.;]\s+)\s*(subjective|history of present illness|hpi|chief complaint|cc|objective|physical exam(?:ination)?|exam(?:ination)?|vitals|labs|results|assessment and plan|assessment\/plan|a\/p|assessment|impression|diagnos[ie]s|dx|plan)\b\s*[:\-]?\s*/gi;
    var KEY = function (h) { h = h.toLowerCase(); return /subj|history|hpi|chief|^cc$/.test(h) ? 'hpi' : /obj|exam|vital|lab|result/.test(h) ? 'exam' : /assessment and plan|assessment\/plan|^a\/p$/.test(h) ? 'ap' : /assess|impress|diagnos|^dx$/.test(h) ? 'assessment' : 'plan'; };
    var PLAN = /\b(start|add|begin|increase|decrease|titrate|stop|discontinue|prescrib|refer|order|recheck|re-check|return|schedule|counsel|educat|advis|repeat|supportive care)\w*|\bfollow[- ]?up (in|with)\b|\b(in|within) \d+ (days?|weeks?|months?)\b/i;
    var CONDITION = /\b(hypertension|htn|diabetes|dm|ckd|kidney disease|copd|asthma|pharyngitis|strep throat|infection|uri|bronchitis|sinusitis|otitis|depression|anxiety|hyperlipidemia|obesity|gerd|arthritis|pneumonia|uti|migraine|hypothyroidism|atrial fibrillation|heart failure)\b/i;
    var STATUS = /\b(uncontrolled|controlled|stable|worsening|improving|stage \w+|acute|chronic|with hyperglycemia|exacerbation|not at goal|at goal|resolved|likely|viral|bacterial)\b/i;
    var DX = function (x) { return /\b(diagnos|impression|consistent with|suspect|rule out|r\/o)\w*|^\s*\d+[.)]\s/i.test(x) || (CONDITION.test(x) && STATUS.test(x) && !/\bfollow[- ]?up\b|\bhere for\b|\bpresents?\b/i.test(x)); };
    var HX = /\b(taking|takes|currently on|reports?|denies|complains?|here for|presents?|history of|follow[- ]?up)\b/i;
    var OBJ = /\b(bp|blood pressure|hr|heart rate|pulse|temp(erature)?|spo2|o2 sat)\b[^.]{0,20}\d|\b(exam|lungs?|abdomen|a1c|egfr|acr|labs?|x-?ray|tender|clear to|pulses|rash|edema|murmur|rapid strep|exudate|lymph ?nodes|nodes|sensation|bmi|weight \d|feet normal|no sores)\b/i;
    var put = function (k, txt) { txt = String(txt || '').trim().replace(/^[,.;:\s-]+/, ''); if (txt) f[k].push(txt); };
    var sentences = function (txt) { return String(txt).split(/(?<=[.!?])\s+|\n+/).map(function (x) { return x.trim(); }).filter(Boolean); };
    var classify = function (txt) { sentences(txt).forEach(function (x) { put(DX(x) ? 'assessment' : HX.test(x) && !OBJ.test(x) ? 'hpi' : PLAN.test(x) ? 'plan' : OBJ.test(x) ? 'exam' : 'hpi', x); }); };
    var parts = [], last = 0, key = null; HEAD.lastIndex = 0;
    while ((m = HEAD.exec(t))) { parts.push([key, t.slice(last, m.index + m[1].length)]); key = KEY(m[2]); last = HEAD.lastIndex; }
    parts.push([key, t.slice(last)]);
    parts.forEach(function (pt) {
      if (!pt[1].trim()) return;
      if (!pt[0]) return classify(pt[1]);
      if (pt[0] === 'ap') return sentences(pt[1]).forEach(function (x) { put(PLAN.test(x) && !/^\s*\d+[.)]/.test(x) ? 'plan' : 'assessment', x); });
      put(pt[0], pt[1]);
    });
    return { hpi: f.hpi.join(' '), exam: f.exam.join(' '), assessment: f.assessment.join('\n'), plan: f.plan.join(' '), bp: f.bp, hr: f.hr, temp: f.temp, spo2: f.spo2 };
  };

  V.pages.encounter = function () {
    var e = D.enc(V.q.get('enc'));
    if (!e) return chooser();
    var p = D.pt(e.mrn) || { name: '(removed patient)', mrn: e.mrn };
    if (V.seen) V.seen('visit', e.id, e.mrn);
    var chk = { busy: false, err: '' };
    var auto = { busy: false, err: '', timer: null, again: false };
    var tab = V.q.get('tab') || 'note', REC = { on: false, rec: null, start: 0, consent: false }, codeMsg = '', busy = false;
    var signed = function () { return e.status === 'Signed'; };
    var save = function () { e.updated = new Date().toISOString(); D.save(); };
    var stamp = function () { return new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); };
    // the claim this note would produce, checked against every rule
    // the codes the claim would carry: the accepted ones, or until any are accepted, the suggestions not rejected
    function billable() { var acc = e.codes.filter(function (c) { return c.state === 'acc'; }); return acc.length ? acc : e.codes.filter(function (c) { return c.state !== 'rej'; }); }
    function preview() {
      var acc = billable();
      return { encId: e.id, mrn: e.mrn, dos: e.date, prov: e.prov, lines: acc.filter(function (c) { return D.isProc(c.type); }).map(function (c) { return D.lineOf({ code: c.code, mod: c.mod || '', units: 1, tooth: c.tooth, surface: c.surface, quad: c.quad }); }), dx: acc.filter(function (c) { return !D.isProc(c.type); }).map(function (c) { return c.code; }), stage: 'edits', resolved: e.ignored || {} };
    }
    function edits() { var c = preview(); c._enc = V.withAdds(e, e.pendingAdds || []); return D.checkClaim(c).filter(function (x) { return !(e.ignored || {})[x.id]; }); }

    function left() {
      var v = e.vitals || {}, prior = S.encs.filter(function (x) { return x.mrn === e.mrn && x.id !== e.id; }).length;
      var vin = function (k, label, ph) { return '<div><label>' + label + '</label><input data-v="' + k + '" value="' + E(v[k] || '') + '" placeholder="' + ph + '"' + (signed() ? ' readonly' : '') + '></div>'; };
      return U.pane('Chart', '', '', (D.DENTAL ? '<div class="dp-sec"><div class="dp-h">Teeth this visit</div>' + V.odontogram(billable().filter(function (c) { return D.isProc(c.type); }), { small: 1 }) + '</div>'
          : '<div class="dp-sec"><div class="dp-h">Vitals ' + D.us(e.date, 1).slice(0, 5) + '</div><div class="vit-in" style="grid-template-columns:1fr 1fr">' + vin('bp', 'BP', '120/80') + vin('hr', 'HR', 'bpm') + vin('temp', 'Temp', '°F') + vin('wt', 'Weight', 'lb') + vin('spo2', 'SpO2', '%') + '</div></div>') +
        '<div class="dp-sec"><div class="dp-h">' + (D.DENTAL ? 'Medical history' : 'Problems') + ' <span class="end"><a href="#" data-editpt>Edit</a></span></div><div class="lst">' + (V.lines(p.problems).map(function (x) { return '<div>' + E(x) + '</div>'; }).join('') || '<span class="mut">None on file</span>') + '</div></div>' +
        '<div class="dp-sec"><div class="dp-h">Medications</div><div class="lst">' + (V.lines(p.meds).map(function (x) { return '<div>' + E(x) + '</div>'; }).join('') || '<span class="mut">None on file</span>') + '</div></div>' +
        '<div class="dp-sec"><div class="dp-h">Allergies</div><div class="s-red">' + (V.lines(p.allergies).map(E).join('<br>') || '<span class="mut">None recorded</span>') + '</div></div>' +
        '<div class="dp-sec"><div class="dp-h">Visits</div>' + U.kv([['Earlier visits', prior], ['Coverage', E(p.payer || 'None')], [D.L.member, p.member ? '<span class="mono">' + E(p.member) + '</span>' : U.st('Missing', 'amb')]]) + '</div>');
    }
    function noteTab() {
      var ro = signed() ? ' readonly' : '', ed = function (k, label, rows, ph) { return '<h4>' + label + '</h4><textarea class="ed" data-n="' + k + '" rows="' + rows + '" placeholder="' + ph + '"' + ro + '>' + E(e[k] || '') + '</textarea>'; };
      var pend = (e.addenda || []).filter(function (a) { return !a.signed; });
      var bn = signed() ? '<span class="s-grn">Signed by ' + E(e.signedBy) + ', ' + new Date(e.signedAt).toLocaleString('en-US') + '.</span> The note is locked; changes go in an addendum.' :
        (REC.on ? '<span class="rec"><span class="dot"></span>Recording</span> · the transcript fills in as you talk. ' : '') + (SR ? '' : 'This browser can’t record speech; type the note or paste a transcript.');
      if (D.DENTAL) ed = function (k, label, rows, ph) { return '<h4>' + label + '</h4><textarea class="ed" data-n="' + k + '" rows="' + rows + '" placeholder="' + ({ s: 'Reason for visit, history, what the patient reports', o: 'Exam findings: teeth and surfaces, perio charting, X-rays taken and what they show', a: 'Diagnosis per tooth, e.g. caries #14 MO', p: 'Treatment done today (anesthetic, materials) and next visit' })[k] + '"' + ro + '>' + E(e[k] || '') + '</textarea>'; };
      return (bn ? '<div class="banner-note">' + bn + '</div>' : '') +
        (pend.length ? '<div class="dp-sec" style="background:var(--amb-bg)">' + pend.map(function (a, i) { return '<div style="margin-bottom:6px"><b class="s-amb">' + U.warn + 'Addendum waiting for your signature</b> <span class="mut">from ' + E(a.from) + '</span><div style="margin:4px 0">' + E(a.text) + '</div><button class="mini pri" data-sign-add="' + e.addenda.indexOf(a) + '">Sign addendum</button> <button class="mini" data-drop-add="' + e.addenda.indexOf(a) + '">Decline</button></div>'; }).join('') + '</div>' : '') +
        (!signed() ? '<div class="dp-sec" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><label class="chk" style="margin:0"><input type="checkbox" id="consent"' + (REC.consent || REC.on ? ' checked' : '') + (REC.on ? ' disabled' : '') + '> Patient agreed to be recorded</label>' +
          '<button class="btn sm' + (REC.on ? '' : ' pri') + '" id="rec"' + (SR && (REC.consent || REC.on) ? '' : ' disabled') + '>' + (REC.on ? I.pause + 'Stop recording' : I.mic + 'Start recording') + '</button>' +
          '<button class="btn sm" id="fill"' + (e.transcript.length ? '' : ' disabled') + '>Write note from transcript</button><span class="mut" id="fillmsg"></span></div>' : '') +
        '<div class="note">' + ed('s', 'Subjective', 3, 'Chief complaint, HPI, ROS') + ed('o', 'Objective', 3, 'Exam findings, results reviewed') + ed('a', 'Assessment', 3, 'One problem per line') + ed('p', 'Plan', 3, 'Orders, prescriptions, follow-up') +
          ((e.addenda || []).filter(function (a) { return a.signed; }).map(function (a) { return '<h4>Addendum, ' + D.us(a.signedOn) + '</h4><p>' + E(a.text) + '</p>'; }).join('')) + '</div>' +
        U.sect('Orders from this note', e.orders.length + (signed() ? '' : ' pending signature'), ordersTable(), signed() ? '' : '<a href="#" data-tab="orders">Add order</a>') +
        U.sect('Transcript excerpt', e.transcript.length + ' lines', '<div style="padding:8px 12px">' + (e.transcript.slice(-5).map(function (l) { return '<div class="tx-line"><span class="mono">' + l.t + '</span><span>' + E(l.text) + '</span></div>'; }).join('') || '<span class="mut">Nothing recorded yet.</span>') + '</div>');
    }
    function ordersTable() {
      return U.grid(['Type', 'Order', 'Detail', 'Dx', 'Status', ''], e.orders.map(function (o, i) { return { cells: [E(o.type), E(o.name), E(o.detail || ''), E(o.dx || ''), '<span class="' + (o.status === 'Signed' ? 's-grn' : 's-amb') + '">' + o.status + '</span>', signed() ? '' : '<button class="mini" data-del-order="' + i + '">Remove</button>'] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No orders.') + '</tbody>');
    }
    function midBody() {
      if (tab === 'note') return noteTab();
      if (tab === 'transcript') return '<div style="padding:10px 14px">' + (e.transcript.map(function (l, i) { return '<div class="tx-line"' + (l.src === 'command' ? ' style="opacity:.6"' : '') + '><span class="mono">' + l.t + '</span><span style="flex:1">' + (l.src ? '<span class="mut" style="font-size:11.5px">' + E(SRC[l.src] || l.src) + ': </span>' : '') + E(l.text) + '</span>' + (signed() ? '' : '<button class="mini" data-del-tx="' + i + '">×</button>') + '</div>'; }).join('') || '<div class="mut">No transcript yet. Record the visit, or paste or type lines below.</div>') +
        (REC.on ? '<div class="tx-line mut"><span class="mono">' + stamp() + '</span><span id="interim">Listening…</span></div>' : '') +
        (signed() ? '' : '<div style="margin-top:10px"><textarea class="ed" id="txadd" rows="3" placeholder="Paste or type what was said"></textarea><div style="margin-top:6px"><button class="btn sm" id="txaddbtn">Add to transcript</button></div></div>') + '</div>';
      if (tab === 'orders') return ordersTable() + (signed() ? '' : '<div class="dp-sec"><div class="dp-h">Add order</div>' + U.fields([{ k: 'type', label: 'Type', type: 'select', options: ['Rx', 'Lab', 'Imaging', 'Referral', 'Procedure'] }, { k: 'name', label: 'Order', span: 2, ph: 'Losartan 100 mg tab' }, { k: 'dx', label: 'Dx', ph: 'I10' }, { k: 'detail', label: 'Detail', span: 4, ph: '1 daily, #90, 1 refill' }]) + '<div style="margin-top:8px"><button class="btn sm pri" id="addorder">Add order</button></div></div>');
      if (tab === 'history') return U.grid(['Date', 'Provider', 'Type', 'Assessment', 'Status'], S.encs.filter(function (x) { return x.mrn === e.mrn && x.id !== e.id; }).map(function (x) { return { attrs: ' data-href="' + V.encHref(x.id) + '"', cells: [D.us(x.date), E(D.provName(x.prov)), E(x.visitType), E((x.a || '').split('\n')[0].slice(0, 70)), x.status] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'No earlier visits.') + '</tbody>');
      return U.grid(['Name', 'Type', 'Received', 'Status'], S.docs.filter(function (d) { return d.mrn === e.mrn; }).map(function (d) { return { attrs: ' data-href="/v4/emr/documents?mrn=' + e.mrn + '"', cells: [E(d.name), E(d.type), D.us(d.rec), E(d.status)] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(4, 'No documents for this patient.') + '</tbody>');
    }
    function right() {
      var ed = edits(), acc = e.codes.filter(function (c) { return c.state === 'acc'; }), cpt = acc.filter(function (c) { return D.isProc(c.type); }), dx = acc.filter(function (c) { return !D.isProc(c.type); });
      var claim = S.claims.filter(function (c) { return c.encId === e.id; })[0], k = e.check, adds = e.pendingAdds || [];
      var status = auto.busy ? 'Althea is building the claim…' : busy ? 'reading the note…' : e.autoAt ? 'built by Althea · ' + new Date(e.autoAt).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'builds as you write';
      var callouts = ed.filter(function (x) { return !/^BASIC/.test(x.id); }).map(function (x) {
        return '<div class="dp-sec"><div class="edit' + (x.lvl === 'amb' ? ' amb' : '') + '"><div class="edit-h">' + U.mark(x.lvl) + E(x.id) + ' ' + E(x.label) + '</div><div class="edit-b"><p>' + E(x.text) + '</p>' +
          (x.evidence ? '' : '<div class="mut" style="font-size:12px;margin-bottom:6px">Nothing in the note or transcript supports this yet. Document it if it was done.</div>') +
          (signed() ? '' : '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px"><button class="mini" data-ignore="' + E(x.id) + '">Ignore</button></div>') + '</div></div></div>';
      }).join('');
      var used = e.codes.filter(function (c) { return c.state === 'acc'; }).length;
      return '<section class="pane"><div class="ph">Coding <span class="sub">' + status + '</span>' + (signed() ? '' : '<span class="end"><button class="mini" id="getcodes"' + (busy || auto.busy ? ' disabled' : '') + ' title="Althea rebuilds the claim on her own as the note changes; this re-runs it now">Rebuild</button></span>') + '</div><div class="pbody">' +
        '<div class="ms" style="border-bottom:1px solid var(--b2)"><div><span>Claim check</span><b class="s-' + (k ? V.scoreColor(k.score) : 'mut') + '">' + (auto.busy && !k ? '…' : k ? k.score + '%' : '-') + '</b></div><div><span>Est. charge</span><b>' + M(D.sum(cpt, function (c) { return D.fee(c.code) * (c.units || 1); })) + '</b></div><div><span>Edits</span><b class="' + (ed.length ? 's-amb' : 's-grn') + '">' + ed.length + '</b></div><div style="flex:1.5"><span>Payer</span><b title="' + E(p.payer || 'None') + '"' + (String(p.payer || '').length > 12 ? ' style="font-size:13.5px"' : '') + '>' + E(p.payer || 'None') + '</b></div></div>' +
        (codeMsg ? '<div class="banner-note">' + E(codeMsg) + '</div>' : '') +
        V.checkHtml({ id: '__enc', check: k }, auto.busy || chk.busy || auto.retrying || (!k && !auto.err && noteHasText()), chk.err || auto.err).replace('data-runcheck="__enc"', 'id="enccheck"') +
        '<div class="dp-sec cp-open" id="openclaim" title="Open the claim"><div class="dp-h">Claim preview <span class="end"><a href="#" id="openclaim2">Open Claim</a></span> <span class="sub">' + (claim ? V.link(claim.id, V.claimHref(claim.id)) : cpt.length ? 'built by Althea, sent when you sign' : 'builds as you write') + '</span></div>' +
          (D.DENTAL ? U.grid(['Ln', 'CDT', 'Tooth', 'Surface', 'Quad', ['Charge', 'num']], cpt.map(function (c, i) { return { cells: [i + 1, '<b>' + E(c.code) + '</b>', E(c.tooth || ''), E(c.surface || ''), E(c.quad || ''), M(D.fee(c.code) * (c.units || 1))] }; }), { tight: 1 }) :
          U.grid(['Ln', 'CPT', 'Mod', 'Dx Ptr', ['Units', 'num'], ['Charge', 'num']], cpt.map(function (c, i) { return { cells: [i + 1, '<b>' + E(c.code) + '</b>', signed() ? E(c.mod || '') : '<input class="inl" data-mod="' + e.codes.indexOf(c) + '" value="' + E(c.mod || '') + '" style="width:44px">', dx.slice(0, 4).map(function (_, k2) { return String.fromCharCode(65 + k2); }).join(''), c.units || 1, M(D.fee(c.code) * (c.units || 1))] }; }), { tight: 1 })).replace('<tbody></tbody>', '<tbody>' + U.empty(6, auto.busy || busy ? 'Building…' : D.DENTAL ? 'Write the note; Althea codes the procedures from it.' : 'Write the note; Althea builds the claim from it.') + '</tbody>') +
          '<div class="mut" style="margin-top:5px">' + (dx.map(function (c, k2) { return (k2 < 12 ? String.fromCharCode(65 + k2) + ': ' : '') + c.code + (c.desc && c.desc !== c.code ? ' ' + c.desc : ''); }).join(' · ') || 'No diagnosis yet') + '</div>' +
          '<div style="display:flex;justify-content:space-between;margin-top:5px"><span class="mut">Total</span><b>' + M(D.sum(cpt, function (c) { return D.fee(c.code) * (c.units || 1); })) + '</b></div></div>' +
        (adds.length && !signed() ? '<div class="dp-sec"><div class="dp-h">Added to the note when you sign <span class="sub">each quotes the visit record</span></div>' + adds.map(function (a, n) {
          return '<div class="box"><div class="box-h"><span>' + E(a.section) + (a.why ? ' · ' + E(a.why) : '') + '</span><a href="#" data-dropadd="' + n + '">Remove</a></div><div class="box-b prop">' + E(a.text) + '<div class="mut" style="font-size:11.5px;margin-top:3px">Source: “' + E(a.source_quote) + '”</div></div></div>'; }).join('') + '</div>' : '') +
        callouts +
        '<div class="dp-sec"><div class="dp-h">Codes <span class="sub">Althea picked ' + used + ' of ' + e.codes.length + '</span></div><div class="codes-scroll">' +
        U.grid(['Code', 'Description', ['Conf.', 'num'], ''], e.codes.map(function (c, n) {
          return { cells: ['<b>' + E(c.code) + '</b>' + (c.tooth || c.surface || c.quad ? ' <span class="mono">' + E((c.tooth ? '#' + c.tooth : '') + (c.surface ? ' ' + c.surface : '') + (c.quad ? ' ' + c.quad : '')) + '</span>' : '') + '<div class="mut" style="font-size:11px">' + c.type + '</div>', '<div class="wrap">' + E(c.desc) + '</div>' + (c.why ? '<div class="mut wrap" style="font-size:11.5px">' + E(c.why) + '</div>' : ''), c.conf ? '<b class="' + (c.conf < 80 ? 's-amb' : 's-grn') + '">' + c.conf + '%</b>' : '<span class="mut">' + (c.auto ? 'Althea' : 'manual') + '</span>',
            signed() ? (c.state === 'acc' ? '<span class="s-grn">Billed</span>' : '') : '<button class="mini' + (c.state === 'acc' ? ' pri' : '') + '" data-acc="' + n + '" title="' + (c.state === 'acc' ? 'On the claim. Click to take it off.' : 'Not on the claim. Click to add it.') + '">' + (c.state === 'acc' ? '✓ On Claim' : 'Not Used') + '</button>'] };
        }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(4, busy ? 'Reading the note…' : 'Codes appear as you write.') + '</tbody>') + '</div>' +
        (signed() ? '' : D.DENTAL ? '<div style="display:flex;gap:5px;margin-top:6px"><input class="inl" id="mcode" placeholder="CDT (D2392)" style="width:96px"><input class="inl" id="mtooth" placeholder="Tooth" style="width:52px"><input class="inl" id="msurf" placeholder="Surfaces" style="width:66px"><input class="inl" id="mquad" placeholder="Quad" style="width:48px"><button class="mini" id="addcode">Add</button></div>'
          : '<div style="display:flex;gap:5px;margin-top:6px"><input class="inl" id="mcode" placeholder="Code (99213, E11.9)" style="width:120px"><input class="inl" id="mdesc" placeholder="Description"><button class="mini" id="addcode">Add</button></div>') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Checks run <span class="sub">' + E(p.payer || '') + '</span></div><div class="ok-list">' + (ed.map(function (x) { return '<div class="' + (x.lvl === 'red' ? 'e' : 'w') + '">' + U.mark(x.lvl) + E(x.label) + (/^BASIC/.test(x.id) ? ': ' + E(x.text) : '') + '</div>'; }).join('') || '<div>All ' + (6 + D.allRules().length) + ' checks passed</div>') +
          Object.keys(e.ignored || {}).map(function (k3) { return '<div class="w">' + E(k3) + ' ignored: ' + E(e.ignored[k3]) + '</div>'; }).join('') + '</div></div>' +
        '</div></section>';
    }
    function draw() {
      var rec = REC.on ? '<span class="rec"><span class="dot"></span>Virtual SOAP <span id="clock">0:00</span></span>' : signed() ? '<span class="s-grn">' + I.check + 'Note signed</span>' : '<span class="mut">Draft</span>';
      app.innerHTML = U.header('encounter') + V.banner(e.mrn, rec + '<button class="btn" id="switch">Switch patient</button>') +
        U.toolbar(D.L.visit + ' ' + D.us(e.date), [['Edit note', 'pencil', { id: 'editnote' }], ['Orders', 'clip', { id: 'toorders' }], ['Message patient', 'chat', { id: 'msg' }], '|', ['Print', 'print', { id: 'print' }], ['Import CSV', 'upload', { id: 'importcsv' }]],
          '<select class="mini" id="vtype"' + (signed() ? ' disabled' : '') + '>' + D.VISIT_TYPES.concat(D.VISIT_TYPES.indexOf(e.visitType) < 0 && e.visitType ? [e.visitType] : []).map(function (t) { return '<option' + (t === e.visitType ? ' selected' : '') + '>' + E(t) + '</option>'; }).join('') + '</select>' +
          '<select class="mini" id="vprov"' + (signed() ? ' disabled' : '') + '>' + V.provOptions().map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === e.prov ? ' selected' : '') + '>' + E(o[1]) + '</option>'; }).join('') + '</select>') +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr) 470px">' + left() +
          '<section class="pane">' + U.tabs([['note', 'Progress note'], ['transcript', 'Transcript (' + e.transcript.length + ')'], ['orders', 'Orders (' + e.orders.length + ')'], ['history', 'History'], ['docs', 'Documents']], tab) +
            '<div class="pbody" id="midbody">' + midBody() + '</div>' + (signed() ? '<div class="dp-f">' + (S.claims.filter(function (c) { return c.encId === e.id; })[0] ? '<a class="btn" style="display:inline-flex;align-items:center" href="' + V.claimHref(S.claims.filter(function (c) { return c.encId === e.id; })[0].id) + '">Open claim</a>' : '') + '<button class="btn" id="addendum">Add addendum</button></div>'
              : '<div class="dp-f"><button class="btn" id="delete" style="margin-right:auto">Delete draft</button><button class="btn" id="save">Save draft</button><button class="btn pri" id="sign">Sign note and orders</button></div>') + '</section>' +
          right() + '</div>';
      U.fit(); U.search();
      app.querySelectorAll('textarea.ed').forEach(function (t) { t.style.height = Math.max(64, t.scrollHeight + 2) + 'px'; });
    }
    function redrawRight() { var r = app.querySelector('.main > section:last-child'); if (r) r.outerHTML = right();
      if (D.DENTAL) { var l = app.querySelector('.main > section:first-child'); if (l) l.outerHTML = left(); }   // the tooth chart follows the codes (no inputs to lose there)
    }
    V.redraw = draw; draw();

    // typing in the note: save as you go, and re-check rules a moment later
    var t1 = null;
    app.addEventListener('input', function (ev) {
      var n = ev.target.dataset.n, v = ev.target.dataset.v;
      if (n) { e[n] = ev.target.value; ev.target.style.height = Math.max(64, ev.target.scrollHeight + 2) + 'px'; }
      else if (v) { e.vitals[v] = ev.target.value.trim(); }
      else return;
      save(); clearTimeout(t1); t1 = setTimeout(redrawRight, 600); scheduleCodes();
    });
    U.on(app, '[data-tab]', 'click', function (b, ev) { ev.preventDefault(); tab = b.dataset.tab; draw(); });
    U.on(app, '#toorders', 'click', function () { tab = 'orders'; draw(); });
    U.on(app, '#editnote', 'click', function () { tab = 'note'; draw(); var t = app.querySelector('[data-n=s]'); if (t) t.focus(); });
    U.on(app, '#msg', 'click', function () { V.sendMessage(e.mrn); });
    U.on(app, '#switch', 'click', function () { V.pickPatient('Switch patient', function (m) { V.startVisit(m); }); });
    U.on(app, '[data-editpt]', 'click', function (a, ev) { ev.preventDefault(); V.patientForm(p, function () { draw(); }); });
    U.on(app, 'tr[data-href]', 'click', function (tr) { location.href = tr.dataset.href; });
    U.on(app, '#vtype', 'change', function (s) { e.visitType = s.value; save(); redrawRight(); scheduleCodes(300); });
    U.on(app, '#vprov', 'change', function (s) { e.prov = s.value; save(); });

    // recording: the browser's speech recognition, one transcript line per phrase
    U.on(app, '#consent', 'change', function (c) { REC.consent = c.checked; draw(); });
    var tick = null;
    U.on(app, '#rec', 'click', function () {
      if (REC.on) { REC.on = false; V.recording = ''; if (REC.rec) try { REC.rec.stop(); } catch (x) {} clearInterval(tick); D.log('Recording stopped: ' + p.name); draw(); return; }
      if (!SR) return U.toast('This browser can’t record speech. Use Chrome or Edge, or paste a transcript.');
      var r = new SR(); r.lang = 'en-US'; r.continuous = true; r.interimResults = true;
      r.onresult = function (ev) { var interim = ''; for (var i = ev.resultIndex; i < ev.results.length; i++) { var t = ev.results[i][0].transcript.trim(); if (ev.results[i].isFinal && t) { e.transcript.push({ t: stamp(), text: t }); save(); if (tab === 'transcript') draw(); } else interim += t; } var el = document.getElementById('interim'); if (el) el.textContent = interim || 'Listening…'; };
      r.onend = function () { if (REC.on) try { r.start(); } catch (x) {} };
      r.onerror = function (ev) { if (ev.error === 'not-allowed') { REC.on = false; clearInterval(tick); U.toast('Microphone permission was denied.'); draw(); } };
      try { r.start(); } catch (x) {}
      REC.rec = r; REC.on = true; REC.start = Date.now(); V.recording = e.id; D.log('Recording started: ' + p.name);
      tick = setInterval(function () { var s = Math.floor((Date.now() - REC.start) / 1000), c = document.getElementById('clock'); if (c) c.textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }, 1000);
      draw();
    });
    U.on(app, '#txaddbtn', 'click', function () { var t = document.getElementById('txadd').value.trim(); if (!t) return; t.split(/\n+/).forEach(function (l) { if (l.trim()) e.transcript.push({ t: stamp(), text: l.trim() }); }); save(); draw(); });
    U.on(app, '[data-del-tx]', 'click', function (b) { e.transcript.splice(+b.dataset.delTx, 1); save(); draw(); });
    // write the note from the transcript (same service Virtual SOAP uses); fills empty sections only
    U.on(app, '#fill', 'click', function (b) {
      b.disabled = true; var msg = document.getElementById('fillmsg'); msg.textContent = 'Writing…';
      fetch('/api/althea/extract', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target: 'soap_scribe', text: e.transcript.filter(function (l) { return l.src !== 'command'; }).map(function (l) { return l.text; }).join(' ') }) })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j, s: r.status }; }); })
        .then(function (x) {
          if (x.s === 401 || x.s === 403) throw new Error('Log in to Althais on this server (/login) to use note writing.');
          var f = x.ok && !x.j.error && x.j.fields ? x.j.fields : V.splitSoap(e.transcript.filter(function (l) { return l.src !== 'command'; }).map(function (l) { return l.text; }).join('\n')), filled = [];
          var blank = function (k) { return !String(e[k] || '').replace(/Chief complaint:[^.\n]*\.?/i, '').trim(); };
          var put = function (k, val) { if (val && blank(k)) { e[k] = (k === 's' && String(e.s || '').trim() ? e.s.replace(/\s+$/, '') + '\n' : '') + val; filled.push(k.toUpperCase()); } };
          put('s', [f.chief_complaint && 'Chief complaint: ' + f.chief_complaint, f.hpi, f.ros && 'ROS: ' + f.ros, f.pmh && 'PMH: ' + f.pmh, f.medications && 'Medications: ' + f.medications, f.allergies && 'Allergies: ' + f.allergies].filter(Boolean).join('\n'));
          put('o', [f.exam, f.labs && 'Results: ' + f.labs].filter(Boolean).join('\n')); put('a', f.assessment); put('p', f.plan);
          ['bp', 'hr', 'temp', 'spo2'].forEach(function (k) { if (f[k] && !e.vitals[k]) e.vitals[k] = f[k]; });
          save(); draw(); scheduleCodes(300); U.toast(filled.length ? 'Filled ' + filled.join(', ') + '. Review before signing.' : 'Nothing new to fill; sections that already had text were left alone.');
        }).catch(function (err) { b.disabled = false; msg.textContent = err.message; });
    });

    // codes
    // codes update by themselves a moment after the note changes (and on opening a note that has none yet)
    var lastCoded = '', codeTimer = null, fails = 0;
    // Althea builds the claim on her own: picks the codes the note supports, applies payer-rule fixes found in the record,
    // and improves until the claim check is 90% or better. Note additions wait (each quoting its source) until the provider signs.
    function autoKey() { return D.noteText(e) + '|' + e.visitType + '|' + (p.payer || '') + '|' + e.codes.map(function (c) { return c.code + (c.manual ? c.state : ''); }).join(','); }
    function scheduleAuto(force) {
      if (D.DENTAL) return dentalCheck(force);
      clearTimeout(auto.timer); if (signed() || (!e.codes.length && !noteHasText())) return;
      auto.timer = setTimeout(function () {
        if (auto.busy) { auto.again = true; return; }
        var key = autoKey(); if (!force && e.autoKey === key && e.check && e.autoVer === 3) return;   // v3: one-call coding (claim-auto)
        auto.busy = true; auto.err = ''; redrawRight();
        var start = e.codes.filter(function (c) { return !(c.manual && c.state === 'rej'); }).map(function (c) { return { code: c.code, type: c.type, desc: c.desc, mod: c.mod || '', units: c.units || 1, reason: c.why || '' }; });
        V.optimize({ enc: e, codes: start, pin: e.codes.filter(function (c) { return c.manual && c.state === 'acc'; }) }).then(function (b) {
          var picked = {}; b.codes.forEach(function (x) { picked[x.code] = x; });
          e.codes.forEach(function (c) { if (c.manual) return; var x = picked[c.code]; c.state = x ? 'acc' : 'rej'; if (x) { c.mod = x.mod || c.mod || ''; c.units = x.units || 1; if (x.reason) c.why = x.reason; } });
          b.codes.forEach(function (x) { if (!e.codes.some(function (c) { return c.code === x.code; })) e.codes.push({ code: x.code, desc: x.desc || x.code, type: x.type, conf: 0, why: x.reason || '', mod: x.mod || '', units: x.units || 1, state: 'acc', auto: true }); });
          e.pendingAdds = b.adds; e.check = b.check; e.unresolved = b.unresolved; e.autoKey = autoKey(); e.autoVer = 3; e.autoAt = new Date().toISOString(); e.checkKey = '';
          if (b.error && !b.check) { if (/plan or role|Log in/.test(b.error)) auto.err = b.error; else { auto.retrying = true; setTimeout(function () { auto.retrying = false; scheduleAuto(true); }, /usage limit/.test(b.error) ? 60000 : 4000); } }   // not checked yet: try again on its own
          save();
        }).catch(function (x) { if (/plan or role|Log in/.test(x.message)) auto.err = x.message; else { auto.err = ''; setTimeout(function () { scheduleAuto(true); }, /usage limit/.test(x.message) ? 60000 : 4000); } })
          .then(function () { auto.busy = false; redrawRight(); if (auto.again) { auto.again = false; scheduleAuto(); } });
      }, 600);
    }
    // dental: the claim the visit would make, checked by the dental rules on the server (the same as dental.js)
    function dentalCheck(force) {
      clearTimeout(auto.timer); if (signed()) return;
      auto.timer = setTimeout(function () {
        var c = preview(); c.id = 'Visit ' + D.us(e.date); if (!c.lines.length) { e.check = null; redrawRight(); return; }
        var key = V.checkKey(c); if (!force && e.check && e.checkKey === key) return;
        auto.busy = true; auto.err = ''; redrawRight();
        var real = D.save; D.save = function () {};   // the visit's preview claim isn't a claim yet
        V.claimCheck(c, { tries: 4 }).then(function (k) { e.check = k; e.checkKey = key; e.autoAt = new Date().toISOString(); }).catch(function (x) { auto.err = x.message; })
          .then(function () { D.save = real; auto.busy = false; save(); redrawRight(); });
      }, 500);
    }
    var checkTimer = null;
    function scheduleCheck() { scheduleAuto(true); }
    function scheduleCheckOld() {
      clearTimeout(checkTimer); if (signed()) return;
      checkTimer = setTimeout(function () {
        var c = preview(); if (!c.lines.length || chk.busy) return; c.id = 'Visit ' + D.us(e.date);
        var key = V.checkKey(c); if (e.check && e.checkKey === key) return;
        chk.busy = true; chk.err = ''; redrawRight();
        V.claimCheck(c).then(function (k) { e.check = k; e.checkKey = key; save(); }).catch(function (x) { chk.err = x.message; }).then(function () { chk.busy = false; redrawRight(); });
      }, 1200);
    }
    function codeKey() { return D.noteText(e) + '|' + e.visitType + '|' + (p.payer || ''); }
    function noteHasText() { return [e.s, e.o, e.a, e.p].join('\n').replace(/Chief complaint:[^.\n]*\.?/i, '').trim().length >= 12; }   // only the pre-filled chief complaint doesn't count
    function scheduleCodes(ms) { clearTimeout(codeTimer); if (signed() || !noteHasText()) return; codeTimer = setTimeout(function () { runCodes(false); }, ms == null ? 2000 : ms); }
    function runCodes(force) {
      if (signed()) return;
      if (!(e.s + e.o + e.a + e.p).trim()) return U.toast('Write the note first.');
      if (busy) { scheduleCodes(1500); return; }
      var key = codeKey(); if (!force && key === lastCoded) return;
      if (noteHasText() && !D.DENTAL) { lastCoded = key; codeMsg = ''; e.codedAt = new Date().toISOString(); scheduleAuto(!!force); return; }   // one call codes, builds and checks the claim
      busy = true; codeMsg = ''; redrawRight();
      fetch('/api/code-note', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: D.noteText(e), encounter_type: 'office', is_new_patient: e.visitType === 'New patient', payer: p.payer || '', specialty: D.DENTAL ? 'general dentistry' : 'primary care / outpatient office' }) })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j, s: r.status }; }); })
        .then(function (x) {
          if (!x.ok || x.j.error) throw new Error(x.s === 401 ? 'Log in to Althais on this server (/login) to get code suggestions. You can add codes by hand below.' : x.s === 403 ? 'Your account’s plan or role doesn’t include coding. Add codes by hand below.' : 'Coding is unavailable right now (' + (x.j.error || x.s) + '). Add codes by hand below.');
          var keep = {}; e.codes.forEach(function (c) { keep[c.code + (c.tooth ? '#' + c.tooth : '')] = c; });
          e.codes = (x.j.codes || []).map(function (c) { var old = keep[c.code + (c.tooth ? '#' + c.tooth : '')] || (!c.tooth && keep[c.code]); return { code: c.code, desc: c.description || '', type: /CDT/i.test(c.type || '') ? 'CDT' : /CPT/i.test(c.type || '') ? 'CPT' : 'ICD-10', tooth: c.tooth || '', surface: c.surface || '', quad: c.quad || '',
            conf: Number(c.confidence) || 0, why: (c.justification || '') + (c.documentation_gap ? ' ' + c.documentation_gap : ''), mod: old && old.manual ? old.mod : (c.modifier || ''), state: old ? old.state : D.DENTAL ? 'acc' : '', manual: !!(old && old.manual) }; })
            .concat(e.codes.filter(function (c) { return !c.conf && !(x.j.codes || []).some(function (n) { return n.code === c.code && (n.tooth || '') === (c.tooth || ''); }); }));
          codeMsg = (x.j.codes || []).length ? '' : 'No codes suggested for this note yet.'; lastCoded = key; fails = 0; scheduleAuto(); e.codedAt = new Date().toISOString(); save();
        }).catch(function (err) {
          fails++;
          if (/Log in|plan or role/.test(err.message)) codeMsg = err.message;
          else { codeMsg = ''; busy = true; setTimeout(function () { busy = false; runCodes(true); }, /usage limit|429/.test(err.message) ? 60000 : Math.min(15000, 1000 * Math.pow(2, fails - 1))); }   // keeps trying on its own
        }).then(function () { busy = false; redrawRight(); });
    }
    U.on(app, '#getcodes', 'click', function () { runCodes(true); });
    var _rc = runCodes;
    V.ctx = { enc: e.id };
    // Claim Preview opens the claim: the real one once the note is signed, otherwise the claim as it stands now (CMS-1500)
    U.on(app, '#openclaim', 'click', function (el, ev) {
      if (ev.target.closest('input,button,a:not(#openclaim2)')) return; ev.preventDefault();
      var made = S.claims.filter(function (c) { return c.encId === e.id; })[0];
      if (made) { location.href = V.claimHref(made.id); return; }
      var c = preview(); if (!c.lines.length) return U.toast('Get or accept a ' + D.L.proc + ' code first.');
      c.id = 'Draft, ' + D.us(e.date); c.payer = p.payer; V.claimForm(c);
    });
    U.on(app, '[data-dropadd]', 'click', function (a, ev) { ev.preventDefault(); e.pendingAdds.splice(+a.dataset.dropadd, 1); save(); redrawRight(); scheduleAuto(true); });
    U.on(app, '#enccheck', 'click', function () { scheduleAuto(true); });
    U.on(app, '#enccheck-old', 'click', function () {
      var c = preview(); c.id = 'Visit ' + D.us(e.date); if (!c.lines.length) return U.toast('Get or add a CPT code first.');
      chk.busy = true; chk.err = ''; redrawRight();
      V.claimCheck(c).then(function (k) { e.check = k; save(); }).catch(function (x) { chk.err = x.message; }).then(function () { chk.busy = false; redrawRight(); });
    });
    if (!signed() && !e.codes.some(function (c) { return c.conf; })) scheduleCodes(400);
    else if (!signed()) scheduleAuto();   // codes already here: build the claim   // a note with no suggested codes yet
    U.on(app, '[data-acc]', 'click', function (b) { var c = e.codes[+b.dataset.acc]; c.state = c.state === 'acc' ? 'rej' : 'acc'; c.manual = true; save(); redrawRight(); scheduleCheck(); });
    U.on(app, '[data-rej]', 'click', function (b) { var c = e.codes[+b.dataset.rej]; if (c.state === 'rej' || !c.conf) { if (!c.conf && c.state !== 'rej') { e.codes.splice(+b.dataset.rej, 1); } else c.state = ''; } else c.state = 'rej'; save(); redrawRight(); scheduleCheck(); });
    U.on(app, '#addcode', 'click', function () {
      if (D.DENTAL) {
        var dc = document.getElementById('mcode').value.trim().toUpperCase(), tooth = DX.normTooth(document.getElementById('mtooth').value), surf = DX.normSurface(document.getElementById('msurf').value), quad = document.getElementById('mquad').value.trim().toUpperCase();
        if (!DX.CDT.test(dc)) return U.toast('Enter a CDT code: D and four digits, like D2392.');
        if (tooth && !DX.toothKind(tooth)) return U.toast('Tooth ' + tooth + ' isn\u2019t a tooth number. Use 1 to 32, or A to T for primary teeth.');
        e.codes.push({ code: dc, desc: (S.fees.filter(function (f) { return f.code === dc; })[0] || {}).desc || dc, type: 'CDT', tooth: tooth, surface: surf, quad: quad, conf: 0, why: 'Added by ' + S.clinic.user, state: 'acc', manual: true }); save(); redrawRight(); scheduleCheck(); return;
      }
      var code = document.getElementById('mcode').value.trim().toUpperCase(), desc = document.getElementById('mdesc').value.trim();
      if (!/^([0-9]{5}|[A-Z][0-9]{4}|[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?)$/.test(code)) return U.toast('Enter a CPT (5 digits, or a letter and 4 digits) or an ICD-10 code (E11.9).');
      var cpt = /^([0-9]{5}|[A-Z][0-9]{4})$/.test(code) && !/^[A-Z][0-9]{2}\./.test(code);
      if (!desc && cpt) desc = (S.fees.filter(function (f) { return f.code === code; })[0] || {}).desc || '';
      e.codes.push({ code: code, desc: desc || code, type: cpt ? 'CPT' : 'ICD-10', conf: 0, why: 'Added by ' + S.clinic.user, state: 'acc' }); save(); redrawRight(); scheduleCheck();
    });
    U.on(app, '[data-mod]', 'change', function (i) { e.codes[+i.dataset.mod].manual = true; e.codes[+i.dataset.mod].mod = i.value.trim().toUpperCase(); save(); redrawRight(); scheduleCheck(); });
    // rule edits
    U.on(app, '[data-apply]', 'click', function (b) {
      var x = edits().filter(function (y) { return y.id === b.dataset.apply; })[0]; if (!x) return;
      var k = x.id === 'MCR-CKD-003' ? 'a' : 'o'; e[k] = (e[k] ? e[k].replace(/\s+$/, '') + '\n' : '') + x.addendum; save(); D.log('Applied ' + x.id + ' to note: ' + p.name); draw(); scheduleCodes(300); U.toast('Added to the ' + (k === 'a' ? 'assessment' : 'objective') + '. Logged as approved by ' + S.clinic.user + '.');
    });
    U.on(app, '[data-ignore]', 'click', function (b) { U.ask('Ignore ' + b.dataset.ignore, 'Reason (kept with the claim)', '', function (r) { e.ignored = e.ignored || {}; e.ignored[b.dataset.ignore] = r; save(); redrawRight(); scheduleAuto(true); }, { ok: 'Ignore' }); });
    // orders
    U.on(app, '#addorder', 'click', function () { var v = U.values(app.querySelector('#midbody .dp-sec')); if (!v.name) return U.toast('Enter the order.'); v.status = 'Pending'; e.orders.push(v); save(); draw(); });
    U.on(app, '[data-del-order]', 'click', function (b) { e.orders.splice(+b.dataset.delOrder, 1); save(); draw(); });
    // addenda
    U.on(app, '[data-sign-add]', 'click', function (b) {
      var a = e.addenda[+b.dataset.signAdd]; a.signed = true; a.signedOn = D.today(); a.signedBy = S.clinic.user; save();
      S.claims.filter(function (c) { return c.encId === e.id && c.stage === 'edits'; }).forEach(function (c) { if (c.waitingOn) { delete c.resolved[c.waitingOn]; delete c.waitingOn; } D.scrub(c); D.history(c, 'Addendum signed, claim re-checked: ' + (c.stage === 'ready' ? 'Ready To Submit' : c.edits.length + ' edit(s) remain')); });
      D.log('Addendum signed: ' + p.name); D.save(); draw(); U.toast('Addendum signed. The claim was re-checked.');
    });
    U.on(app, '[data-drop-add]', 'click', function (b) { e.addenda.splice(+b.dataset.dropAdd, 1); S.claims.filter(function (c) { return c.encId === e.id && c.waitingOn; }).forEach(function (c) { delete c.resolved[c.waitingOn]; delete c.waitingOn; D.scrub(c); }); save(); draw(); });
    U.on(app, '#addendum', 'click', function () {
      U.form('Addendum', [{ k: 'text', label: 'Addendum text', type: 'textarea', req: 1, span: 4 }], {}, function (v) { e.addenda.push({ text: v.text, from: S.clinic.user, signed: true, signedOn: D.today(), signedBy: S.clinic.user }); save(); S.claims.filter(function (c) { return c.encId === e.id && (c.stage === 'edits' || c.stage === 'ready'); }).forEach(D.scrub); D.save(); draw(); }, { save: 'Sign addendum' });
    });
    /* ---- Althea on this visit (static/js/althea.js asks window.AlthaisEncounter first) ---- */
    var QUESTION = /\?\s*$|^(how|what|why|where|when|who|which|can|could|does|do|is|are|should|will|would)\b/i;
    var COMMAND = /^(open|show|go|take me|read|scribe|start|stop|end|record|check|find|list|tell|sign|submit|accept|reject|switch|help|hey|hi|hello|thanks|thank you)\b/i;
    var CHART = /^(chart|add|note|document|write down|put)\b[:,\s]*(that\s+)?(to the note[:,\s]*)?/i;
    window.AlthaisEncounter = {
      handles: function (intent) { return !signed() && ['scribe_visit', 'dictate_visit_note', 'start_visit', 'start_visit_timer', 'stop_visit_timer'].indexOf(intent) >= 0; },
      run: function (intent) {
        if (intent === 'stop_visit_timer') { if (!REC.on) return { text: 'Nothing is being recorded right now.', spoken: 'Nothing is being recorded.' }; app.querySelector('#rec').click(); return { text: 'Stopped. Use Write note from transcript, then Get codes.', spoken: 'Stopped.' }; }
        if (REC.on) return { text: 'Already recording ' + p.name + '’s visit.', spoken: '', quiet: true };
        if (!SR) return { text: 'This browser can’t record speech. Use Chrome or Edge, or paste the transcript in the Transcript tab.', spoken: '' };
        REC.consent = true; tab = 'note'; draw(); app.querySelector('#rec').click();
        return { quiet: true, spoken: '', text: 'Recording ' + p.name + '’s visit into this note. Make sure the patient agreed to be recorded. Tap Stop recording when it’s over.' };
      },
      wantsChart: function (t) { t = String(t).trim(); return !signed() && (CHART.test(t) || (!QUESTION.test(t) && !COMMAND.test(t) && t.split(/\s+/).length >= 2)); },
      log: function (text, kind) { if (signed()) return; e.transcript.push({ t: stamp(), text: String(text).trim(), src: kind }); save(); if (tab === 'transcript') draw(); },
      chart: function (raw) {
        var text = String(raw).replace(CHART, '').trim();
        return fetch('/api/althea/extract', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target: 'soap_note', text: text.slice(0, 4000) }) })
          .then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; }).then(function (j) {
            var f = j.fields || V.splitSoap(text), put = [], add = function (k, label, val) { val = String(val || '').trim(); if (!val || String(e[k] || '').toLowerCase().indexOf(val.toLowerCase()) >= 0) return; e[k] = (e[k] ? e[k].replace(/\s+$/, '') + '\n' : '') + val; if (put.indexOf(label) < 0) put.push(label); };
            add('s', 'Subjective', [f.chief_complaint && 'Chief complaint: ' + f.chief_complaint, f.hpi, f.ros && 'ROS: ' + f.ros, f.medications && 'Medications: ' + f.medications, f.allergies && 'Allergies: ' + f.allergies].filter(Boolean).join('\n'));
            add('o', 'Objective', [f.exam, f.labs && 'Results: ' + f.labs].filter(Boolean).join('\n')); add('a', 'Assessment', f.assessment); add('p', 'Plan', f.plan);
            ['bp', 'hr', 'temp', 'spo2'].forEach(function (k) { if (f[k]) { e.vitals[k] = f[k]; if (put.indexOf('Vitals') < 0) put.push('Vitals'); } });
            if (!put.length) { e.s = (e.s ? e.s.replace(/\s+$/, '') + '\n' : '') + text; put.push('Subjective'); }   // couldn't sort it: keep it, in Subjective
            save(); tab = 'note'; draw(); scheduleCodes(300);
            return { text: 'Added to ' + put.join(', ') + '.', spoken: 'Added to ' + put.slice(0, 3).join(', ') + '.' };
          });
      },
    };
    U.on(app, '#save', 'click', function () { save(); D.log('Draft saved: ' + p.name); U.toast('Draft saved, ' + stamp() + '.'); });
    U.on(app, '#delete', 'click', function () {
      U.confirm('Delete Draft Visit', '<p class="cf">Delete this draft visit for ' + E(p.name) + '? This can’t be undone.</p>', 'Delete Draft', function () {
      S.encs.splice(S.encs.indexOf(e), 1); S.appts.forEach(function (a) { if (a.encId === e.id) { a.encId = ''; if (a.visit === 'In room') a.visit = 'Checked in'; } }); D.log('Draft deleted: ' + p.name); D.save(); location.href = '/v4/emr/schedule';
      }, { danger: 1 });
    });
    U.on(app, '#sign', 'click', function () {
      if (REC.on) app.querySelector('#rec').click();
      if (!(e.s + e.o + e.a + e.p).trim()) return U.toast('The note is empty.');
      if (auto.busy && !e.autoAt) return U.toast('Althea is still building the claim. One moment.');   // once built, a background re-check never blocks signing
      V.signVisit(e, draw);
    });
  };

  // no visit chosen: today's appointments and any open drafts
  function chooser() {
    var today = S.appts.filter(function (a) { return a.date === D.today() && ['Cancelled', 'No show'].indexOf(a.visit) < 0; }).sort(function (a, b) { return a.time.localeCompare(b.time); });
    var drafts = S.encs.filter(function (x) { return x.status === 'Draft'; });
    app.innerHTML = U.header('encounter') + U.toolbar('Encounter', [['Start Visit', 'mic', { pri: 1, id: 'start' }], ['New Patient', 'user', { id: 'newpt' }]], 'Choose a visit') +
      '<div class="main" style="grid-template-columns:1fr 1fr">' +
        U.pane('Today’s appointments', today.length, '', U.grid(['Time', 'Patient', 'Reason', 'Provider', 'Status'], today.map(function (a) { return { attrs: ' data-start="' + a.mrn + '" data-appt="' + a.id + '"', cells: [V.fmtT(a.time), E(D.ptName(a.mrn)), E(a.reason || ''), E(D.provName(a.prov)), V.apptStatus(a)] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'No appointments today. Use Start Visit to pick any patient.') + '</tbody>')) +
        U.pane('Open drafts', drafts.length, '', U.grid(['Date', 'Patient', 'Provider', 'Reason'], drafts.map(function (x) { return { attrs: ' data-href="' + V.encHref(x.id) + '"', cells: [D.us(x.date), E(D.ptName(x.mrn)), E(D.provName(x.prov)), E(x.reason || '')] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(4, 'None.') + '</tbody>')) + '</div>';
    U.fit(); U.search();
    U.on(app, 'tr[data-start]', 'click', function (tr) { V.startVisit(tr.dataset.start, tr.dataset.appt); });
    U.on(app, 'tr[data-href]', 'click', function (tr) { location.href = tr.dataset.href; });
    U.on(app, '#start', 'click', function () { V.pickPatient('Start visit for', function (m) { V.startVisit(m); }); });
    U.on(app, '#newpt', 'click', function () { V.patientForm(null, function (pt) { V.startVisit(pt.mrn); }); });
  }
})();
