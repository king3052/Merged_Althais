/* v4 working preview, part 3: Claims, Denials, Payer rules, Team, Settings, and the page runner. */
(function () {
  var app = V.app, E = U.esc, M = U.money, S = D.S, q = V.q;
  var CARCS = [['CO-4', 'Procedure inconsistent with modifier'], ['CO-5', 'Place of service'], ['CO-11', 'Dx/procedure mismatch'], ['CO-16', 'Missing information'], ['CO-18', 'Duplicate claim'], ['CO-22', 'Coordination of benefits'], ['CO-27', 'Coverage terminated'],
    ['CO-29', 'Timely filing'], ['CO-50', 'Medical necessity'], ['CO-97', 'Bundled'], ['CO-109', 'Not covered by this payer'], ['CO-197', 'Prior auth absent'], ['PR-1', 'Deductible'], ['PR-2', 'Coinsurance']].concat(D.DENTAL ? [['CO-119', 'Benefit maximum reached'], ['CO-252', 'Attachment required'], ['CO-B4', 'Late filing penalty'], ['PR-3', 'Copayment'], ['CO-96', 'Non-covered charge (frequency limit)']] : []);
  var PREVENT = { 'CO-197': 'Prior auth order check', 'CO-11': 'Diagnosis check while charting', 'CO-5': 'Telehealth location rule', 'CO-16': 'Missing-info edits before submission', 'CO-50': 'Note check while charting', 'CO-97': 'Bundling check', 'CO-29': 'Timely filing deadline edit', 'CO-4': 'Modifier review' };
  var findClaim = function (id) { return D.claim(id); };
  var lineCodes = function (c) { return c.lines.map(D.lineText).join(', '); };   // dental: "D2392 #14 MO"

  /* ================= Claims ================= */
  V.pages.claims = function () {
    var Q = q.get('q') || 'edits', sel = q.get('claim') || '', tab = 'edits', CHK = {}, F = { dos: 'month', prov: '', payer: '', edit: '' };
    if (sel && findClaim(sel)) Q = findClaim(sel).stage;
    else if (!q.get('q') && S.encs.some(function (e) { return e.status === 'Draft' && e.codes.some(function (c) { return c.state === 'acc' && D.isProc(c.type); }); })) Q = 'signoff';
    var QS = { edits: function (c) { return c.stage === 'edits'; }, ready: function (c) { return c.stage === 'ready'; }, submitted: function (c) { return c.stage === 'submitted'; },
      stale: function (c) { return c.stage === 'submitted' && D.daysBetween(c.submittedOn || D.today(), D.today()) > 30; }, rejected: function (c) { return c.stage === 'submitted' && /reject/i.test(c.status || ''); }, denied: function (c) { return c.stage === 'denied'; },
      signoff: function () { return false; }, appeal: function (c) { return c.stage === 'denied' && c.dstatus !== 'Appealed' && D.daysBetween(D.today(), c.appealBy) <= 7; }, paid: function (c) { return c.stage === 'paid'; }, all: function () { return true; } };
    var TITLES = { signoff: 'Waiting For Sign-Off', edits: 'Edits To Resolve', ready: 'Ready To Submit', submitted: 'With Payer', stale: 'Submitted over 30 days', rejected: 'Rejected by clearinghouse', denied: 'Denied', appeal: 'Appeal due in 7 days', lag: 'Charges not entered', paid: 'Paid', all: 'All claims' };
    function row(c) {
      var st = c.stage === 'edits' ? (c.waitingOn ? '<span class="s-blue">Waiting on addendum</span>' : U.st((c.edits[0] || {}).label || 'Edit', (c.edits[0] || {}).lvl || 'amb') + (c.edits.length > 1 ? ' <span class="mut">+' + (c.edits.length - 1) + '</span>' : ''))
        : c.stage === 'ready' ? '<span class="s-grn">Passed (' + c.passed + ')</span>' + (c.queuedAt ? ' <span class="tc s-blue">approved · Althais submits ' + V.batchTime(c.submitAt) + '</span>' : '') : V.claimStatus(c);
      if (c.check && ['edits', 'ready'].indexOf(c.stage) >= 0) st += ' <span class="s-' + V.scoreColor(c.check.score) + '" title="Claim check">' + c.check.score + '%</span>';
      if (c.proposal && c.proposal.changed && c.proposal.score > (c.check ? c.check.score : 0) && ['edits', 'ready'].indexOf(c.stage) >= 0) st += ' <span class="s-grn" title="Althea built a version that checks at ' + c.proposal.score + '%. Open the claim to approve it.">→ ' + c.proposal.score + '% ready to approve</span>';
      else if (V.proposalBusy(c.id)) st += ' <span class="mut">Althea building…</span>';
      return { sel: c.id === sel, checked: CHK[c.id], attrs: ' data-claim="' + c.id + '"', cells: ['<a href="#">' + c.id + '</a>', E(D.ptName(c.mrn)), '<span class="mono s-mut">' + c.mrn + '</span>', D.us(c.dos, 1), E(D.provName(c.prov)), E(D.payer(c)), E(lineCodes(c))].concat(D.DENTAL ? [] : [c.dx.join(', ')]).concat([st, M(D.charge(c)), Math.max(0, D.daysBetween(c.dos || D.today(), D.today()))]) };
    }
    var COLS = function (k) { return ['Claim #', 'Patient', 'MRN', 'DOS', D.L.provider, 'Payer', [D.DENTAL ? 'Procedures' : 'CPT', 'wrap']].concat(D.DENTAL ? [] : [['Dx', 'wrap']]).concat([[k, 'wrap'], ['Charges', 'num'], ['Age', 'num']]); };
    var fOK = function (c) {
      var age = D.daysBetween(c.dos || D.today(), D.today());
      return (F.dos === '' || (F.dos === 'month' ? (c.dos || '').slice(0, 7) === D.today().slice(0, 7) : F.dos === '30' ? age <= 30 : age <= 90) || ['edits', 'ready'].indexOf(c.stage) >= 0 && F.dos === 'month')
        && (!F.prov || c.prov === F.prov) && (!F.payer || D.payer(c) === F.payer) && (!F.edit || (c.edits || []).some(function (x) { return x.label === F.edit; }));
    };
    // visits Althea has coded that the provider hasn't signed yet: reviewed and signed here, one at a time or all at once
    var waiting = function () { return S.encs.filter(function (e) { return e.status === 'Draft' && e.codes.some(function (c) { return c.state === 'acc' && D.isProc(c.type); }); }).sort(function (a, b) { return (b.date || '').localeCompare(a.date || ''); }); };
    var vsel = q.get('visit') || '';
    var lag = function () { return S.encs.filter(function (e) { return e.status === 'Signed' && !e.imported && !S.claims.some(function (c) { return c.encId === e.id; }); }); };
    function detail() {
      if (Q === 'signoff') return visitDetail();
      var c = findClaim(sel);
      if (c && V.seen) V.seen('claim', c.id, c.mrn);
      if (!c) return U.pane('Claim detail', '', '', '<div class="blank">' + (S.claims.length ? 'Select a claim.' : 'No claims yet. Claims are created when a provider signs a visit note with an accepted CPT code, or with New claim.') + '</div>');
      var p = D.pt(c.mrn) || {}, e = D.enc(c.encId), body = '', foot = '';
      var lines = D.DENTAL ? V.dentalLines(c) : '<div class="dp-sec"><div class="dp-h">Charge lines' + (['edits', 'ready'].indexOf(c.stage) >= 0 ? '<span class="end"><a href="#" id="addline">Add line</a></span>' : '') + '</div>' +
        U.grid(['Ln', 'CPT', 'Mod', 'Dx ptr', ['Units', 'num'], ['Amount', 'num'], ''], c.lines.map(function (l, i) {
          var edit = ['edits', 'ready'].indexOf(c.stage) >= 0;
          return { cells: [i + 1, edit ? '<input class="inl" data-lc="' + i + '" value="' + E(l.code) + '" style="width:62px">' : l.code, edit ? '<input class="inl" data-lm="' + i + '" value="' + E(l.mod || '') + '" style="width:40px">' : E(l.mod || '—'), c.dx.slice(0, 4).map(function (_, k) { return String.fromCharCode(65 + k); }).join(''),
            edit ? '<input class="inl" type="number" min="1" data-lu="' + i + '" value="' + (l.units || 1) + '" style="width:44px">' : (l.units || 1), M(D.lineFee(l)), edit ? '<button class="mini" data-ldel="' + i + '">×</button>' : ''] };
        }), { tight: 1 }) + '<div class="mut" style="margin-top:5px">' + c.dx.map(function (d, k) { return (k < 4 ? String.fromCharCode(65 + k) + ': ' : '') + d; }).join('  ') + '</div></div>';
      if (tab === 'edits') {
        if (c.stage === 'edits') {
          body = c.edits.map(function (x, i) {
            var hasLevel = c.lines.some(function (l) { return /^992(1[3-5]|0[3-5])$/.test(l.code); }), opts = [];
            if (x.addendum) opts.push(['addendum', 'Send addendum to provider for signature', D.provName(c.prov) + ' signs, then the claim re-checks']);
            if (/^BASIC-(INS|MEM)/.test(x.id)) opts.push(['patient', 'Fix the patient’s insurance', 'Opens the patient record']);
            if (/^BASIC-(CPT|DX)/.test(x.id)) opts.push(['lines', 'Edit the codes', D.DENTAL ? 'Use Procedures' : 'Use Charge lines or Diagnoses']);
            var rc = D.DENTAL && /^(D\d{4}) is a .* Bill (D\d{4}) instead\./.exec(x.text || '');
            if (rc) opts.push(['recode', 'Change ' + rc[1] + ' to ' + rc[2] + (x.tooth ? ' on tooth ' + x.tooth : ''), 'The code that matches the surfaces; fee ' + M(D.fee(rc[2]))]);
            if (/^DEN-(TOOTH|SURF|QUAD|TOOTHTYPE|SAMETOOTH|CODE)/.test(x.id)) opts.push(['lines', 'Edit the procedure lines', 'Fix the tooth, surfaces, quadrant or code']);
            if (/^DEN-ATTACH/.test(x.id)) opts.push(['attached', 'Mark the X-rays and narrative as attached', 'Sent with the claim through the attachment service']);
            if (/^BASIC-FEE/.test(x.id)) opts.push(['fees', 'Add the fee', 'Settings › Fee schedules']);
            if (hasLevel && /UHC-EM|AET-DM/.test(x.id)) opts.push(['downcode', 'Change the visit level one step down', 'Est. charge drops by ' + M(D.fee(c.lines.filter(function (l) { return /^992/.test(l.code); })[0].code) - D.fee(String(+c.lines.filter(function (l) { return /^992/.test(l.code); })[0].code - 1)))]);
            if (!x.addendum && e && !/^BASIC/.test(x.id)) opts.push(['ask', 'Ask the provider to document it', 'Adds a task for ' + D.provName(c.prov)]);
            opts.push(['override', 'Override and submit', 'Reason logged on the claim']);
            return '<div class="dp-sec"><div class="edit' + (x.lvl === 'amb' ? ' amb' : '') + '"><div class="edit-h">' + U.mark(x.lvl) + E(x.id) + ' ' + E(x.label) + '</div><div class="edit-b"><p>' + E(x.text) + '</p>' +
              (x.evidence ? '<div class="b" style="margin:2px 0">Found in ' + E(x.evidence.kind.toLowerCase()) + ' <span class="mut" style="font-weight:400">' + E(x.evidence.when || '') + ', not in note</span></div><div class="box"><div class="box-h"><span>' + E(x.evidence.kind) + '</span></div><div class="box-b">"' + E(x.evidence.quote) + '"</div></div>' : '') +
              (x.addendum ? '<div class="box"><div class="box-h"><span>Proposed note addendum</span></div><div class="box-b prop" data-addtext="' + i + '" contenteditable="true">' + E(x.addendum) + '</div></div><div class="mut" style="font-size:12px">Nothing is added to the note until the provider signs.</div>' : '') +
              '<div class="b" style="margin-top:8px">Resolution</div>' + opts.map(function (o, k) { return '<label class="opt' + (k === 0 ? ' on' : '') + '"><input type="radio" name="res' + i + '" value="' + o[0] + '"' + (k === 0 ? ' checked' : '') + '><span>' + E(o[1]) + '<small>' + E(o[2]) + '</small></span></label>'; }).join('') +
              '<div style="text-align:right;margin-top:6px"><button class="btn sm pri" data-resolve="' + i + '">Apply resolution</button></div></div></div></div>';
          }).join('') + (c.waitingOn ? '<div class="dp-sec"><div class="edit amb"><div class="edit-h" style="border:0">' + U.warn + 'Waiting on ' + D.provName(c.prov) + ' to sign the addendum<span class="end">' + V.link('Open note', V.encHref(c.encId)) + '</span></div></div></div>' : '') + lines;
        } else {
          var pass = (D.DENTAL ? ['Eligibility: dental plan and subscriber ID on file', 'Tooth, surfaces and quadrant on every line that needs them', 'Fee on file for every code'] : ['Eligibility: payer and member ID on file', 'Diagnosis present for every line', 'Fee on file for every code']).concat(D.allRules().map(function (r) { return r.id + ' checked'; }));
          body = '<div class="dp-sec"><div class="dp-h">' + (c.stage === 'denied' ? 'Denial' : c.stage === 'paid' ? 'Payment' : 'Edits passed') + '</div>' +
            (c.stage === 'paid' ? U.kv([['Paid', M(c.paid) + ' on ' + D.us(c.paidOn)], ['Adjustment', M(D.charge(c) - c.paid)], ['Check / EFT', E(c.ref || '-')]]) :
             c.stage === 'denied' ? U.kv([['CARC', c.carc], ['Reason', E(c.reason)], ['Appeal by', D.us(c.appealBy)], ['Status', E(c.dstatus)]]) + '<div style="margin-top:6px">' + V.link('Open in Denials', '/v4/revenue/denials?claim=' + c.id) + '</div>' :
             '<div class="ok-list">' + pass.map(function (x) { return '<div>' + E(x) + '</div>'; }).join('') + Object.keys(c.resolved || {}).map(function (k) { return '<div class="w">' + E(k) + ' overridden: ' + E(c.resolved[k]) + '</div>'; }).join('') + '</div>') + '</div>' + lines;
        }
      } else if (tab === 'charges') body = lines;
      else if (tab === 'dx') body = '<div class="dp-sec">' + U.grid(['Ptr', 'Code', ''], c.dx.map(function (d, k) { return { cells: [k < 4 ? String.fromCharCode(65 + k) : '', d, ['edits', 'ready'].indexOf(c.stage) >= 0 ? '<button class="mini" data-dxdel="' + k + '">Remove</button>' : ''] }; }), { tight: 1 }) +
        (['edits', 'ready'].indexOf(c.stage) >= 0 ? '<div style="display:flex;gap:5px;margin-top:6px"><input class="inl" id="dxadd" placeholder="ICD-10, e.g. E11.9"><button class="mini" id="dxaddb">Add</button></div>' : '') + '</div>';
      else if (tab === 'note') body = e ? '<div class="note"><h4>Subjective</h4><p>' + E(e.s) + '</p><h4>Objective</h4><p>' + E(e.o) + '</p><h4>Assessment</h4><p>' + E(e.a) + '</p><h4>Plan</h4><p>' + E(e.p) + '</p>' + (e.addenda || []).filter(function (a) { return a.signed; }).map(function (a) { return '<h4>Addendum</h4><p>' + E(a.text) + '</p>'; }).join('') + '<p style="margin-top:8px">' + V.link('Open the visit', V.encHref(e.id)) + '</p></div>' : '<div class="blank">Entered by hand, no visit note linked.</div>';
      else body = '<div class="dp-sec kv">' + (c.hist || []).slice().reverse().map(function (h) { return '<div><span>' + new Date(h.at).toLocaleString('en-US', { month: '2-digit', day: '2-digit', hour: 'numeric', minute: '2-digit' }) + '</span><b style="font-weight:400;text-align:right">' + E(h.text) + '</b></div>'; }).join('') + '</div>';
      if (tab === 'edits') { setTimeout(function () { V.autoCheck(c, draw); }, 0); var cs = V.checkState(c.id); body = (!c.encId && !String(c.note || '').trim() && ['edits', 'ready'].indexOf(c.stage) >= 0 ? '<div class="dp-sec"><div class="edit amb"><div class="edit-h">' + U.warn + 'No visit note on this claim</div><div class="edit-b"><p>The claim check and Althea build from the visit documentation, and this claim has none, so it can’t score well. Paste the visit note and Althea builds the claim from it.</p><textarea class="ed" id="pastenote" rows="5" placeholder="Chief complaint, HPI, exam, assessment and plan"></textarea><div style="text-align:right;margin-top:6px"><button class="btn sm pri" id="savenote">Save Note</button></div></div></div></div>' : '') + V.proposalHtml(c) + V.checkHtml(c, cs.busy, cs.err) + body; }
      var tools = '<button class="btn" id="cms">' + U.I.print + 'View ' + D.L.form + '</button>' + (['edits', 'ready'].indexOf(c.stage) >= 0 && !D.DENTAL ? '<button class="btn ft-althea" id="altfix">Althea: Fix To 90%</button>' : '');
      foot = '';
      V.ctx = { claim: c.id };
      if (c.stage === 'ready') foot += c.approvedAt ? '<button class="btn" id="back-edits">Re-Check</button><button class="btn pri" id="submit1">' + U.I.send + 'Submit Now</button>'
        : '<button class="btn" id="back-edits">Re-Check</button><button class="btn" id="submit1">Submit Now</button><button class="btn pri" id="approve1">Sign And Approve</button>';
      else if (c.stage === 'submitted') foot += '<button class="btn" id="ack"' + (/accepted/i.test(c.status) ? ' disabled' : '') + '>Mark accepted (277CA)</button><button class="btn" id="deny">Record denial</button><button class="btn pri" id="pay">Post payment</button>';
      else if (c.stage === 'edits') foot += '<button class="btn" id="rescrub1">Re-check</button>' + (c.encId ? '' : '<button class="btn" id="delclaim">Delete</button>');
      else if (c.stage === 'denied') foot += '<a class="btn pri" style="display:inline-flex;align-items:center" href="/v4/revenue/denials?claim=' + c.id + '">Work denial</a>';
      return '<section class="pane"><div class="ph">Claim detail<span class="end">' + (c.encId ? V.link(D.DENTAL ? 'Open visit' : 'Open encounter', V.encHref(c.encId)) : '') + '</span></div>' +
        '<div class="dp-sec"><div class="dp-title">' + E(p.name || '') + ' <span class="mut" style="font-weight:400;font-size:12.5px">' + E(p.sex || '') + ', ' + D.age(p.dob) + ', DOB ' + D.us(p.dob) + '</span></div>' +
        U.kv3([['Claim #', c.id], ['DOS', D.us(c.dos)], ['Charges', M(D.charge(c))], ['Payer', E(D.payer(c))], [D.DENTAL ? 'Dentist' : 'Rendering', E(D.provName(c.prov))], ['POS', E(D.posLabel(c.pos))]]) + '</div>' +
        U.tabs([['edits', c.stage === 'edits' ? 'Edits' : 'Status', c.stage === 'edits' ? c.edits.length : ''], ['charges', D.DENTAL ? 'Procedures' : 'Charges'], ['dx', D.DENTAL ? 'Diagnoses (optional)' : 'Diagnoses'], ['note', D.DENTAL ? 'Visit note' : 'Encounter note'], ['hist', 'History']], tab) +
        '<div class="pbody">' + body + '</div>' + '<div class="ft">' + (c.stage === 'ready' && c.approvedAt ? '<div class="ft-ok">' + U.I.check + '<div><b>Approved by ' + E(c.approvedBy || 'provider') + '</b><small>' + (c.queuedAt ? 'Althais submits it today at ' + V.batchTime(c.submitAt) + '. Need it sooner? Submit Now.' : 'Queued for Althais’s submission') + '</small></div></div>' : '') + '<div class="ft-tools">' + tools + '</div>' + (foot ? '<div class="ft-acts">' + foot + '</div>' : '') + '</div></section>';
    }
    function signoffGrid(W) {
      return U.grid(['DOS', 'Patient', D.L.provider, 'Payer', [D.DENTAL ? 'Procedures' : 'CPT', 'wrap'], ['Dx', 'wrap'], 'Claim check', ['Est. charge', 'num'], ''], W.map(function (e) {
        var c = V.visitClaim(e), k = e.check;
        return { sel: e.id === vsel, attrs: ' data-visit="' + e.id + '"', cells: [D.us(e.date, 1), E(D.ptName(e.mrn)), E(D.provName(e.prov)), E((D.pt(e.mrn) || {}).payer || ''), E(c.lines.map(D.lineText).join(', ')), c.dx.join(', '),
          k ? '<span class="s-' + V.scoreColor(k.score) + '">' + k.score + '%</span> <span class="tc mut">' + (k.score >= 85 ? 'Ready To Submit' : 'Needs Review') + '</span>' : '<span class="mut">Checking…</span>', M(D.charge(c)), '<button class="mini pri" data-signvisit="' + e.id + '">Review And Sign</button>'] };
      }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(9, 'Nothing waiting. Visits Althea has coded show up here until the provider signs them.') + '</tbody>');
    }
    function visitDetail() {
      var e = D.enc(vsel);
      if (!e) return U.pane('Claim detail', '', '', '<div class="blank">Nothing waiting for sign-off.</div>');
      var p = D.pt(e.mrn) || {}, c = V.visitClaim(e), k = e.check, acc = e.codes.filter(function (x) { return x.state === 'acc'; }), adds = e.pendingAdds || [];
      var col = k ? V.scoreColor(k.score) : 'mut';
      var body = '<div class="dp-sec"><div style="font-size:14px;font-weight:600">' + E(p.name || '') + ' <span class="mut" style="font-weight:400;font-size:12px">' + E(p.sex || '') + ', DOB ' + E(D.us(p.dob || '')) + '</span></div>' +
          U.kv([['Visit', D.us(e.date) + ', ' + E(e.visitType || 'Office visit')], [D.L.provider, E(D.provName(e.prov))], ['Payer', E(p.payer || 'None')], [D.L.member, E(p.member || 'Missing')]]) + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Claim check</div>' + (k ? '<div style="display:flex;align-items:center;gap:12px"><b class="s-' + col + '" style="font-size:22px;font-weight:600">' + k.score + '%</b><div style="flex:1"><div style="height:6px;background:var(--b2)"><div style="height:6px;width:' + k.score + '%;background:var(--' + col + ')"></div></div><div class="mut" style="font-size:12px;margin-top:3px">' + (k.score >= 85 ? 'Ready To Submit' : 'Needs Review') + ' · Checked ' + new Date(k.at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) + '</div></div></div>' : '<div class="mut">Checking…</div>') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Codes <span class="sub">picked by Althea</span></div>' + U.grid(['Code', 'Description'], acc.map(function (x) { return { cells: ['<b class="mono">' + E(D.DENTAL ? DX.lineText(x) : x.code + (x.mod ? '-' + x.mod : '')) + '</b>', '<span class="wrap">' + E(x.desc || '') + '</span>'] }; })) +
          '<div style="display:flex;justify-content:space-between;padding:8px 2px 0"><span class="mut">Estimated charge</span><b>' + M(D.charge(c)) + '</b></div></div>' +
        (adds.length ? '<div class="dp-sec"><div class="dp-h">Added to the note when signed <span class="sub">' + adds.length + ', quoted from the visit</span></div>' + adds.map(function (a) { return '<div style="margin-bottom:6px"><span class="s-blue tc" style="font-size:11.5px">' + E(a.section) + '</span> ' + E(a.text) + '</div>'; }).join('') + '</div>' : '');
      return U.pane('Claim detail', '', V.link('Open note', V.encHref(e.id)), body, { after: '<div class="ft"><div class="ft-acts"><button class="btn" id="vcms" style="margin-right:auto">' + U.I.print + 'View ' + D.L.form + '</button><button class="btn pri" data-signvisit="' + e.id + '">Sign And Approve Claim</button></div></div>' });
    }
    function draw() {
      var list = Q === 'lag' ? [] : S.claims.filter(QS[Q]).filter(fOK).sort(function (a, b) { return (b.dos || '').localeCompare(a.dos || ''); }), n = function (k) { return S.claims.filter(QS[k]).length; }, t = D.totals();
      if (!sel || !findClaim(sel)) sel = list[0] ? list[0].id : (S.claims[0] || {}).id || '';
      var picked = S.claims.filter(function (c) { return CHK[c.id]; });
      var recent = S.claims.filter(function (c) { return c.submittedOn && D.daysBetween(c.submittedOn, D.today()) <= 7; });
      var editTypes = U.uniq([].concat.apply([], D.stage('edits').map(function (c) { return (c.edits || []).map(function (x) { return x.label; }); })));
      var chips = '<div class="fl">' + U.chip('DOS', 'dos', [['month', 'This month'], ['30', 'Last 30 days'], ['90', 'Last 90 days']], F.dos) + U.chip('Provider', 'prov', D.providers().map(function (s) { return [s.id, D.short(s)]; }), F.prov) +
        U.chip('Payer', 'payer', U.uniq(S.claims.map(D.payer)), F.payer) + (Q === 'edits' ? U.chip('Edit type', 'edit', editTypes, F.edit) : '') + '<a class="end" href="#" id="clrf">Clear filters</a></div>';
      var empty = function (txt) { return function (h) { return h.replace('<tbody></tbody>', '<tbody>' + U.empty(12, txt) + '</tbody>'); }; };
      var W = waiting(); if (Q === 'signoff' && (!vsel || !W.some(function (e) { return e.id === vsel; }))) vsel = W[0] ? W[0].id : '';
      var body = Q === 'signoff' ? signoffGrid(W) : Q === 'lag' ? U.grid(['DOS', 'Patient', 'MRN', 'Provider', 'Note', ''], lag().map(function (e) { return { cells: [D.us(e.date, 1), E(D.ptName(e.mrn)), '<span class="mono s-mut">' + e.mrn + '</span>', E(D.provName(e.prov)), 'Signed, no CPT accepted', V.link('Open note', V.encHref(e.id))] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'Every signed note has a claim.') + '</tbody>')
        : empty(S.claims.length ? 'None in this queue.' : 'No claims yet. Sign a visit note with an accepted CPT code, or use New claim.')(U.grid(COLS(Q === 'edits' ? 'Edit' : Q === 'ready' ? 'Edits' : 'Status'), list.map(row), { cb: 1, click: 1, selChecks: false }));
      var nPick = function () { return S.claims.filter(function (c) { return CHK[c.id] && c.stage === 'ready'; }).length; };
      var mid = '<div class="ph">' + TITLES[Q] + (Q === 'ready' && n('ready') ? '<span class="end">' + '<button class="mini pri" id="subnow">' + U.I.send + 'Submit Now' + (nPick() ? ' (' + nPick() + ')' : '') + '</button>' + '</span>' : '') + '</div>' + (Q === 'lag' ? '' : chips) + '<div class="pbody">' + body +
        (Q === 'edits' ? U.sect('Ready To Submit', '', empty('None ready yet.')(U.grid(COLS('Edits'), D.stage('ready').filter(fOK).map(row), { cb: 1, click: 1, selChecks: false })), n('ready') ? '<button class="mini pri" id="subnow">' + U.I.send + 'Submit Now' + (nPick() ? ' (' + nPick() + ')' : '') + '</button>' : '') : '') +
        ((Q === 'edits' || Q === 'ready') ? U.sect('Recently submitted', 'last 7 days', empty('Nothing submitted in the last 7 days.')(U.grid(COLS('Status'), recent.map(row), { cb: 1, click: 1, selChecks: false }))) : '') +
        '</div><div class="pf"><span id="nsel">' + picked.length + ' selected</span><span id="ssel">Selected charges <b>' + M(D.sum(picked, D.charge)) + '</b></span><span class="end">Showing ' + list.length + ' of ' + S.claims.length + '</span></div>';
      if (Q === 'signoff') mid = '<div class="ph">' + TITLES.signoff + ' <span class="sub">' + W.length + ' visit' + (W.length === 1 ? '' : 's') + ', ' + M(D.sum(W, function (e) { return D.charge(V.visitClaim(e)); })) + ', coded by Althea</span>' +
        (W.length ? '<span class="end"><button class="mini pri" id="signall">Sign All (' + W.length + ')</button></span>' : '') + '</div><div class="pbody">' + body + '</div>';
      app.innerHTML = U.header('claims') +
        U.toolbar('Claims', [['Submit selected', 'send', { pri: 1, id: 'subsel' }], ['New claim', 'plus', { id: 'newclaim' }], ['Re-scrub', 'check', { id: 'rescrub' }], '|', ['Print', 'print', { id: 'print' }], ['Export', 'download', { id: 'export' }]].concat(D.DENTAL ? [] : [['Import 837', 'upload', { id: 'imp' }]]),
          '') +
        '<div class="main" style="grid-template-columns:200px minmax(0,1fr) 400px">' +
          U.pane('Work queues', '', '', U.queue([
            { h: 'Before submission', items: [{ key: 'signoff', label: 'Waiting For Sign-Off', n: waiting().length, color: 'blue' }, { key: 'edits', label: 'Edits To Resolve', n: n('edits'), color: 'red' }, { key: 'ready', label: 'Ready To Submit', n: n('ready'), color: 'blue' }, { key: 'lag', label: 'Charges not entered', n: lag().length, color: 'gray' }] },
            { h: 'With Payer', items: [{ key: 'submitted', label: 'Submitted', n: n('submitted'), color: 'lblue' }, { key: 'stale', label: 'Over 30 days', n: n('stale'), color: null, indent: 1 }, { key: 'rejected', label: 'Rejected by clearinghouse', n: n('rejected'), color: 'amb' }] },
            { h: 'After adjudication', items: [{ key: 'denied', label: 'Denied', n: n('denied'), color: 'red' }, { key: 'appeal', label: 'Appeal due in 7 days', n: n('appeal'), color: null, indent: 1 }, { key: 'paid', label: 'Paid', n: n('paid'), color: 'grn' }, { key: 'balance', label: 'Patient balance', n: S.patients.filter(function (p) { return p.balance > 0; }).length, color: 'gray' }] },
            { h: 'All', items: [{ key: 'all', label: 'All claims, ' + new Date().toLocaleDateString('en-US', { month: 'long' }), n: S.claims.length, color: null }] },
          ], Q), { after: U.sum([['Billed MTD', M(t.billed)], ['Collected MTD', M(t.collected)], ['First-pass rate', t.r90 ? U.pct(t.firstPass) : '-'], ['Avg days to pay', t.avgPay == null ? '-' : t.avgPay.toFixed(1)]]) }) +
          '<section class="pane">' + mid + '</section>' + detail() + '</div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    // background: check every claim before submission that hasn't been checked in its current form, one at a time
    (function bg() { var c = S.claims.filter(function (x) { return ['edits', 'ready'].indexOf(x.stage) >= 0 && x.lines.length && (!x.check || x.checkKey !== V.checkKey(x)) && !V.checkState(x.id).err && !V.checkState(x.id).busy; })[0];
      if (!c) {   // everything is checked: build the ones under 90%
        var low = S.claims.filter(function (x) { return ['edits', 'ready'].indexOf(x.stage) >= 0 && x.lines.length && x.check && x.check.score < 90 && !(x.proposal && x.proposal.key === V.checkKey(x)) && !V.proposalBusy(x.id); })[0];
        if (!low) { setTimeout(bg, 4000); return; }
        V.proposeFor(low, function () { draw(); }); setTimeout(bg, 1500); return;
      }
      V.autoCheck(c, function () { draw(); setTimeout(bg, 800); }); })();
    // claims Althais submitted: their latest clearinghouse and payer status, read from the server (Claim.MD responses)
    (function track() {
      S.claims.filter(function (c) { return c.stage === 'ready' && !c.imported && c.approvedAt && (!c.queuedAt || c.queueKey !== V.checkKey(c)); }).slice(0, 10).forEach(function (c) { V.queueClaim(c, function () { draw(); }); });
      var list = S.claims.filter(function (c) { return (c.stage === 'submitted' || (c.stage === 'ready' && c.queuedAt)) && !c.imported && (!c.trackedAt || Date.now() - c.trackedAt > 120000); }).slice(0, 15);
      list.forEach(function (c) {
        c.trackedAt = Date.now();
        fetch('/api/billing/claims/' + encodeURIComponent(c.id), { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (t) {
          if (t && t.delivery === 'HELD' && c.stage === 'ready') { var hr = (t.blockers || []).map(function (b) { return b.title; }).join('; '); if (c.status !== 'Held: ' + hr) { c.status = 'Held: ' + hr; c.heldReasons = t.blockers; D.save(); draw(); } return; }
          if (!t || t.delivery !== 'SENT') return;
          if (c.stage === 'ready') { c.stage = 'submitted'; c.submittedOn = (t.sentAt || D.today()).slice(0, 10); c.ref = t.reference; c.status = 'Submitted by Althais'; D.history(c, 'Submitted by Althais' + (t.reference ? ', ref ' + t.reference : '')); }
          var why = ((t.blockers || [])[0] || {}).reason || '', was = c.status;
          if (t.ack === 'REJECTED') c.status = 'Rejected by clearinghouse' + (why ? ': ' + why : '');
          else if (t.payerStatus === 'REJECTED') c.status = 'Rejected by payer' + (why ? ': ' + why : '');
          else if (t.payerStatus === 'ACCEPTED') c.status = 'Accepted by payer';
          else if (t.ack === 'ACCEPTED') c.status = 'Accepted by clearinghouse';
          if (c.status !== was) { D.history(c, c.status); D.save(); draw(); }
        }).catch(function () {});
      });
      setTimeout(track, 120000);
    })();
    var C = function () { return findClaim(sel); };
    var after = function (c, text) { D.history(c, text); D.log(text + ': ' + c.id); D.save(); draw(); };
    U.on(app, 'tr[data-visit]', 'click', function (tr, ev) { if (ev.target.closest('button,a')) return; vsel = tr.dataset.visit; draw(); });
    U.on(app, '[data-signvisit]', 'click', function (b, ev) { ev.stopPropagation(); var e = D.enc(b.dataset.signvisit); if (!e) return;
      V.signVisit(e, function (made) { if (made && !waiting().length) { Q = made.stage; sel = made.id; } draw(); }); });
    U.on(app, '#approve1', 'click', function () { var c = C(); if (!c) return;
      var k = c.check, col = k ? V.scoreColor(k.score) : 'mut', p = D.pt(c.mrn) || {}, at = V.batchTime();
      var row = function (code, desc) { return '<div class="so-code"><b>' + E(code) + '</b><span>' + E(desc || '') + '</span></div>'; };
      var snap = V.claimSnap(c);
      var body = '<div class="so">' +
        '<div class="so-who">Approving as <b>' + E(S.clinic.user) + '</b> · ' + E(c.id) + '</div>' +
        '<div class="so-sec"><div class="so-h">' + E(D.ptName(c.mrn)) + '<span>' + E(D.payer(c) || 'No payer') + ' · DOS ' + E(D.us(c.dos)) + '</span></div>' +
          (k ? '<div class="so-score"><b class="s-' + col + '">' + k.score + '%</b><div class="so-bar"><i style="width:' + k.score + '%;background:var(--' + col + ')"></i></div><span>' + (k.score >= 85 ? 'Ready To Submit' : k.score >= 65 ? 'Usable, Some Risk' : 'Not Ready') + '</span></div>' : '') +
          '<div class="so-codes">' + snap.cpts.map(function (x) { return row(x.code + (x.modifier ? '-' + x.modifier : ''), x.description); }).join('') + snap.icds.map(function (x) { return row(x.code, x.description); }).join('') + '</div>' +
          '<div class="so-tot"><span>Estimated charge</span><b>' + M(D.charge(c)) + '</b></div></div>' +
        '<div class="so-sec"><div class="so-h">Submission</div><div class="so-sub">' + U.I.send + '<div><b>Althais submits it today at ' + E(at) + '</b><small>With the end-of-day batch, through Althais’s clearinghouse. Track it in Claim Tracker.</small></div></div></div>' +
        '<div class="so-foot">Approving is recorded with your name and the time. Need it out sooner? Use Submit Now.</div></div>';
      var approve = function (now) {
        c.approvedAt = new Date().toISOString(); c.approvedBy = S.clinic.user; D.history(c, 'Signed and approved by ' + S.clinic.user); D.save();
        V.queueClaim(c, function () { draw(); if (!now) U.toast(c.id + ' approved. Althais submits it today at ' + at + '.'); if (now) V.transmit([c], function () { draw(); }); });
        draw();
      };
      U.modal('Approve Claim', body, [['Cancel', '', function () {}], ['Approve And Submit Now', '', function () { approve(true); }], ['Sign And Approve', 'pri', function () { approve(false); }]], { width: 540 });
    });
    U.on(app, '#vcms', 'click', function () { var e = D.enc(vsel); if (!e) return; var c = V.visitClaim(e); c.id = 'Draft, ' + D.us(e.date); c.payer = (D.pt(e.mrn) || {}).payer; V.claimForm(c); });
    U.on(app, '#signall', 'click', function () { var W = waiting(); if (!W.length) return;
      var tot = D.sum(W, function (e) { return D.charge(V.visitClaim(e)); });
      U.confirm('Sign All', '<p class="cf">Sign ' + W.length + ' visit note' + (W.length === 1 ? '' : 's') + ' as ' + E(S.clinic.user) + ' and approve ' + (W.length === 1 ? 'its claim' : 'their claims') + ' (' + M(tot) + ')? Althea’s note additions are added to each note. Each signature is recorded with your name and the time.</p>', 'Sign All (' + W.length + ')', function () {
        var made = 0; W.forEach(function (e) { V.signVisit(e, function (c) { if (c) made++; }, { silent: 1 }); });
        Q = 'ready'; sel = ''; draw(); U.toast('Signed ' + W.length + ' note' + (W.length === 1 ? '' : 's') + '. ' + made + ' claim' + (made === 1 ? '' : 's') + ' created.');
      }); });
    U.on(app, '[data-q]', 'click', function (el) { if (el.dataset.q === 'balance') { location.href = '/v4/emr/patients?list=balance'; return; } Q = el.dataset.q; sel = ''; tab = 'edits'; draw(); });
    U.on(app, '[data-f]', 'change', function (s) { F[s.dataset.f] = s.value; draw(); });
    U.on(app, '#clrf', 'click', function (a, e) { e.preventDefault(); F = { dos: '', prov: '', payer: '', edit: '' }; draw(); });
    U.on(app, 'tr[data-claim]', 'click', function (tr, e) {
      if (e.target.matches('input[type=checkbox]')) { CHK[tr.dataset.claim] = e.target.checked; var pk = S.claims.filter(function (c) { return CHK[c.id]; }); document.getElementById('nsel').textContent = pk.length + ' selected'; document.getElementById('ssel').innerHTML = 'Selected charges <b>' + M(D.sum(pk, D.charge)) + '</b>'; subLabel(); return; }
      e.preventDefault(); sel = tr.dataset.claim; tab = 'edits'; draw();
    });
    U.on(app, '[data-tab]', 'click', function (b) { tab = b.dataset.tab; draw(); });
    U.on(app, '.opt', 'click', function (l) { l.parentNode.querySelectorAll('.opt').forEach(function (o) { o.classList.toggle('on', o === l); }); });
    // line edits
    var lineChange = function (attr, fn) { U.on(app, '[' + attr + ']', 'change', function (i) { var c = C(); fn(c, +i.getAttribute(attr), i.value.trim()); D.scrub(c); after(c, 'Charge lines edited'); }); };
    lineChange('data-lc', function (c, i, v) { c.lines[i].code = v.toUpperCase(); delete c.lines[i].charge; }); lineChange('data-lm', function (c, i, v) { c.lines[i].mod = v.toUpperCase(); }); lineChange('data-lu', function (c, i, v) { c.lines[i].units = Math.max(1, parseInt(v, 10) || 1); });
    U.on(app, '[data-ldel]', 'click', function (b) { var c = C(); c.lines.splice(+b.dataset.ldel, 1); D.scrub(c); after(c, 'Charge line removed'); });
    U.on(app, '#addline', 'click', function (a, e) { e.preventDefault(); if (D.DENTAL) return V.addDentalLine(C(), function (c, text) { D.scrub(c); after(c, text); }); U.ask('Add Charge Line', 'CPT / HCPCS code', '', function (code) { var c = C(); c.lines.push({ code: code.trim().toUpperCase(), mod: '', units: 1 }); D.scrub(c); after(c, 'Charge line added ' + code); }, { ok: 'Add Line', ph: 'e.g. 36415' }); });
    U.on(app, '[data-dxdel]', 'click', function (b) { var c = C(); c.dx.splice(+b.dataset.dxdel, 1); D.scrub(c); after(c, 'Diagnosis removed'); });
    U.on(app, '#dxaddb', 'click', function () { var v = document.getElementById('dxadd').value.trim().toUpperCase(); if (!/^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$/.test(v)) return U.toast('Enter an ICD-10 code like E11.9.'); var c = C(); c.dx.push(v); D.scrub(c); after(c, 'Diagnosis added ' + v); });
    // resolutions
    U.on(app, '[data-resolve]', 'click', function (b) {
      var c = C(), i = +b.dataset.resolve, x = c.edits[i], v = (app.querySelector('input[name=res' + i + ']:checked') || {}).value, e = D.enc(c.encId);
      if (v === 'addendum') {
        var text = (app.querySelector('[data-addtext="' + i + '"]') || {}).textContent || x.addendum;
        e.addenda = e.addenda || []; e.addenda.push({ text: text.trim(), from: 'Billing (' + x.id + ')', signed: false }); c.resolved = c.resolved || {}; c.resolved[x.id] = 'Addendum sent for signature'; c.waitingOn = x.id; c.stage = 'edits';
        c.edits = c.edits.filter(function (y) { return y.id !== x.id; }); return after(c, 'Addendum for ' + x.id + ' sent to ' + D.provName(c.prov));
      }
      if (v === 'patient') return location.href = V.chartHref(c.mrn);
      if (v === 'lines') { tab = /DX/.test(x.id) ? 'dx' : 'charges'; return draw(); }
      if (v === 'fees') return location.href = '/v4/settings?s=fs';
      if (v === 'recode') { var m2 = /^(D\d{4}) is a .* Bill (D\d{4}) instead\./.exec(x.text), ln = c.lines.filter(function (y) { return y.code === m2[1] && (!x.tooth || DX.normTooth(y.tooth) === x.tooth); })[0];
        if (ln) { ln.code = m2[2]; delete ln.charge; D.scrub(c); return after(c, 'Code changed ' + m2[1] + ' to ' + m2[2] + (x.tooth ? ' on tooth ' + x.tooth : '')); } }
      if (v === 'attached') { c.attached = true; D.scrub(c); return after(c, 'X-rays and narrative marked as attached'); }
      if (v === 'downcode') { var l = c.lines.filter(function (y) { return /^992/.test(y.code); })[0]; var old = l.code; l.code = String(+l.code - 1); delete l.charge; D.scrub(c); return after(c, 'Level changed ' + old + ' to ' + l.code); }
      if (v === 'ask') { e.addenda = e.addenda || []; e.addenda.push({ text: 'Please document: ' + x.text, from: 'Billing (' + x.id + ')', signed: false }); c.waitingOn = x.id; c.resolved = c.resolved || {}; c.resolved[x.id] = 'Asked provider'; c.edits = c.edits.filter(function (y) { return y.id !== x.id; }); return after(c, 'Asked ' + D.provName(c.prov) + ' to document ' + x.id); }
      U.ask('Override ' + x.id, 'Reason (kept on the claim)', '', function (r) { c.resolved = c.resolved || {}; c.resolved[x.id] = r; D.scrub(c); after(c, 'Override ' + x.id + ': ' + r); }, { ok: 'Override', html: '<p class="cf" style="margin-bottom:10px">' + E(x.text || '') + '</p>' });
    });
    var subLabel = function () { var b = document.getElementById('subnow'); if (!b) return; var k = S.claims.filter(function (c) { return CHK[c.id] && c.stage === 'ready'; }).length;
      b.innerHTML = U.I.send + 'Submit Now' + (k ? ' (' + k + ')' : ''); };
    // tick every claim in a table at once
    app.addEventListener('change', function (e) { if (!e.target.matches('[data-cball]')) return; var tb = e.target.closest('table');
      tb.querySelectorAll('tr[data-claim] input[type=checkbox]').forEach(function (cb) { cb.checked = e.target.checked; CHK[cb.closest('tr').dataset.claim] = e.target.checked; });
      var pk = S.claims.filter(function (c) { return CHK[c.id]; }); var ns = document.getElementById('nsel'); if (ns) ns.textContent = pk.length + ' selected'; var ss = document.getElementById('ssel'); if (ss) ss.innerHTML = 'Selected charges <b>' + M(D.sum(pk, D.charge)) + '</b>'; subLabel(); });
    U.on(app, '#subnow', 'click', function (b, e) { e.stopPropagation();
      var l = S.claims.filter(function (c) { return CHK[c.id] && c.stage === 'ready'; });
      if (!l.length && findClaim(sel) && findClaim(sel).stage === 'ready') l = [findClaim(sel)];
      if (!l.length) return U.toast('Select a claim in Ready To Submit, or tick the ones to send.', 'warn');
      submit(l);
    });
    var submit = function (list) {   // the 837P steps, then the server's claim gate decides sent or held
      V.transmit(list, function () { list.forEach(function (c) { CHK[c.id] = false; }); draw(); });
    };
    U.on(app, '#subsel', 'click', function () { var l = S.claims.filter(function (c) { return CHK[c.id]; }); if (!l.length) l = Q === 'ready' ? D.stage('ready') : []; if (!l.length) return U.toast('Check claims in Ready To Submit first.'); submit(l); });
    U.on(app, '#submit1', 'click', function () { submit([C()]); });
    U.on(app, '#cms', 'click', function () { V.claimForm(C()); });
    U.on(app, '#altfix', 'click', function () { V.buildClaim({ claim: C() }, draw); });
    U.on(app, '#savenote', 'click', function () { var t = document.getElementById('pastenote').value.trim(); if (t.length < 20) return U.toast('Paste the visit note.'); var c = C(); c.note = t; c.proposal = null; D.history(c, 'Visit note added to the claim'); D.save(); draw(); });
    U.on(app, '#back-edits, #rescrub1', 'click', function () { var c = C(); D.scrub(c); after(c, 'Re-checked: ' + (c.stage === 'ready' ? 'ready' : c.edits.length + ' edit(s)')); });
    U.on(app, '#rescrub', 'click', function () {
      var l = S.claims.filter(function (c) { return c.stage === 'edits' || c.stage === 'ready'; }); l.forEach(D.scrub); D.log('Re-scrubbed ' + l.length + ' claims'); D.save(); draw();
      U.toast('Re-checked ' + l.length + ' claims against the payer rules. Running the claim check on ' + Math.min(5, l.length) + '…');
      var todo = l.slice(0, 5);
      (function next(i) { if (i >= todo.length) { draw(); var low = todo.filter(function (c) { return c.check && c.check.score < 65; }); if (todo.length) U.toast('Claim check done. ' + (low.length ? low.length + ' below 65%: ' + low.map(function (c) { return c.id; }).join(', ') : 'All look acceptable.')); return; }
        V.claimCheck(todo[i]).catch(function () {}).then(function () { draw(); next(i + 1); }); })(0);
    });
    U.on(app, '#delclaim', 'click', function () { var c = C(); U.confirm('Delete Claim', '<p class="cf">Delete ' + E(c.id) + '? This can’t be undone.</p>', 'Delete Claim', function () { S.claims.splice(S.claims.indexOf(c), 1); D.save(); sel = ''; draw(); }, { danger: 1 }); });
    U.on(app, '#ack', 'click', function () { var c = C(); c.status = 'Accepted 277CA'; after(c, 'Accepted by clearinghouse (277CA)'); });
    U.on(app, '#pay', 'click', function () {
      var c = C(); U.form('Post payment, ' + c.id, [{ k: 'paid', label: 'Paid amount', type: 'number', step: '0.01', req: 1, def: (D.charge(c) * .78).toFixed(2) }, { k: 'date', label: 'Paid on', type: 'date', req: 1, def: D.today() }, { k: 'ref', label: 'Check / EFT #' }, { k: 'pr', label: 'Patient responsibility', type: 'number', step: '0.01', def: '0.00' }], {}, function (v) {
        c.stage = 'paid'; c.paid = +v.paid; c.paidOn = v.date; c.ref = v.ref; var p = D.pt(c.mrn); if (p && +v.pr > 0) p.balance = +(p.balance + +v.pr).toFixed(2); after(c, 'Payment posted ' + M(c.paid));
      }, { save: 'Post payment' });
    });
    U.on(app, '#deny', 'click', function () {
      var c = C(); U.form('Record denial, ' + c.id, [{ k: 'carc', label: 'CARC', type: 'select', options: CARCS.map(function (x) { return [x[0], x[0] + ' ' + x[1]]; }), span: 2 }, { k: 'date', label: 'Denied on', type: 'date', def: D.today() }, { k: 'days', label: 'Appeal window (days)', type: 'number', def: 60 }, { k: 'note', label: 'Payer remark', span: 4 }], {}, function (v) {
        var k = CARCS.filter(function (x) { return x[0] === v.carc; })[0]; c.stage = 'denied'; c.carc = v.carc; c.reason = k[1]; c.remark = v.note; c.deniedOn = v.date; c.appealBy = D.addDays(v.date, +v.days || 60); c.dstatus = 'Needs review'; c.everDenied = true; after(c, 'Denied ' + v.carc);
      }, { save: 'Record denial' });
    });
    U.on(app, '#newclaim', 'click', function () {
      if (!S.patients.length || !D.providers().length) return U.toast('Add a patient and a provider first.');
      U.form('New claim', [{ k: 'mrn', label: 'Patient', type: 'select', options: V.ptOptions(), req: 1, span: 2 }, { k: 'dos', label: 'Date of service', type: 'date', req: 1, def: D.today() }, { k: 'prov', label: D.DENTAL ? 'Treating dentist' : 'Rendering provider', type: 'select', options: V.provOptions(), req: 1 },
        D.DENTAL ? { k: 'cpt', label: 'Procedures: code, tooth, surfaces or quadrant (separate with ; or new lines)', req: 1, span: 4, ph: 'D0120; D2392 #14 MO; D4341 UR' } : { k: 'cpt', label: 'CPT codes (comma separated)', req: 1, span: 2, ph: '99213, 36415' },
        { k: 'dx', label: D.DENTAL ? 'Diagnoses, if the plan asks (optional)' : 'Diagnoses (comma separated)', req: !D.DENTAL, span: D.DENTAL ? 4 : 2, ph: D.DENTAL ? 'K02.52' : 'E11.9, I10' }], {}, function (v) {
        var procs = D.DENTAL ? DX.parseProcs(v.cpt).map(D.lineOf) : v.cpt.split(/[\s,]+/).filter(Boolean).map(function (x) { return { code: x.toUpperCase(), mod: '', units: 1 }; });
        var c = { id: D.nextClaimId(), encId: '', mrn: v.mrn, dos: v.dos, prov: v.prov, payer: (D.pt(v.mrn) || {}).payer, lines: procs, dx: String(v.dx || '').split(/[\s,]+/).filter(Boolean).map(function (x) { return x.toUpperCase(); }), stage: 'edits', resolved: {}, created: new Date().toISOString() };
        D.scrub(c); c.hadEdits = c.stage === 'edits'; S.claims.push(c); sel = c.id; Q = c.stage; D.history(c, 'Entered by hand'); D.log('Claim entered: ' + c.id); D.save(); draw();
      }, { save: 'Create claim', wide: 1 });
    });
    U.on(app, '#export', 'click', function () { U.download('claims-' + D.today() + '.csv', U.csv([['Claim', 'Patient', 'MRN', 'DOS', D.L.provider, 'Payer', D.DENTAL ? 'Procedures' : 'CPT', 'Dx', 'Stage', 'Status', 'Charges', 'Paid']].concat(S.claims.map(function (c) { return [c.id, D.ptName(c.mrn), c.mrn, c.dos, D.provName(c.prov), D.payer(c), lineCodes(c), c.dx.join(' '), c.stage, c.status || c.dstatus || '', D.charge(c).toFixed(2), (c.paid || 0).toFixed(2)]; })))); });
    U.on(app, '#imp', 'click', function () {
      U.pickFile('.837,.txt,.edi,.x12', false, function (f) {
        U.readFile(f[0], true).then(function (txt) {
          var segs = txt.split(/~\s*/), made = 0, name = '', dob = '';
          segs.forEach(function (s) {
            var el = s.split('*');
            if (el[0] === 'NM1' && (el[1] === 'IL' || el[1] === 'QC')) name = (el[3] || '') + ', ' + (el[4] || '');
            if (el[0] === 'DMG') dob = (el[2] || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3');
            if (el[0] === 'CLM') {
              var p = S.patients.filter(function (x) { return x.name.toLowerCase() === name.toLowerCase(); })[0];
              if (!p && name.trim() !== ',') { p = { mrn: D.nextMrn(name), name: name, dob: dob || '1900-01-01', sex: 'X', payer: 'Self-pay', member: '', pcp: '', balance: 0, status: 'New', created: D.today() }; S.patients.push(p); }
              var c = { id: D.nextClaimId(), encId: '', mrn: p ? p.mrn : '', dos: D.today(), prov: (D.providers()[0] || {}).id || '', lines: [], dx: [], stage: 'edits', resolved: {}, ext: el[1] };
              var j = segs.indexOf(s) + 1;
              for (; j < segs.length && segs[j].indexOf('CLM*') !== 0; j++) { var e2 = segs[j].split('*'); if (e2[0] === 'SV1') c.lines.push({ code: (e2[1] || '').split(':')[1] || '', mod: (e2[1] || '').split(':')[2] || '', units: +e2[4] || 1 }); if (e2[0] === 'HI') e2.slice(1).forEach(function (h) { var d = (h.split(':')[1] || ''); if (d) c.dx.push(d.length > 3 ? d.slice(0, 3) + '.' + d.slice(3) : d); }); if (e2[0] === 'DTP' && e2[1] === '472') c.dos = (e2[3] || '').replace(/^(\d{4})(\d{2})(\d{2}).*$/, '$1-$2-$3'); }
              D.scrub(c); D.history(c, 'Imported from 837, patient control # ' + el[1]); S.claims.push(c); made++;
            }
          });
          D.log('Imported ' + made + ' claims from 837'); D.save(); Q = 'all'; draw(); U.toast(made ? made + ' claim' + (made === 1 ? '' : 's') + ' imported and checked.' : 'No CLM segments found in that file.');
        });
      });
    });
  };

  /* ================= Denials ================= */
  V.pages.denials = function () {
    var Q = 'open', sel = q.get('claim') || '';
    var openL = function () { return S.claims.filter(function (c) { return c.stage === 'denied'; }); };
    var match = function (c) { return Q === 'open' || (Q === 'review' && c.dstatus === 'Needs review') || (Q === 'drafted' && c.dstatus === 'Appeal drafted') || (Q === 'appealed' && c.dstatus === 'Appealed') || Q === c.carc; };
    var letterFor = function (c) { var p = D.pt(c.mrn) || {}, prov = D.staff(c.prov) || {}; return 'To ' + D.payer(c) + ' Appeals,\n\nWe request reconsideration of claim ' + c.id + ' for ' + (p.name || '').split(', ').reverse().join(' ') + ', DOS ' + D.us(c.dos) + ', denied ' + c.carc + ' (' + c.reason + ').\n\n[State why the service should be paid and what is enclosed.]\n\nSincerely,\n' + (prov.name ? V.personName(prov.name) + (prov.cred && prov.name.indexOf(prov.cred) < 0 ? ', ' + prov.cred : '') : S.clinic.user) + '\n' + (S.clinic.name || ''); };
    function right() {
      var c = findClaim(sel);
      if (!c || c.stage !== 'denied' && !c.appealOutcome) return U.pane('Appeal', '', '', '<div class="blank">' + (openL().length ? 'Select a denial.' : 'No open denials. Record a denial on a submitted claim in Claims (Record denial).') + '</div>');
      var p = D.pt(c.mrn) || {}, docs = S.docs.filter(function (d) { return d.mrn === c.mrn; }), ro = c.dstatus === 'Appealed' || c.appealOutcome;
      return '<section class="pane"><div class="ph">Appeal <span class="sub">' + c.id + '</span><span class="end">' + V.link('Open claim', V.claimHref(c.id)) + '</span></div><div class="pbody">' +
        '<div class="dp-sec"><div class="dp-title">' + E(p.name || '') + ' <span class="mut" style="font-weight:400;font-size:12.5px">' + E(p.sex || '') + ', ' + D.age(p.dob) + ', MRN ' + c.mrn + '</span></div>' +
          U.kv3([['Payer', E(D.payer(c))], [D.DENTAL ? 'Procedures' : 'CPT', E(lineCodes(c))], ['Billed', M(D.charge(c))], ['Denied', c.carc], ['Appeal by', D.us(c.appealBy), D.daysBetween(D.today(), c.appealBy) <= 14 ? 'amb' : ''], ['Status', E(c.appealOutcome || c.dstatus)]]) + (c.remark ? '<div class="mut" style="margin-top:6px">Payer remark: ' + E(c.remark) + '</div>' : '') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Basis for appeal</div><textarea class="ed" id="basis" rows="3" placeholder="Why this should be paid, one point per line"' + (ro ? ' readonly' : '') + '>' + E(c.basis || '') + '</textarea></div>' +
        '<div class="dp-sec"><div class="dp-h">Letter draft <span class="sub">edit before sending</span>' + (ro ? '' : '<span class="end"><button class="mini" id="ailetter">Draft with AI</button></span>') + '</div><textarea class="ed" id="letter" rows="11"' + (ro ? ' readonly' : '') + '>' + E(c.letter || letterFor(c)) + '</textarea></div>' +
        '<div class="dp-sec"><div class="dp-h">Attachments <span class="end">' + (ro ? '' : '<a href="#" id="attach">Upload</a>') + '</span></div>' + (c.encId ? '<label class="chk" style="margin:0 0 4px"><input type="checkbox" data-att="note"' + ((c.attach || ['note']).indexOf('note') >= 0 ? ' checked' : '') + (ro ? ' disabled' : '') + '> Visit note, ' + D.us(c.dos) + '</label><br>' : '') +
          docs.map(function (d) { return '<label class="chk" style="margin:0 0 4px"><input type="checkbox" data-att="' + d.id + '"' + ((c.attach || []).indexOf(d.id) >= 0 ? ' checked' : '') + (ro ? ' disabled' : '') + '> ' + E(d.name) + '</label><br>'; }).join('') + (!docs.length && !c.encId ? '<span class="mut">No documents for this patient.</span>' : '') + '</div>' +
        '</div><div class="dp-f">' + (c.appealOutcome ? '' : c.dstatus === 'Appealed' ? '<button class="btn" id="upheld">Upheld, write off</button><button class="btn pri" id="overturn">Overturned, post payment</button>' : '<button class="btn" id="resub">Correct and resubmit</button><button class="btn" id="savedraft">Save draft</button><button class="btn" id="apdf">' + U.I.download + 'Appeal claim (PDF)</button><button class="btn pri" id="send">Send appeal</button>') + '</div></section>';
    }
    function draw() {
      var all = openL(), list = all.filter(match), t = D.totals(), carcs = [];
      if (!sel || !findClaim(sel)) sel = list[0] ? list[0].id : '';
      all.forEach(function (c) { if (carcs.indexOf(c.carc) < 0) carcs.push(c.carc); });
      var resolved = S.claims.filter(function (c) { return c.appealOutcome; }), ever = S.claims.filter(function (c) { return c.everDenied; }), byR = {};
      ever.forEach(function (c) { (byR[c.carc] = byR[c.carc] || []).push(c); });
      var due = all.filter(function (c) { return c.dstatus !== 'Appealed' && D.daysBetween(D.today(), c.appealBy) <= 30; });
      app.innerHTML = U.header('denials') +
        U.toolbar('Denials', [['Appeal claim', 'download', { pri: 1, id: 'apdf2' }], ['Send appeal', 'send', { id: 'send2' }], ['Edit appeal', 'pencil', { id: 'editl' }], ['Correct and resubmit', 'refresh', { id: 'resub2' }], '|', ['Print', 'print', { id: 'printl' }], ['Export', 'download', { id: 'export' }]]) +
        U.strip([['Open denials', all.length, M(D.sum(all, D.charge)), all.length ? 'red' : ''], ['Appeals drafted', all.filter(function (c) { return c.dstatus === 'Appeal drafted'; }).length, 'ready to send'], ['Due in 7 days', due.filter(function (c) { return D.daysBetween(D.today(), c.appealBy) <= 7; }).length, M(D.sum(due.filter(function (c) { return D.daysBetween(D.today(), c.appealBy) <= 7; }), D.charge)), due.some(function (c) { return D.daysBetween(D.today(), c.appealBy) <= 7; }) ? 'amb' : ''],
          ['Denial rate, 90 days', t.r90 ? U.pct(t.d90 / t.r90) : '0.0%', t.d90 + ' of ' + t.r90], ['Overturn rate', t.decided ? U.pct(t.overturned / t.decided, 0) : '-', 'last 90 days']]) +
        '<div class="main" style="grid-template-columns:205px minmax(0,1fr) 420px">' +
          U.pane('Denial queues', '', '', U.queue([
            { h: 'Open', items: [{ key: 'open', label: 'All open', n: all.length, color: 'red' }, { key: 'review', label: 'Needs review', n: all.filter(function (c) { return c.dstatus === 'Needs review'; }).length, color: 'amb' }, { key: 'drafted', label: 'Appeal drafted', n: all.filter(function (c) { return c.dstatus === 'Appeal drafted'; }).length, color: 'blue' }, { key: 'appealed', label: 'Appeal sent', n: all.filter(function (c) { return c.dstatus === 'Appealed'; }).length, color: 'gray' }] },
            { h: 'By reason', items: carcs.map(function (k) { var l = all.filter(function (c) { return c.carc === k; }); return { key: k, label: k + ' ' + l[0].reason, n: l.length, color: null }; }) },
            { h: 'Closed', items: [{ key: 'x', label: 'Overturned', n: resolved.filter(function (c) { return c.appealOutcome === 'Overturned'; }).length, color: 'grn' }, { key: 'y', label: 'Written off', n: resolved.filter(function (c) { return c.appealOutcome === 'Written off'; }).length, color: 'gray' }] },
          ], Q)) +
          '<section class="pane"><div class="ph">' + (Q === 'open' ? 'All open' : 'Filtered') + ' <span class="sub">' + list.length + ' claims, ' + M(D.sum(list, D.charge)) + '</span></div><div class="pbody">' +
            U.grid(['Claim #', 'Patient', 'Payer', 'DOS', 'CARC', ['Reason', 'wrap'], ['Amount', 'num'], 'Appeal by', 'Status'], list.map(function (c) {
              return { sel: c.id === sel, attrs: ' data-claim="' + c.id + '"', cells: ['<a href="#">' + c.id + '</a>', E(D.ptName(c.mrn)), E(D.payer(c)), D.us(c.dos, 1), c.carc, E(c.reason), M(D.charge(c)), '<span class="' + (D.daysBetween(D.today(), c.appealBy) <= 14 ? 's-amb' : '') + '">' + D.us(c.appealBy, 1) + '</span>', '<span class="s-' + ({ 'Needs review': 'amb', 'Appeal drafted': 'blue', Appealed: 'mut' }[c.dstatus] || 'none') + '">' + E(c.dstatus === 'Appealed' ? 'Appeal sent ' + D.us(c.appealSent, 1).slice(0, 5) : c.dstatus) + '</span>'] };
            }), { cb: 1, click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(10, 'No open denials.') + '</tbody>') +
            U.sect('Recently resolved', '', U.grid(['Claim #', 'Patient', 'Payer', 'CARC', 'Outcome', ['Recovered', 'num']], resolved.map(function (c) { return { attrs: ' data-claim="' + c.id + '"', cells: [c.id, E(D.ptName(c.mrn)), E(D.payer(c)), c.carc, '<span class="s-' + (c.appealOutcome === 'Overturned' ? 'grn' : 'mut') + '">' + c.appealOutcome + '</span>', M(c.paid || 0)] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'None yet.') + '</tbody>')) +
            U.sect('Denials by reason', ever.length + ' claims ever denied', U.grid(['Reason', ['Claims', 'num'], ['Amount', 'num'], 'Prevention now in place'], Object.keys(byR).map(function (k) { return { cells: [k + ' ' + E(byR[k][0].reason), byR[k].length, M(D.sum(byR[k], D.charge)), E(PREVENT[k] || 'Review with billing')] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(4, 'None yet.') + '</tbody>')) +
          '</div><div class="pf"><span>' + (sel ? 1 : 0) + ' selected</span><span class="end">Showing ' + list.length + ' of ' + all.length + '</span></div></section>' + right() + '</div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    var C = function () { return findClaim(sel); };
    var keep = function () { var c = C(); if (!c) return; var l = document.getElementById('letter'), b = document.getElementById('basis'); if (l && !l.readOnly) { c.letter = l.value; c.basis = b.value; c.attach = [].slice.call(app.querySelectorAll('[data-att]:checked')).map(function (x) { return x.dataset.att; }); } };
    U.on(app, '[data-q]', 'click', function (el) { if (el.dataset.q === 'x' || el.dataset.q === 'y') return; Q = el.dataset.q; sel = ''; draw(); });
    U.on(app, 'tr[data-claim]', 'click', function (tr, e) { e.preventDefault(); keep(); D.save(); sel = tr.dataset.claim; draw(); });
    U.on(app, '#ailetter', 'click', function (b) {
      var c = C(); b.disabled = true; b.textContent = 'Drafting…';
      V.aiLetter(c).then(function (t) { keep(); c.letter = t; c.dstatus = c.dstatus === 'Needs review' ? 'Appeal drafted' : c.dstatus; D.history(c, 'Appeal letter drafted with AI'); D.save(); draw(); U.toast('Letter drafted. Read it over before sending.'); })
        .catch(function (e) { b.disabled = false; b.textContent = 'Draft with AI'; U.toast(e.message); });
    });
    U.on(app, '#savedraft', 'click', function () { keep(); var c = C(); c.dstatus = 'Appeal drafted'; D.history(c, 'Appeal drafted'); D.save(); draw(); U.toast('Draft saved.'); });
    var send = function () { var c = C(); if (!c || c.stage !== 'denied') return U.toast('Select a denial.'); if (c.dstatus === 'Appealed') return U.toast('Already sent.'); keep(); if (/\[State why/.test(c.letter || '')) return U.toast('Finish the letter first: replace the bracketed line.'); c.dstatus = 'Appealed'; c.appealSent = D.today(); D.history(c, 'Appeal sent with ' + (c.attach || []).length + ' attachment(s)'); D.log('Appeal sent: ' + c.id); D.save(); draw(); U.toast('Appeal recorded as sent. (This preview doesn’t transmit to payers.) Record the outcome when the payer answers.'); };
    U.on(app, '#send', 'click', send); U.on(app, '#send2', 'click', send);
    // Appeal Claim: the appeal request form plus the letter, as a PDF to send to the payer
    var apdf = function (b) {
      var c = C(); if (!c || (c.stage !== 'denied' && !c.appealOutcome)) return U.toast('Select a denial.');
      keep();
      if (!String(c.letter || '').trim()) c.letter = letterFor(c);
      var bl = String(c.basis || '').split(/\n+/).map(function (x) { return x.replace(/^[-•*]\s*/, '').trim(); }).filter(Boolean);
      if (bl.length && /\[State why[^\]]*\]/.test(c.letter)) {
        c.letter = c.letter.replace(/\[State why[^\]]*\]/, 'This claim should be paid because:\n' + bl.map(function (x) { return '- ' + x; }).join('\n') + '\n\nEnclosed are the appeal request form, a copy of the denial notice' + ((c.attach || []).indexOf('note') >= 0 ? ', the signed progress note for the date of service' : '') + ' and the supporting documents listed on the form. Please reprocess the claim for payment.');
      }
      if (/\[State why/.test(c.letter)) U.toast('The letter still has a [State why…] placeholder. Fill it in, then download again.', 'warn');
      var lbl = b && b.innerHTML; if (b) { b.disabled = true; b.textContent = 'Building PDF…'; }
      V.appealPdf(c).then(function (file) {
        if (c.dstatus === 'Needs review') c.dstatus = 'Appeal drafted';
        D.history(c, 'Appeal packet created (' + file + ')'); D.log('Appeal packet created: ' + c.id); D.save(); draw();
        U.toast('Appeal packet downloaded: ' + file + '. Sign it, then send it to ' + D.payer(c) + '.', 'ok');
      }).catch(function (e) { if (b) { b.disabled = false; b.innerHTML = lbl; } U.toast(e.message || 'The PDF couldn’t be made.', 'warn'); });
    };
    U.on(app, '#apdf', 'click', apdf); U.on(app, '#apdf2', 'click', apdf);
    U.on(app, '#editl', 'click', function () { var l = document.getElementById('letter'); if (l) l.focus(); });
    U.on(app, '#printl', 'click', function () { var c = C(); if (!c) return window.print(); keep(); var w = window.open(''); w.document.write('<pre style="font:14px/1.6 Georgia,serif;white-space:pre-wrap;max-width:680px;margin:40px auto">' + E(c.letter || letterFor(c)) + '</pre>'); setTimeout(function () { w.print(); }, 300); });
    var resub = function () { var c = C(); if (!c || c.stage !== 'denied') return U.toast('Select a denial.'); U.confirm('Correct And Resubmit', '<p class="cf">Move ' + E(c.id) + ' back to claims so it can be corrected and resubmitted?</p>', 'Move To Claims', function () { c.stage = 'edits'; c.dstatus = 'Resubmitted'; D.scrub(c); D.history(c, 'Sent back for correction after ' + c.carc); D.log('Correcting ' + c.id); D.save(); location.href = V.claimHref(c.id); }); };
    U.on(app, '#resub', 'click', resub); U.on(app, '#resub2', 'click', resub);
    U.on(app, '#attach', 'click', function (a, e) { e.preventDefault(); keep(); var c = C(); V.upload(c.mrn, function (d) { c.attach = (c.attach || []).concat([d.id]); D.save(); draw(); }); });
    U.on(app, '#overturn', 'click', function () { var c = C(); U.form('Appeal overturned', [{ k: 'paid', label: 'Paid amount', type: 'number', step: '0.01', req: 1, def: (D.charge(c) * .78).toFixed(2) }, { k: 'date', label: 'Paid on', type: 'date', def: D.today() }], {}, function (v) { c.stage = 'paid'; c.paid = +v.paid; c.paidOn = v.date; c.appealOutcome = 'Overturned'; D.history(c, 'Appeal overturned, paid ' + M(c.paid)); D.log('Appeal overturned: ' + c.id); D.save(); draw(); }, { save: 'Post payment' }); });
    U.on(app, '#upheld', 'click', function () { var c = C(); U.confirm('Write Off', '<p class="cf">Write off ' + M(D.charge(c)) + ' on ' + E(c.id) + '? The appeal was upheld.</p>', 'Write Off', function () { c.stage = 'paid'; c.paid = 0; c.paidOn = D.today(); c.appealOutcome = 'Written off'; D.history(c, 'Appeal upheld, written off'); D.save(); draw(); }, { danger: 1 }); });
    U.on(app, '#export', 'click', function () { U.download('denials-' + D.today() + '.csv', U.csv([['Claim', 'Patient', 'Payer', 'DOS', 'CARC', 'Reason', 'Amount', 'Appeal by', 'Status']].concat(S.claims.filter(function (c) { return c.everDenied; }).map(function (c) { return [c.id, D.ptName(c.mrn), D.payer(c), c.dos, c.carc, c.reason, D.charge(c).toFixed(2), c.appealBy, c.appealOutcome || c.dstatus]; })))); });
  };

  /* ================= Payer rules ================= */
  V.pages.rules = function () {
    var Q = 'all', sel = '';
    var stats = function (r) { var hit = S.claims.filter(function (c) { return (c.ruleHits || {})[r.id]; }); return { hit: hit.length, open: hit.filter(function (c) { return c.stage === 'edits' && c.edits.some(function (x) { return (x.rule || x.id) === r.id; }); }), fixed: hit.filter(function (c) { return !(c.stage === 'edits' && c.edits.some(function (x) { return (x.rule || x.id) === r.id; })); }) }; };
    function draw() {
      var R = D.allRules(), list = R.filter(function (r) { return Q === 'all' || r.payer === Q || r.as === Q || (Q === 'local' && r.local); }), payers = [], types = [];
      if (!sel || !R.some(function (r) { return r.id === sel; })) sel = (list[0] || R[0]).id;
      R.forEach(function (r) { if (payers.indexOf(r.payer) < 0) payers.push(r.payer); if (types.indexOf(r.as) < 0) types.push(r.as); });
      var r = R.filter(function (x) { return x.id === sel; })[0], st = stats(r), today = S.claims.filter(function (c) { return c.dos === D.today(); });
      var allStats = R.map(stats), stopped = D.sum(allStats, function (s) { return s.hit; }), prevented = D.sum(allStats, function (s) { return D.sum(s.fixed, D.charge); });
      app.innerHTML = U.header('rules') +
        U.toolbar('Payer rules', [['Check for updates', 'refresh', { pri: 1, id: 'upd' }], ['Add local rule', 'plus', { id: 'addrule' }], '|', ['Export', 'download', { id: 'export' }]]) +
        U.strip([['Active rules', R.length, payers.length + ' payers'], ['Claims stopped before submission', stopped, 'since 08/01'], ['Est. denials prevented', M(prevented), 'since 08/01', prevented ? 'grn' : ''], ['Local rules', S.localRules.length]]) +
        '<div class="main" style="grid-template-columns:205px minmax(0,1fr) 420px">' +
          U.pane('Payers', '', '', U.queue([{ items: [{ key: 'all', label: 'All payers', n: R.length, color: 'blue' }].concat(payers.map(function (p) { return { key: p, label: p, n: R.filter(function (x) { return x.payer === p; }).length, color: null }; })) },
            { h: 'Rule type', items: types.map(function (t) { return { key: t, label: t, n: R.filter(function (x) { return x.as === t; }).length, color: null }; }).concat([{ key: 'local', label: 'Local rules', n: S.localRules.length, color: null }]) }], Q)) +
          '<section class="pane"><div class="ph">' + (Q === 'all' ? 'All payers' : E(Q)) + ' <span class="sub">' + list.length + ' rules</span></div><div class="pbody">' +
            U.grid(['Rule ID', 'Payer', 'Published', 'Effective', ['Requirement', 'wrap'], 'Applied as', ['Today', 'num'], ['Since eff.', 'num']], list.map(function (x) {
              var s = stats(x); return { sel: x.id === sel, attrs: ' data-rule="' + x.id + '"', cells: ['<span class="mono">' + x.id + '</span>', E(x.payer), x.pub ? D.us(x.pub, 1) : '', x.eff ? D.us(x.eff, 1) : 'Now', E(x.req), E(x.as), today.filter(function (c) { return (c.ruleHits || {})[x.id]; }).length, s.hit] };
            }), { cb: 1, click: 1 }) +
          U.sect('Payer performance', 'from this clinic’s claims', (function () {
            var pl = U.uniq(S.claims.map(D.payer));
            return U.grid(['Payer', ['Claims', 'num'], ['Paid', 'num'], ['Denied', 'num'], ['Denial rate', 'num'], ['Avg days to pay', 'num'], ['Collected', 'num']], pl.map(function (x) {
              var cl = S.claims.filter(function (c) { return D.payer(c) === x; }), paid = cl.filter(function (c) { return c.stage === 'paid' && c.submittedOn; }), den = cl.filter(function (c) { return c.everDenied; }), sub = cl.filter(function (c) { return c.submittedOn; });
              var rate = sub.length ? den.length / sub.length : 0;
              return { cells: [E(x), cl.length, paid.length, den.length, '<span class="' + (rate > .1 ? 's-red' : rate > .05 ? 's-amb' : '') + '">' + (sub.length ? U.pct(rate) : '-') + '</span>', paid.length ? (D.sum(paid, function (c) { return D.daysBetween(c.submittedOn, c.paidOn); }) / paid.length).toFixed(1) : '-', M(D.sum(cl, function (c) { return c.paid || 0; }))] };
            })).replace('<tbody></tbody>', '<tbody>' + U.empty(7, 'Appears once claims are sent.') + '</tbody>');
          })()) +
          U.sect('Bulletins reviewed, no action needed', 'last 30 days', U.grid(['Payer', 'Published', 'Bulletin', 'Why no rule'], (S.bulletins || []).map(function (b) { return { cells: [E(b.payer), D.us(b.pub, 1), E(b.title), E(b.why)] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(4, 'None logged. Use Check for updates to record a bulletin you reviewed.') + '</tbody>')) +
          '</div><div class="pf"><span>1 selected</span><span class="end">Showing ' + list.length + ' of ' + R.length + '</span></div></section>' +
          '<section class="pane"><div class="ph">Rule detail <span class="sub mono">' + r.id + '</span></div><div class="pbody">' +
            '<div class="dp-sec"><div class="dp-title">' + E(r.title) + '</div>' + U.kv3([['Payer', E(r.payer)], ['Effective', r.eff ? D.us(r.eff) : 'Now'], ['Applies to', E(r.applies)]]) + '</div>' +
            '<div class="dp-sec"><div class="dp-h">Requirement</div><div>' + E(r.text) + '</div></div>' +
            '<div class="dp-sec"><div class="dp-h">How Althais applies it</div><div class="lst">' + r.how.map(function (h) { return '<div>' + E(h) + '</div>'; }).join('') + '</div></div>' +
            '<div class="dp-sec"><div class="dp-h">Source</div>' + E(r.src) + '</div>' +
            '<div class="dp-sec"><div class="dp-h">Claims affected <span class="sub">' + st.hit + '</span></div>' + U.grid(['Result', ['Claims', 'num'], ['Amount', 'num']], [{ cells: ['<span class="s-grn">Fixed before submission</span>', st.fixed.length, M(D.sum(st.fixed, D.charge))] }, { cells: [U.st('Held, open', 'red'), st.open.length, M(D.sum(st.open, D.charge))] }], { tight: 1 }) + '</div>' +
          '</div>' + (r.local ? '<div class="dp-f"><button class="btn" id="delrule">Delete rule</button><button class="btn pri" id="editrule">Edit</button></div>' : '') + '</section></div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    U.on(app, '[data-q]', 'click', function (el) { Q = el.dataset.q; sel = ''; draw(); });
    U.on(app, 'tr[data-rule]', 'click', function (tr) { sel = tr.dataset.rule; draw(); });
    U.on(app, '#upd', 'click', function () {
      U.form('Log a payer bulletin', [{ k: 'payer', label: 'Payer', req: 1 }, { k: 'pub', label: 'Published', type: 'date', def: D.today() }, { k: 'title', label: 'Bulletin', req: 1, span: 2 }, { k: 'why', label: 'Why no rule is needed', span: 4, ph: 'e.g. not a service this clinic bills' }], {}, function (v) {
        S.bulletins = S.bulletins || []; S.bulletins.unshift(v); D.log('Bulletin reviewed: ' + v.payer + ', ' + v.title); D.save(); draw();
      }, { wide: 1, save: 'Log bulletin', intro: 'The rule library (' + D.RULES.length + ' rules) is current. Record any other payer bulletin you checked; if it needs a rule, use Add local rule.' });
    });
    var ruleForm = function (r) {
      var isNew = !r; r = r || { payer: 'Aetna' };
      U.form(isNew ? 'Add local rule' : 'Edit ' + r.id, [{ k: 'payer', label: 'Payer starts with', req: 1, ph: 'Aetna' }, { k: 'title', label: 'Rule name', req: 1, span: 3, ph: 'Smoking status on preventive visits' }, { k: 'cpt', label: D.L.proc + ' starts with', ph: D.DENTAL ? 'D27 (blank = any)' : '9939 (blank = any)' },
        { k: 'must', label: 'Note must mention', req: 1, span: 3, ph: 'tobacco' }, { k: 'text', label: 'Message to show', type: 'textarea', rows: 2, span: 4, ph: 'Document tobacco use status on preventive visits.' }], r, function (v) {
        Object.assign(r, v, { as: 'Note check', local: true, req: v.title, applies: v.cpt ? v.cpt + '*' : 'All ' + D.L.proc, how: ['Checks every claim for ' + v.payer + (v.cpt ? ' with ' + D.L.proc + ' ' + v.cpt + '*' : ''), 'Holds the claim if the note doesn’t mention "' + v.must + '"'], src: 'Local rule, added by ' + S.clinic.user, eff: r.eff || D.today(), pub: r.pub || D.today() });
        if (isNew) { r.id = 'LOCAL-' + String(S.localRules.length + 1).padStart(3, '0'); S.localRules.push(r); }
        S.claims.filter(function (c) { return c.stage === 'edits' || c.stage === 'ready'; }).forEach(D.scrub); D.log((isNew ? 'Local rule added: ' : 'Local rule edited: ') + r.id); D.save(); sel = r.id; draw();
      }, { wide: 1, save: isNew ? 'Add rule' : 'Save' });
    };
    U.on(app, '#addrule', 'click', function () { ruleForm(); });
    U.on(app, '#editrule', 'click', function () { ruleForm(S.localRules.filter(function (x) { return x.id === sel; })[0]); });
    U.on(app, '#delrule', 'click', function () { U.confirm('Delete Rule', '<p class="cf">Delete rule ' + E(sel) + '? Open claims are re-checked without it.</p>', 'Delete Rule', function () { S.localRules = S.localRules.filter(function (x) { return x.id !== sel; }); D.S.localRules = S.localRules; S.claims.filter(function (c) { return c.stage === 'edits' || c.stage === 'ready'; }).forEach(D.scrub); D.save(); sel = ''; draw(); }, { danger: 1 }); });
    U.on(app, '#export', 'click', function () { U.download('payer-rules.csv', U.csv([['Rule ID', 'Payer', 'Effective', 'Requirement', 'Applied as', 'Source']].concat(D.allRules().map(function (r) { return [r.id, r.payer, r.eff, r.req, r.as, r.src]; })))); });
  };

  /* ================= Team ================= */
  V.pages.team = function () {
    var Q = 'team', sel = q.get('staff') || '';
    var F = { q: '', role: '', loc: '', comp: '', status: 'Active' };
    var role = function (x) { return x.provider ? 'Providers' : x.access === 'Billing' ? 'Billing' : x.access === 'Front desk' ? 'Front desk' : 'Clinical'; };
    var nextExp = function (s) { return (s.creds || []).filter(function (c) { return c.exp; }).sort(function (a, b) { return a.exp.localeCompare(b.exp); })[0]; };
    var comp = function (s) { if (s.status === 'Invited') return ['Onboarding', 'mut']; var n = nextExp(s); if (!n) return ['No credentials', 'amb']; var d = D.daysBetween(D.today(), n.exp); return d < 0 ? ['Expired', 'red'] : d <= 30 ? ['Expiring', 'amb'] : ['Compliant', 'grn']; };
    function person(s) {
      if (!s) return U.pane('Member', '', '', '<div class="blank">' + (S.staff.length ? 'Select a team member.' : 'No team members yet. Use Invite member or Add existing staff. Add yourself first; you sign notes as that person.') + '</div>');
      var steps = s.steps || [];
      return '<section class="pane"><div class="ph">' + E(s.name) + ' ' + E(s.cred || '') + '<span class="end"><a href="#" id="editstaff">Open profile</a></span></div><div class="pbody">' +
        '<div class="dp-sec">' + U.kv3([['Role', E(s.spec || s.access)], ['Location', E(s.loc || '-')], ['Hired', D.us(s.hired)]]) + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Credentials <span class="end"><a href="#" id="addcred">Add</a></span></div>' + U.grid(['Credential', 'Number', 'Expires', 'Status', ''], (s.creds || []).map(function (c, i) { var d = c.exp ? D.daysBetween(D.today(), c.exp) : 999; return { cells: [E(c.name), '<span class="mono">' + E(c.num || '') + '</span>', '<span class="' + (d <= 30 ? 's-amb' : '') + '">' + D.us(c.exp) + '</span>', d < 0 ? '<span class="s-red">Expired</span>' : d <= 30 ? '<span class="s-amb">' + d + ' days</span>' : '<span class="s-grn">Current</span>', '<button class="mini" data-delcred="' + i + '">×</button>'] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'None on file.') + '</tbody>') + '</div>' +
        (steps.length ? '<div class="dp-sec"><div class="dp-h">Onboarding <span class="sub">' + steps.filter(function (x) { return x[1]; }).length + ' of ' + steps.length + '</span></div>' + steps.map(function (x, i) { return '<label class="chk" style="margin:0 0 4px"><input type="checkbox" data-step="' + i + '"' + (x[1] ? ' checked' : '') + '> ' + E(x[0]) + '</label><br>'; }).join('') + '</div>' : '') +
        '<div class="dp-sec"><div class="dp-h">Training <span class="end"><a href="#" id="addtrain">Add</a></span></div>' + U.grid(['Course', 'Completed', 'Due'], (s.training || []).map(function (t) { return { cells: [E(t.name), D.us(t.done), D.us(t.due)] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(3, 'None recorded.') + '</tbody>') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Reminders</div><div class="lst">' + ((s.reminders || []).map(function (r) { return '<div>' + E(r) + '</div>'; }).join('') || '<span class="mut">None sent</span>') + '</div></div>' +
        '<div class="dp-sec"><div class="dp-h">Althais access</div>' + U.kv([['Role', E(s.access)], ['Can sign notes', s.provider ? 'Yes' : 'No'], ['Can view billing', /Admin|Billing/.test(s.access) ? 'Yes' : 'No'], ['Last login', S.clinic.userStaff === s.id ? 'Now' : '-'], ['Signs as you', S.clinic.userStaff === s.id ? 'Yes' : '<a href="#" id="makeme">Make this me</a>']]) + '</div>' +
        (s.hours || s.assigned ? '<div class="dp-sec"><div class="dp-h">Schedule this week</div>' + U.kv([['Hours', E(s.hours || '-')], ['Assigned to', E(s.assigned || '-')]]) + '</div>' : '') +
        '</div><div class="dp-f"><button class="btn" id="deact">' + (s.status === 'Inactive' ? 'Reactivate' : 'Deactivate') + '</button><button class="btn" id="card">Upload new card</button><button class="btn pri" id="remind">Send reminder</button></div></section>';
    }
    function draw() {
      var todo = function (x) { return ['Expiring', 'Expired', 'No credentials'].indexOf(comp(x)[0]) >= 0 || x.status === 'Invited'; };
      var list = S.staff.filter(function (x) { return (Q === 'team' ? true : Q === 'onb' ? x.status === 'Invited' : Q === 'todo' ? todo(x) : Q === 'cred' ? (x.creds || []).length : Q === 'comp' ? comp(x)[0] !== 'Compliant' : Q === 'train' ? (x.training || []).length : role(x) === Q)
        && (!F.q || (x.name + ' ' + (x.email || '')).toLowerCase().indexOf(F.q.toLowerCase()) >= 0) && (!F.role || role(x) === F.role) && (!F.loc || x.loc === F.loc) && (!F.comp || comp(x)[0] === F.comp)
        && (!F.status || (F.status === 'Active' ? x.status !== 'Inactive' : x.status === F.status)); });
      if (!sel || !D.staff(sel)) sel = (list[0] || {}).id || '';
      var exp = []; S.staff.forEach(function (s) { (s.creds || []).forEach(function (c) { var d = c.exp ? D.daysBetween(D.today(), c.exp) : 999; if (d <= 120) exp.push([s, c, d]); }); }); exp.sort(function (a, b) { return a[2] - b[2]; });
      var inv = S.staff.filter(function (s) { return s.status === 'Invited'; });
      app.innerHTML = U.header('team') +
        U.toolbar('Team', [['Invite member', 'user', { pri: 1, id: 'invite' }], ['Add existing staff', 'plus', { id: 'addstaff' }], ['Send reminders', 'mail', { id: 'remindall' }], '|', ['Export CSV', 'download', { id: 'export' }]]) +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr) 400px">' +
          U.pane('Staff', '', '', U.queue([{ items: [{ key: 'todo', label: 'To-do', n: S.staff.filter(todo).length, color: 'amb' }, { key: 'team', label: 'Team', n: S.staff.filter(function (x) { return x.status !== 'Inactive'; }).length, color: 'blue' }, { key: 'onb', label: 'Onboarding', n: inv.length, color: 'gray' },
              { key: 'cred', label: 'Credentials', n: D.sum(S.staff, function (x) { return (x.creds || []).length; }), color: null }, { key: 'comp', label: 'Compliance', color: null }, { key: 'train', label: 'Training', color: null }, { key: 'roles', label: 'Roles and access', color: null }] },
            { h: 'Filter by role', items: ['Providers', 'Clinical', 'Billing', 'Front desk'].map(function (r) { return { key: r, label: r, n: S.staff.filter(function (x) { return role(x) === r; }).length, color: null }; }) }], Q),
            { after: U.sum([['Fully compliant', S.staff.filter(function (x) { return comp(x)[0] === 'Compliant'; }).length + ' of ' + S.staff.length], ['Expiring in 30 days', exp.filter(function (x) { return x[2] <= 30; }).length], ['Onboarding', inv.length]]) }) +
          '<section class="pane"><div class="ph">Team <span class="sub">' + list.length + ' members</span></div>' +
            '<div class="fl"><input class="chipf" id="tq" placeholder="Search name or email" value="' + E(F.q) + '">' + U.chip('Role', 'role', ['Providers', 'Clinical', 'Billing', 'Front desk'], F.role) + U.chip('Location', 'loc', U.uniq(S.staff.map(function (x) { return x.loc; })), F.loc) +
              U.chip('Compliance', 'comp', ['Compliant', 'Expiring', 'Expired', 'No credentials', 'Onboarding'], F.comp) + U.chip('Status', 'status', ['Active', 'Invited', 'Inactive'], F.status) + '</div><div class="pbody">' +
            U.grid(['Name', 'Cred.', 'Specialty / dept.', 'Location', 'Access', 'Compliance', 'Next expiring', 'Expires', 'Status'], list.map(function (x) { var c = comp(x), n = nextExp(x); return { sel: x.id === sel, attrs: ' data-staff="' + x.id + '"', cells: [E(x.name), E(x.cred || ''), E(x.spec || ''), E(x.loc || ''), E(x.access), '<span class="s-' + c[1] + '">' + c[0] + '</span>', E(n ? n.name : ''), n ? D.us(n.exp) : '', '<span class="s-' + (x.status === 'Active' ? 'grn' : 'mut') + '">' + x.status + '</span>'] }; }), { cb: 1, click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(10, 'No team members.') + '</tbody>') +
            U.sect('Expiring in the next 120 days', exp.length + ' credentials', U.grid(['Name', 'Credential', 'Expires', ['Days left', 'num'], 'Last reminder'], exp.map(function (x) { return { attrs: ' data-staff="' + x[0].id + '"', cells: [E(x[0].name), E(x[1].name), D.us(x[1].exp), '<span class="' + (x[2] <= 30 ? 's-amb' : '') + '">' + x[2] + '</span>', E((x[0].reminders || [])[0] || '')] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'None.') + '</tbody>')) +
            U.sect(inv.length === 1 ? 'Onboarding: ' + inv[0].name + (inv[0].cred ? ' ' + inv[0].cred : '') : 'Onboarding', inv.length === 1 ? (inv[0].steps || []).filter(function (y) { return y[1]; }).length + ' of ' + (inv[0].steps || []).length + ' steps' : inv.length + ' in progress', U.grid(['Name', 'Steps done', 'Next step'], inv.map(function (x) { var st = x.steps || []; var nx = st.filter(function (y) { return !y[1]; })[0]; return { attrs: ' data-staff="' + x.id + '"', cells: [E(x.name), st.filter(function (y) { return y[1]; }).length + ' of ' + st.length, E(nx ? nx[0] : 'Ready to activate')] }; }), { click: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(3, 'None.') + '</tbody>')) +
          '</div><div class="pf"><span>' + (sel ? 1 : 0) + ' selected</span><span class="end">Showing ' + list.length + ' of ' + S.staff.length + '</span></div></section>' + person(D.staff(sel)) + '</div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    if (q.get('add') === 'provider') V.staffForm(null, function (s) { sel = s.id; draw(); }, true);
    var P = function () { return D.staff(sel); };
    U.on(app, '[data-q]', 'click', function (el) { if (el.dataset.q === 'roles') { location.href = '/v4/settings?s=users'; return; } Q = el.dataset.q; sel = ''; draw(); });
    U.on(app, '[data-f]', 'change', function (s) { F[s.dataset.f] = s.value; draw(); });
    U.on(app, '#tq', 'input', function (i) { F.q = i.value; var pos = i.selectionStart; draw(); var n = document.getElementById('tq'); n.focus(); n.setSelectionRange(pos, pos); });
    U.on(app, '#addtrain', 'click', function (a, e) { e.preventDefault(); U.form('Add training', [{ k: 'name', label: 'Course', req: 1, span: 2, ph: 'HIPAA annual' }, { k: 'done', label: 'Completed', type: 'date' }, { k: 'due', label: 'Due', type: 'date' }], {}, function (v) { var s = P(); (s.training = s.training || []).push(v); D.save(); draw(); }); });
    U.on(app, 'tr[data-staff]', 'click', function (tr) { sel = tr.dataset.staff; draw(); });
    U.on(app, '#invite', 'click', function () { V.staffForm(null, function (s) { sel = s.id; draw(); }, false, true); });
    U.on(app, '#addstaff', 'click', function () { V.staffForm(null, function (s) { sel = s.id; draw(); }); });
    U.on(app, '#editstaff', 'click', function (a, e) { e.preventDefault(); V.staffForm(P(), function () { draw(); }); });
    U.on(app, '#makeme', 'click', function (a, e) { e.preventDefault(); var s = P(); S.clinic.userStaff = s.id; S.clinic.user = s.name.split(', ').reverse().join(' ') + (s.cred ? ', ' + s.cred : ''); D.save(); draw(); U.toast('You now sign as ' + S.clinic.user + '.'); });
    U.on(app, '#addcred', 'click', function (a, e) { e.preventDefault(); U.form('Add credential', [{ k: 'name', label: 'Credential', req: 1, span: 2, ph: 'BLS, DEA, State license' }, { k: 'num', label: 'Number' }, { k: 'exp', label: 'Expires', type: 'date', req: 1 }], {}, function (v) { var s = P(); s.creds = s.creds || []; s.creds.push(v); D.log('Credential added: ' + s.name + ', ' + v.name); D.save(); draw(); }); });
    U.on(app, '[data-delcred]', 'click', function (b) { var s = P(); s.creds.splice(+b.dataset.delcred, 1); D.save(); draw(); });
    U.on(app, '[data-step]', 'change', function (c) { var s = P(); s.steps[+c.dataset.step][1] = c.checked; D.save(); draw(); if (s.steps.every(function (x) { return x[1]; })) U.confirm('Activate', '<p class="cf">All onboarding steps are done. Activate ' + E(s.name) + '?</p>', 'Activate', function () { s.status = 'Active'; D.save(); draw(); }, { cancel: 'Not Yet' }); });
    var remind = function (s) { (s.reminders = s.reminders || []).unshift(D.us(D.today(), 1).slice(0, 5) + '  Reminder sent by email'); D.log('Reminder sent: ' + s.name); };
    U.on(app, '#remind', 'click', function () { remind(P()); D.save(); draw(); U.toast('Reminder recorded. (This preview does not send email.)'); });
    U.on(app, '#remindall', 'click', function () { var l = S.staff.filter(function (s) { return (s.creds || []).some(function (c) { return c.exp && D.daysBetween(D.today(), c.exp) <= 30; }); }); l.forEach(remind); D.save(); draw(); U.toast(l.length ? 'Reminders recorded for ' + l.length + ' member' + (l.length === 1 ? '' : 's') + '.' : 'No credentials expire in the next 30 days.'); });
    U.on(app, '#deact', 'click', function () { var s = P(); s.status = s.status === 'Inactive' ? 'Active' : 'Inactive'; D.log(s.name + ' ' + s.status.toLowerCase()); D.save(); draw(); });
    U.on(app, '#card', 'click', function () { var s = P(); U.pickFile('image/*,.pdf', false, function (f) { var rec = { id: D.id('DOC'), mrn: '', name: s.name + ' credential card', file: f[0].name, type: 'Other', src: 'Upload', rec: D.today(), size: U.size(f[0].size), mime: f[0].type, status: 'Filed to chart' }; var fin = function () { S.docs.unshift(rec); D.log('Card uploaded: ' + s.name); D.save(); U.toast('Card saved to Documents.'); }; if (f[0].size <= 2097152) U.readFile(f[0]).then(function (d) { rec.data = d; fin(); }); else fin(); }); });
    U.on(app, '#export', 'click', function () { U.download('team-' + D.today() + '.csv', U.csv([['Name', 'Credentials', 'Specialty', 'Location', 'Access', 'Email', 'NPI', 'Status', 'Next expiring', 'Expires']].concat(S.staff.map(function (s) { var n = nextExp(s); return [s.name, s.cred, s.spec, s.loc, s.access, s.email, s.npi, s.status, n ? n.name : '', n ? n.exp : '']; })))); });
  };

  /* ================= Settings ================= */
  V.pages.settings = function () {
    var sec = q.get('s') || 'pd', dirty = false;
    var NAV = [{ h: 'Practice', items: [['pd', 'Practice details'], ['loc', 'Locations', S.locations.length], ['prov', 'Providers', D.providers().length], ['users', 'Users and roles', S.staff.length]] },
      { h: 'Clinical', items: [['vs', 'Virtual SOAP'], ['nt', 'Note templates', S.templates.length], ['os', 'Order sets', S.ordersets.length]] },
      { h: 'Billing', items: [['ch', 'Clearinghouse'], ['py', 'Payers', U.uniq(S.patients.map(function (p) { return p.payer; })).length], ['fs', 'Fee schedules', S.fees.length], ['cr', 'Claim rules', D.allRules().length]] },
      { h: 'Workspace', items: [['ap', 'Appearance'], ['int', 'Integrations', S.integrations.filter(function (i) { return i.on; }).length], ['nf', 'Notifications'], ['sec', 'Security and data']] }];
    function middle() {
      if (sec === 'pd') return U.sect('Practice details', 'used on claims, statements, and patient messages', '<div class="form" id="pdform">' + U.fields([
        { k: 'name', label: 'Practice name', span: 2 }, { k: 'specialty', label: 'Specialty', span: 2 }, { k: 'npi', label: 'Group NPI', ph: '10 digits' }, { k: 'ein', label: 'Tax ID (EIN)', ph: '12-3456789' }, { k: 'taxonomy', label: 'Taxonomy', ph: D.DENTAL ? '1223G0001X' : '207Q00000X' }, D.DENTAL ? { k: 'license', label: 'State dental license' } : { k: 'ptan', label: 'Medicare PTAN' },
        { k: 'addr', label: 'Billing address', span: 2 }, { k: 'city', label: 'City, state, ZIP' }, { k: 'phone', label: 'Phone' }, { k: 'payto', label: 'Pay-to address', span: 2 }, { k: 'fax', label: 'Fax' },
        { k: 'tz', label: 'Time zone', type: 'select', options: ['Eastern (America/New_York)', 'Central (America/Chicago)', 'Mountain (America/Denver)', 'Pacific (America/Los_Angeles)'] }, { k: 'user', label: 'Your name, as it appears when you sign', span: 2 }], S.clinic).replace('<div class="form" style="padding:0">', '').replace(/<\/div>$/, '') + '</div>');
      if (sec === 'loc') return U.sect('Locations', S.locations.length, U.grid(['Name', 'Address', 'POS', 'Phone', 'Hours', ''], S.locations.map(function (l, i) { return { cells: [E(l.name), E(l.addr), E(l.pos), E(l.phone), E(l.hours), '<button class="mini" data-dloc="' + i + '">Remove</button>'] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No locations yet.') + '</tbody>'), '<a href="#" id="addloc">Add location</a>');
      if (sec === 'prov' || sec === 'users') { var l = sec === 'prov' ? D.providers() : S.staff; return U.sect(sec === 'prov' ? 'Providers' : 'Users and roles', l.length, U.grid(['Name', 'Cred.', 'Individual NPI', 'Specialty', 'Access', 'Status', ''], l.map(function (s) { return { cells: [E(s.name), E(s.cred || ''), '<span class="mono">' + E(s.npi || '') + '</span>', E(s.spec || ''), sec === 'users' ? '<select class="inl" data-acc="' + s.id + '">' + ['Admin', 'Provider', 'Clinical', 'Billing', 'Front desk'].map(function (a) { return '<option' + (a === s.access ? ' selected' : '') + '>' + a + '</option>'; }).join('') + '</select>' : E(s.access), '<span class="s-' + (s.status === 'Active' ? 'grn' : 'mut') + '">' + s.status + '</span>', '<button class="mini" data-estaff="' + s.id + '">Edit</button>'] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(7, 'None yet.') + '</tbody>'), '<a href="#" id="addprov">' + (sec === 'prov' ? 'Add provider' : 'Add user') + '</a>'); }
      if (sec === 'vs') return U.sect('Virtual SOAP', '', '<div class="form" id="vsform">' + U.fields([{ k: 'consent', label: 'Patient consent', type: 'select', options: ['Required each visit', 'Once per year'] }, { k: 'retention', label: 'Audio retention', type: 'select', options: ['Not kept', '30 days', '90 days'] }, { k: 'style', label: 'Note style', type: 'select', options: ['SOAP, concise', 'SOAP, detailed'] }, { k: 'coding', label: 'Coding suggestions', type: 'select', options: ['On', 'Off'] }], S.soap).replace('<div class="form" style="padding:0">', '').replace(/<\/div>$/, '') + '</div>');
      if (sec === 'nt' || sec === 'os') { var arr = sec === 'nt' ? S.templates : S.ordersets; return U.sect(sec === 'nt' ? 'Note templates' : 'Order sets', arr.length, U.grid(['Name', ''], arr.map(function (x, i) { return { cells: [E(x), '<button class="mini" data-dlist="' + i + '">Remove</button>'] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(2, 'None.') + '</tbody>'), '<a href="#" id="addlist">Add</a>'); }
      if (sec === 'ch') { var av = S.integrations.filter(function (i) { return i.name === 'Availity'; })[0]; return U.sect('Clearinghouse', '', '<div class="dp-sec">' + U.kv([['Clearinghouse', 'Availity'], ['Status', av.on ? 'Marked connected' : 'Not connected', av.on ? 'grn' : ''], ['Mode', 'Preview: claims are recorded here and never transmitted']]) + '<div style="margin-top:8px"><button class="btn sm" data-int="0">' + (av.on ? 'Mark disconnected' : 'Mark connected') + '</button></div></div>'); }
      if (sec === 'fs') return U.sect('Fee schedule', S.fees.length + ' codes, billed charge per unit', U.grid([D.L.proc, 'Description', ['Rate', 'num'], ''], S.fees.map(function (f, i) { return { cells: ['<input class="inl" data-fc="' + i + '" value="' + E(f.code) + '" style="width:70px">', '<input class="inl" data-fd="' + i + '" value="' + E(f.desc) + '">', '<input class="inl" type="number" step="0.01" data-fr="' + i + '" value="' + f.rate + '" style="width:90px;text-align:right">', '<button class="mini" data-fdel="' + i + '">×</button>'] }; })), '<a href="#" id="addfee">Add code</a>');
      if (sec === 'py') { var pl = U.uniq(S.patients.map(function (p) { return p.payer; })); return U.sect('Payers', pl.length + ' billed by this clinic', U.grid(['Payer', ['Patients', 'num'], ['Claims', 'num'], ['Billed', 'num'], ['Paid', 'num']], pl.map(function (x) { var cl = S.claims.filter(function (c) { return D.payer(c) === x; }); return { cells: [E(x), S.patients.filter(function (p) { return p.payer === x; }).length, cl.length, M(D.sum(cl, D.charge)), M(D.sum(cl, function (c) { return c.paid || 0; }))] }; })).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'Payers appear as you add patients.') + '</tbody>')); }
      if (sec === 'cr') return U.sect('Claim rules', D.allRules().length, U.grid(['Rule ID', 'Payer', 'Requirement'], D.allRules().map(function (r) { return { cells: ['<span class="mono">' + r.id + '</span>', E(r.payer), E(r.req)] }; })), '<a href="/v4/revenue/payer-rules">Manage</a>');
      if (sec === 'int') return U.sect('Integrations', S.integrations.filter(function (i) { return i.on; }).length + ' marked connected', U.grid(['Service', 'Used for', 'Status', 'Since', ''], S.integrations.map(function (x, i) { return { cells: [E(x.name), E(x.use), '<span class="s-' + (x.on ? 'grn' : 'mut') + '">' + (x.on ? 'Connected' : 'Not connected') + '</span>', E(x.sync || ''), '<button class="mini" data-int="' + i + '">' + (x.on ? 'Disconnect' : 'Connect') + '</button>'] }; })) + '<div class="blank mut">In this preview, connecting only records the setting. No data is exchanged.</div>');
      if (sec === 'ap') {
        var B = window.AlthaisBrand, cur = B ? B.current() : '#0d5bd7', can = B ? B.canEdit() : false, by = B && B.savedBy();
        var groups = B ? B.LIBRARY : [];
        return U.sect('Appearance', 'the clinic’s color, the same choices as before, used for everyone at the practice',
          '<div class="dp-sec"><h5 style="margin:0 0 6px;font-size:12px;color:var(--t2)">Mode <span class="mut" style="font-weight:400">· your screen only</span></h5><div class="ap-mode">' + [['light', 'Light'], ['dark', 'Dark'], ['system', 'System']].map(function (m) { var on = (window.AlthaisTheme ? AlthaisTheme.mode() : 'light') === m[0]; return '<button type="button" data-apm="' + m[0] + '" class="' + (on ? 'on' : '') + '">' + m[1] + '</button>'; }).join('') + '</div>' +
          '<h5 style="margin:0 0 6px;font-size:12px;color:var(--t2)">Clinic color <span class="mut" style="font-weight:400">· stays the same in light and dark</span></h5><div class="ap-cur"><i style="background:' + E(cur) + '"></i><div><b>' + E(B ? B.nameOf(cur) : 'Althais Blue') + '</b><span>' + E(cur) + (by ? ' · set by ' + E(by.by) + ', ' + new Date(by.at).toLocaleDateString('en-US') : '') + '</span></div>' +
            '<span style="margin-left:auto"></span><button class="btn" id="apreset">Althais Blue</button></div>' +
          groups.map(function (g) { return '<div class="ap-grp"><h5>' + E(g.group) + '</h5><div class="ap-sw">' + g.colors.map(function (c) { return '<button type="button" data-apc="' + E(c[1]) + '" class="' + (String(c[1]).toLowerCase() === String(cur).toLowerCase() ? 'on' : '') + '"><i style="background:' + E(c[1]) + '"></i>' + E(c[0]) + '</button>'; }).join('') + '</div></div>'; }).join('') +
          '<div class="ap-grp"><h5>Custom color</h5><div class="ap-sw"><input type="color" id="apcustom" value="' + E(cur) + '" style="width:44px;height:28px;padding:0;border:1px solid var(--b)"> <span class="mut" style="align-self:center">Pick any color; text stays readable on it.</span></div></div>' +
          '<h5 style="margin:6px 0;font-size:12px;color:var(--t2)">Preview</h5><div class="ap-prev"><div class="bar">ALTHAIS <span style="letter-spacing:0;font-weight:500;opacity:.85">Overview</span><span style="letter-spacing:0;font-weight:500;opacity:.85">EMR</span></div><div class="row"><button class="btn pri">Primary Button</button><a href="#">A link</a><span class="s-blue">Ready To Submit</span></div></div>' +
          (can ? '' : '<p class="mut" style="margin:10px 0 0">Only a clinic admin can change the color for everyone. Colors you click are previewed here only.</p>') + '</div>');
      }
      if (sec === 'nf') return U.sect('Notifications', '', '<div class="dp-sec">' + [['email', 'Email me a daily summary'], ['sms', 'Text me when a claim is denied'], ['denials', 'Show denial alerts on Overview'], ['creds', 'Remind staff 30 days before a credential expires']].map(function (n) { return '<label class="chk" style="margin:0 0 6px"><input type="checkbox" data-nf="' + n[0] + '"' + (S.notify[n[0]] ? ' checked' : '') + '> ' + n[1] + '</label><br>'; }).join('') + '</div>');
      return V.securityHtml() + U.sect('Records and data', '', '<div class="form" id="secform">' + U.fields([{ k: 'audit', label: 'Access log kept for', type: 'select', options: ['7 years', '10 years'] }, { k: 'baa', label: 'Business associate agreement with Althais', type: 'select', options: ['Not signed', 'Signed'] }], S.security).replace('<div class="form" style="padding:0">', '').replace(/<\/div>$/, '') + '</div>' +
        '<div class="dp-sec"><div class="dp-h">Data in this browser</div><p style="margin:0 0 8px">This browser keeps a working copy of the clinic’s records (about ' + Math.round(JSON.stringify(S).length / 1024) + ' KB)' + (D.crypto.on ? ', encrypted with AES-256. It can only be opened while you’re signed in to this clinic.' : '.') + ' Patients, claims, appointments and visit notes are also saved on Althais’s server, so they come back if this copy is cleared.' + ' A downloaded backup is not encrypted: keep it somewhere safe.</p><button class="btn sm" id="backup">Download backup</button> <button class="btn sm" id="restore">Restore backup</button> <button class="btn sm" id="wipe" style="color:var(--red);border-color:var(--red-line)">Clear this browser</button></div>' +
        '<div class="dp-sec"><div class="dp-h">Previous EMR</div><p style="margin:0 0 8px">Patients and claims from the previous EMR were brought over' + (S.importedLocal ? ' on ' + new Date(S.importedLocal).toLocaleDateString('en-US') : '') + ', and patients and claims you change here are saved to the server.</p><a class="btn sm" style="display:inline-flex;align-items:center" href="/ui/classic">Switch to the classic EMR</a></div>' +
        '<div class="dp-sec"><div class="dp-h">Sample clinic</div><p style="margin:0 0 8px">Twelve example patients, today\'s schedule, a visit in progress and claims in every stage, so you can try each screen or give a demo. Names start with "Sample" and nothing is saved to the server. Reset before each demo, or open /v4/overview?sample=reset.</p><button class="btn sm" id="loadsample">Load sample clinic</button> <button class="btn sm" id="resetsample">Reset for a demo</button> <button class="btn sm" id="clearsample">Remove sample clinic</button></div>');
    }
    function draw() {
      var setup = [[!!S.clinic.npi && !!S.clinic.addr, 'Practice details', 'pd'], [D.providers().length > 0, 'At least one provider', 'prov'], [S.locations.length > 0, 'A location', 'loc'], [S.patients.length > 0, 'Patients added', ''], [S.integrations[0].on, 'Clearinghouse', 'ch'], [S.security.baa === 'Signed', 'BAA signed', 'sec']];
      app.innerHTML = U.header('settings') + U.toolbar('Settings', [['Save changes', 'save', { pri: 1, id: 'save' }], ['Discard', 'refresh', { id: 'discard' }]], S.lastSaved ? 'Last saved ' + new Date(S.lastSaved).toLocaleString('en-US') : 'Not saved yet') +
        '<div class="main" style="grid-template-columns:215px minmax(0,1fr) 330px">' +
          U.pane('Settings', '', '', U.queue(NAV.map(function (g) { return { h: g.h, items: g.items.map(function (i) { return { key: i[0], label: i[1], n: i[2], color: null }; }) }; }), sec)) +
          '<section class="pane"><div class="pbody">' + middle() + '</div></section>' +
          U.pane('Setup status', setup.filter(function (x) { return x[0]; }).length + ' of ' + setup.length, '', '<div class="dp-sec"><div class="ok-list">' + setup.map(function (x) { return '<div class="' + (x[0] ? '' : 'w') + '">' + (x[0] ? '' : U.warn) + (x[2] ? '<a href="#" data-go="' + x[2] + '" style="color:inherit">' + E(x[1]) + '</a>' : E(x[1])) + '</div>'; }).join('') + '</div></div>' +
            '<div class="sect-h" style="border-top:1px solid var(--b)">Security</div><div class="dp-sec">' + U.kv([['Two-step login', V.sec ? { admins_billers: 'Admins and billers', everyone: 'Everyone', optional: 'Optional' }[V.sec.clinic.policy.mfa] : '…'], ['Automatic sign-out', V.sec ? V.sec.clinic.policy.idle_minutes + ' minutes' : '…'], ['Access log kept', S.security.audit], ['BAA', S.security.baa, S.security.baa === 'Signed' ? 'grn' : 'amb']]) + '</div>' +
            '<div class="sect-h" style="border-top:1px solid var(--b)">Virtual SOAP</div><div class="dp-sec">' + U.kv([['Patient consent', S.soap.consent], ['Audio retention', S.soap.retention], ['Coding suggestions', S.soap.coding], ['Documentation changes', 'Provider approval required']]) + '</div>') + '</div>';
      U.fit(); U.search();
    }
    V.redraw = draw; draw();
    V.loadSecurity(draw);
    var commit = function () {
      var pd = document.getElementById('pdform'), vs = document.getElementById('vsform'), sc = document.getElementById('secform');
      if (pd) { var v = U.values(pd); if (v.npi && !/^\d{10}$/.test(v.npi)) { U.toast('Group NPI must be 10 digits.'); return false; } Object.assign(S.clinic, v); }
      // an account created without a clinic name can't set up billing or send claims: name it on the account too
      var me = window.__ALTHAIS_USER__ || {};
      if (pd && !String(me.organization || '').trim() && String(S.clinic.name || '').trim().length >= 2) {
        fetch('/api/clinic/name', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: S.clinic.name.trim() }) })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (x) {
            if (!x.ok || !x.j.organization) return U.toast(x.j.error || 'Could not save the clinic name for billing.', 'warn');
            if (x.j.unchanged) return;
            // this browser's records follow the clinic to its new name
            D.moveTo(x.j.organization, x.j.browser_key);
            U.toast(x.j.organization + ' is now your clinic’s name for billing and claims. Reloading…');
            setTimeout(function () { location.reload(); }, 1400);
          });
      }
      if (vs) Object.assign(S.soap, U.values(vs)); if (sc) Object.assign(S.security, U.values(sc));
      S.lastSaved = new Date().toISOString(); D.log('Settings saved'); D.save(); dirty = false; return true;
    };
    app.addEventListener('input', function () { dirty = true; });
    U.on(app, '[data-q], [data-go]', 'click', function (el, e) { e.preventDefault(); var go = function () { dirty = false; sec = el.dataset.q || el.dataset.go; draw(); }; if (dirty) U.confirm('Unsaved Changes', '<p class="cf">Leave without saving these changes?</p>', 'Leave', go, { cancel: 'Stay' }); else go(); });
    // Appearance: the color changes on screen right away; an admin's choice is saved for everyone at the clinic
    var pickColor = function (hex) { var B = window.AlthaisBrand; if (!B) return; B.apply(hex); draw();
      if (!B.canEdit()) return U.toast('Previewing ' + B.nameOf(hex) + '. Only a clinic admin can save it for everyone.', 'info');
      B.save(hex).then(function () { draw(); U.toast(B.nameOf(hex) + ' saved for the clinic.'); }, function (e) { U.toast(e.message, 'warn'); }); };
    U.on(app, '[data-apc]', 'click', function (b) { pickColor(b.dataset.apc); });
    U.on(app, '[data-apm]', 'click', function (b) { if (window.AlthaisTheme) AlthaisTheme.set(b.dataset.apm); draw(); });
    U.on(app, '#apreset', 'click', function () { pickColor('#0d5bd7'); });
    U.on(app, '#apcustom', 'change', function (inp) { pickColor(inp.value); });
    U.on(app, '#save', 'click', function () { if (commit()) { draw(); U.toast('Settings saved.'); } });
    U.on(app, '#discard', 'click', function () { dirty = false; draw(); U.toast('Changes discarded.'); });
    U.on(app, '#addloc', 'click', function (a, e) { e.preventDefault(); U.form('Add location', [{ k: 'name', label: 'Name', req: 1 }, { k: 'pos', label: 'Place of service', type: 'select', options: ['11 Office', '02 Telehealth', '22 Outpatient hospital'] }, { k: 'addr', label: 'Address', span: 2, req: 1 }, { k: 'phone', label: 'Phone' }, { k: 'hours', label: 'Hours', ph: 'Mon to Fri 8 to 5' }], {}, function (v) { S.locations.push(v); D.log('Location added: ' + v.name); D.save(); draw(); }); });
    U.on(app, '[data-dloc]', 'click', function (b) { S.locations.splice(+b.dataset.dloc, 1); D.save(); draw(); });
    U.on(app, '#addprov', 'click', function (a, e) { e.preventDefault(); V.staffForm(null, function () { draw(); }, sec === 'prov'); });
    U.on(app, '[data-estaff]', 'click', function (b) { V.staffForm(D.staff(b.dataset.estaff), function () { draw(); }); });
    U.on(app, '[data-acc]', 'change', function (s) { D.staff(s.dataset.acc).access = s.value; D.save(); });
    U.on(app, '#addlist', 'click', function (a, e) { e.preventDefault(); U.ask(sec === 'nt' ? 'New Note Template' : 'New Order Set', 'Name', '', function (n) { (sec === 'nt' ? S.templates : S.ordersets).push(n); D.save(); draw(); }, { ok: 'Add' }); });
    U.on(app, '[data-dlist]', 'click', function (b) { (sec === 'nt' ? S.templates : S.ordersets).splice(+b.dataset.dlist, 1); D.save(); draw(); });
    U.on(app, '[data-int]', 'click', function (b) { var x = S.integrations[+b.dataset.int]; x.on = !x.on; x.sync = x.on ? D.us(D.today()) : ''; D.log(x.name + (x.on ? ' marked connected' : ' disconnected')); D.save(); draw(); });
    var feeEdit = function (attr, fn) { U.on(app, '[' + attr + ']', 'change', function (i) { fn(S.fees[+i.getAttribute(attr)], i.value.trim()); D.save(); }); };
    feeEdit('data-fc', function (f, v) { f.code = v.toUpperCase(); }); feeEdit('data-fd', function (f, v) { f.desc = v; }); feeEdit('data-fr', function (f, v) { f.rate = Math.max(0, parseFloat(v) || 0); });
    U.on(app, '[data-fdel]', 'click', function (b) { S.fees.splice(+b.dataset.fdel, 1); D.save(); draw(); });
    U.on(app, '#addfee', 'click', function (a, e) { e.preventDefault(); U.form('Add fee', [{ k: 'code', label: 'CPT / HCPCS', req: 1 }, { k: 'rate', label: 'Rate', type: 'number', step: '0.01', req: 1 }, { k: 'desc', label: 'Description', span: 2 }], {}, function (v) { if (S.fees.some(function (f) { return f.code === v.code.toUpperCase(); })) return 'That code is already on the schedule.'; S.fees.push({ code: v.code.toUpperCase(), desc: v.desc, rate: +v.rate }); D.save(); draw(); }); });
    U.on(app, '[data-nf]', 'change', function (c) { S.notify[c.dataset.nf] = c.checked; D.save(); });
    U.on(app, '#loadsample', 'click', function () { V.loadSample(); });
    U.on(app, '#resetsample', 'click', function () { U.confirm('Reset Sample Clinic', '<p class="cf">Put the sample patients, schedule, visits and claims back the way they start, with today\'s dates? Anything you changed in the sample clinic is undone. Your own records stay.</p>', 'Reset', function () { V.resetSample(); }); });
    U.on(app, '#clearsample', 'click', function () { U.confirm('Remove Sample Data', '<p class="cf">Remove the sample patients, visits and claims? Your own records stay.</p>', 'Remove', function () { V.clearSample(); }, { danger: 1 }); });
    U.on(app, '#backup', 'click', function () { U.download('althais-preview-backup-' + D.today() + '.json', JSON.stringify(S), 'application/json'); });
    U.on(app, '#restore', 'click', function () { U.pickFile('.json,application/json', false, function (f) { U.readFile(f[0], true).then(function (t) { try { var x = JSON.parse(t); if (x.v !== 1 || !x.patients) throw 0; U.confirm('Restore Backup', '<p class="cf">Replace everything in this browser with this backup?</p>', 'Restore', function () { try { D.restore(t); } catch (e) { return U.toast('This browser could not save the backup. Storage may be full.'); } location.reload(); }, { danger: 1 }); } catch (e) { U.toast('That file isn’t an Althais preview backup.'); } }); }); });
    U.on(app, '#wipe', 'click', function () { U.ask('Clear This Browser', 'Type DELETE to confirm', '', function () { D.reset(); location.href = '/v4/overview'; }, { ok: 'Clear This Browser', danger: 1, must: 'DELETE', html: '<p class="cf" style="margin-bottom:10px">This clears this browser’s copy. Patients, claims, appointments and visit notes saved on the server come back the next time Althais opens; settings and documents kept only in this browser are deleted.</p>' }); });
    window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  };

  /* ---------- run the page ---------- */
  var page = app.dataset.page;
  if (!V.pages[page]) app.innerHTML = U.header(page) + '<div class="blank">Unknown page.</div>';
  else V.pages[page]();
  U.on(document.body, '#print', 'click', function () { window.print(); });
})();
