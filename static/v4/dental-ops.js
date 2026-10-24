/* Althais v4, the dental EMR, part 4: the practice. Today (the morning huddle, V.pages.dtoday), Messages (dmessages),
   Recall (drecall), Forms (dforms), Reviews (dreviews), Production & collections (dreports), Lab cases (dlabs) and
   Practice setup (dsetup: offices, chairs, providers and hours, fees, membership plans, integrations, imports, sample data).
   Server: dental_engage.py, dental_ops.py, dental_api.py. */
(function () {
  if (!window.V || !window.DA) return;
  var E = U.esc, M = U.money, M0 = U.money0;
  var chartHref = function (mrn, tab) { return '/v4/dental/chart?mrn=' + encodeURIComponent(mrn) + (tab ? '&tab=' + tab : ''); };
  var stop = function (app, page, title, e) { app.innerHTML = DA.frame(page, title) + DA.blank('<b>' + (e.status === 403 ? 'Your role doesn’t include this screen' : 'Couldn’t load') + '</b>' + E(e.message)); U.fit(); };
  var pct = function (a, b) { return b ? Math.round(100 * a / b) : 0; };

  /* ================= Today: the morning huddle ================= */
  V.pages.dtoday = function () {
    var app = V.app, day = V.q.get('date') || DA.today(), H = null;
    function load() { Promise.all([DA.loadSetup(), DA.get('/huddle?date=' + day)]).then(function (r) { H = r[1]; draw(); }, function (e) { stop(app, 'dtoday', 'Today', e); }); }
    function card(title, n, body, href) { return '<div class="hcard"><div class="h">' + title + '<span class="n">' + (href ? '<a href="' + href + '">' + n + '</a>' : n) + '</span></div><div class="bd">' + body + '</div></div>'; }
    function draw() {
      if (!H.location) { app.innerHTML = DA.frame('dtoday', 'Today') + DA.needSample(); U.fit(); return; }
      var A = H.appointments.filter(function (a) { return a.mrn; }), when = new Date(day + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
      var opp = A.filter(function (a) { return a.unscheduled > 0; }).sort(function (a, b) { return b.unscheduled - a.unscheduled; });
      var due = A.filter(function (a) { return (a.flags.balance || 0) > 0; }), alerts = A.filter(function (a) { return (a.flags.allergies && !/^nkda$/i.test(a.flags.allergies)) || /thinner|joint|valve|alendronate|apixaban|warfarin/i.test(a.flags.medical || ''); });
      var row = function (a, right) { return '<div class="hrow" data-go="' + chartHref(a.mrn) + '"><div><div><b>' + DA.time(a.start) + '</b> ' + E(a.patient) + '</div><div class="s">' + E(a.reason || '') + ' · ' + E(a.provider) + '</div></div><div class="r">' + right + '</div></div>'; };
      var goalPct = pct(H.scheduled_production, H.goal_day);
      app.innerHTML = DA.frame('dtoday', 'Today', [['Schedule', 'cal', { href: '/v4/dental/schedule?date=' + day }], ['Verify benefits', 'check', { href: '/v4/dental/benefits?date=' + day }], ['Claims', 'send', { href: '/v4/dental/claims' }]], '<button class="mini" data-nav="-1">‹</button> <button class="mini" data-nav="0">Today</button> <button class="mini" data-nav="1">›</button>' + DA.mock()) +
        '<div class="kpis"><div><span>' + E(when) + '</span><b>' + A.length + ' patients</b><small>' + H.unconfirmed + ' unconfirmed' + (H.online_requests ? ' · ' + H.online_requests + ' online request' : '') + '</small></div>' +
          '<div><span>Scheduled production</span><b>' + M0(H.scheduled_production) + '</b><small>goal ' + M0(H.goal_day) + ' · ' + goalPct + '%</small><div class="meter' + (goalPct < 80 ? ' warn' : '') + '"><i style="width:' + Math.min(100, goalPct) + '%"></i></div></div>' +
          '<div><span>Month to date</span><b>' + M0(H.mtd) + '</b><small>of ' + M0(H.goal_month) + ' goal</small><div class="meter"><i style="width:' + Math.min(100, pct(H.mtd, H.goal_month)) + '%"></i></div></div>' +
          '<div><span>Unscheduled treatment on today’s patients</span><b class="s-blue">' + M0(opp.reduce(function (s, a) { return s + a.unscheduled; }, 0)) + '</b><small>' + opp.length + ' patients to talk to</small></div>' +
          '<div><span>Benefits not verified</span><b class="' + (H.unverified ? 's-amb' : 's-grn') + '">' + H.unverified + '</b><small><a href="/v4/dental/benefits?date=' + day + '">verify now</a></small></div>' +
          '<div><span>Claims ready to send</span><b>' + H.claims_ready + '</b><small>' + H.claims_waiting + ' with payers · ' + M0(H.denied_dollars) + ' denied</small></div></div>' +
        '<div class="huddle" style="height:calc(100vh - var(--chrome, 82px) - 68px);overflow:auto;align-content:start">' +
          card('Today’s patients', A.length, A.map(function (a) { var f = a.flags; return row(a, (f.payer ? (f.elig_active ? '<span class="chip grn">INS</span>' : '<span class="chip amb">INS?</span>') : '<span class="chip">Self</span>') + (f.balance > 0 ? ' <span class="chip red">' + M0(f.balance) + '</span>' : '') + (a.status === 'confirmed' ? ' <span class="s-grn">' + U.I.check + '</span>' : '')); }).join('') || DA.blank('Nobody booked.'), '/v4/dental/schedule?date=' + day) +
          card('Treatment to schedule', M0(opp.reduce(function (s, a) { return s + a.unscheduled; }, 0)), opp.map(function (a) { return row(a, '<b class="s-blue">' + M0(a.unscheduled) + '</b><div class="s">unscheduled</div>'); }).join('') || DA.blank('No open treatment on today’s patients.')) +
          card('Collect at the desk', M0(due.reduce(function (s, a) { return s + a.flags.balance; }, 0)), due.map(function (a) { return row(a, '<b class="s-red">' + M(a.flags.balance) + '</b>'); }).join('') || DA.blank('No balances on today’s patients.'), '/v4/dental/billing') +
          card('Medical alerts', alerts.length, alerts.map(function (a) { return row(a, '<span class="s-red" style="font-size:11.5px">' + E([a.flags.allergies && !/^nkda$/i.test(a.flags.allergies) ? 'Allergy: ' + a.flags.allergies : '', a.flags.medical || ''].filter(Boolean).join(' · ').slice(0, 60)) + '</span>'); }).join('') || DA.blank('None today.')) +
          card('Revenue cycle', H.work_open + ' open', '<div class="hrow" data-go="/v4/dental/claims"><div>Claims ready to send</div><div class="r"><b>' + H.claims_ready + '</b></div></div><div class="hrow" data-go="/v4/dental/claims?q=attention"><div>Claims that need a fix</div><div class="r"><b class="' + (H.claims_attention ? 's-amb' : '') + '">' + H.claims_attention + '</b></div></div>' +
            '<div class="hrow" data-go="/v4/dental/claims?q=sent"><div>Waiting on payers</div><div class="r"><b>' + H.claims_waiting + '</b></div></div><div class="hrow" data-go="/v4/dental/denials"><div>Denials and follow-up</div><div class="r"><b class="' + (H.work_open ? 's-red' : '') + '">' + H.work_open + '</b></div></div>' +
            '<div class="hrow" data-go="/v4/dental/preauths"><div>Pre-auths pending</div><div class="r"><b>' + H.preauth_pending + '</b></div></div><div class="hrow" data-go="/v4/dental/era"><div>Post ERAs</div><div class="r"><a href="/v4/dental/era">open</a></div></div>', '/v4/dental/denials') +
          card('Front desk', H.unread + ' unread', '<div class="hrow" data-go="/v4/dental/messages"><div>Unread texts</div><div class="r"><b class="' + (H.unread ? 's-amb' : '') + '">' + H.unread + '</b></div></div><div class="hrow" data-go="/v4/dental/schedule?date=' + day + '"><div>Unconfirmed appointments</div><div class="r"><b>' + H.unconfirmed + '</b></div></div>' +
            '<div class="hrow" data-go="/v4/dental/recall"><div>Recall list</div><div class="r"><a href="/v4/dental/recall">open</a></div></div><div class="hrow" data-go="/v4/dental/labs"><div>Lab cases due in 3 days</div><div class="r"><b class="' + (H.labs_due.length ? 's-amb' : '') + '">' + H.labs_due.length + '</b></div></div>') +
        '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '[data-nav]', 'click', function (b) { var n = +b.dataset.nav; day = n ? DA.addDays(day, n) : DA.today(); load(); });
    U.on(app, '[data-go]', 'click', function (el, e) { if (!e.target.closest('a')) location.href = el.dataset.go; });
  };

  /* ================= Messages ================= */
  V.pages.dmessages = function () {
    var app = V.app, threads = [], key = V.q.get('mrn') || '', msgs = [];
    function load() { DA.get('/messages').then(function (t) { threads = t; if (!key && t[0]) key = t[0].mrn || t[0].phone; return key ? DA.get('/messages/' + encodeURIComponent(key)).then(function (m) { msgs = m; }) : null; }).then(draw, function (e) { stop(app, 'dmessages', 'Messages', e); }); }
    function draw() {
      var th = threads.filter(function (t) { return (t.mrn || t.phone) === key; })[0];
      app.innerHTML = DA.frame('dmessages', 'Messages', [['New text', 'chat', { pri: 1, id: 'newmsg' }]], DA.mock()) +
        '<div class="main" style="grid-template-columns:280px minmax(0,1fr) 280px">' +
        U.pane('Conversations', threads.length + '', '', threads.map(function (t) { var k = t.mrn || t.phone; return '<div class="hrow" data-th="' + E(k) + '" style="' + (k === key ? 'background:var(--sel)' : '') + '"><div style="min-width:0"><div><b>' + E(t.patient) + '</b></div><div class="s" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:190px">' + E(t.last) + '</div></div><div class="r"><div class="s">' + DA.short(t.at) + '</div>' + (t.unread ? '<span class="chip amb">' + t.unread + '</span>' : '') + '</div></div>'; }).join('') || DA.blank('No texts yet.')) +
        '<section class="pane"><div class="ph">' + (th ? E(th.patient) : 'Conversation') + (th && th.mrn ? '<span class="end"><a href="' + chartHref(th.mrn) + '">Chart</a></span>' : '') + '</div><div class="pbody"><div class="thread">' + msgs.map(function (m) { return '<div class="bub ' + m.direction + '">' + E(m.body) + '<small>' + DA.short(m.created) + ' ' + m.created.slice(11, 16) + ' · ' + E(m.kind) + (m.direction === 'out' ? ' · ' + E(m.status) : '') + '</small></div>'; }).join('') + '</div></div>' +
          (th && th.mrn ? '<div class="compose"><input id="body" placeholder="Text ' + E(DA.first(th.patient)) + '… (no clinical details by text)"><button class="btn pri" id="send">Send</button></div>' : '') + '</section>' +
        U.pane('Testing', '', '', '<div class="dp-sec mut" style="font-size:12px">Texts go through the SMS vendor (a mock: nothing reaches a phone). Pretend the patient replied: "C" confirms their next visit, "STOP" opts them out.</div>' + (th ? '<div class="dp-sec"><div class="compose" style="padding:0;border:0"><input id="sim" placeholder="Patient replies…"><button class="mini" id="simgo">Reply</button></div></div>' : '')) + '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '[data-th]', 'click', function (el) { key = el.dataset.th; DA.get('/messages/' + encodeURIComponent(key)).then(function (m) { msgs = m; return DA.get('/messages'); }).then(function (t) { threads = t; draw(); }); });
    U.on(app, '#send', 'click', function () { var b = document.getElementById('body').value.trim(); if (!b) return; DA.post('/patients/' + encodeURIComponent(key) + '/messages', { body: b }).then(load, DA.fail); });
    U.on(app, '#body', 'keydown', function (i, e) { if (e.key === 'Enter') document.getElementById('send').click(); });
    U.on(app, '#simgo', 'click', function () { var th = threads.filter(function (t) { return (t.mrn || t.phone) === key; })[0], v = document.getElementById('sim').value.trim(); if (!v) return; DA.post('/sms/simulate', { phone: th.phone, body: v }).then(function (r) { U.toast(r.action === 'confirmed' ? 'Appointment confirmed by text.' : r.action === 'opted_out' ? 'Patient opted out of texts.' : 'Reply received.', 'ok'); load(); }, DA.fail); });
    U.on(app, '#newmsg', 'click', function () { DA.pickPatient('Text which patient?', function (m) { key = m; msgs = []; threads = threads.filter(function (t) { return t.mrn !== m; }).concat([{ mrn: m, phone: '', patient: m, last: '', at: '', unread: 0 }]); load(); }); });
  };

  /* ================= Recall ================= */
  V.pages.drecall = function () {
    var app = V.app, list = [], camps = [], win = 30, picked = {}, only = 'overdue';
    var TPL = 'Hi {first}, it’s time for your {type} at {office}. Book online: {link} or call {phone}. Reply STOP to opt out.';
    function load() { Promise.all([DA.get('/recall?window=' + win), DA.get('/campaigns')]).then(function (r) { list = r[0]; camps = r[1]; draw(); }, function (e) { stop(app, 'drecall', 'Recall', e); }); }
    function draw() {
      var rows = list.filter(function (p) { return only === 'all' || (only === 'overdue' ? p.overdue_days > 0 : p.overdue_days === 0); });
      app.innerHTML = DA.frame('drecall', 'Hygiene recall', [['Text selected (' + Object.keys(picked).filter(function (k) { return picked[k]; }).length + ')', 'send', { pri: 1, id: 'send' }]], '<label class="chipf">Show <select id="only"><option value="overdue"' + (only === 'overdue' ? ' selected' : '') + '>Overdue</option><option value="soon"' + (only === 'soon' ? ' selected' : '') + '>Due in ' + win + ' days</option><option value="all"' + (only === 'all' ? ' selected' : '') + '>All</option></select></label>' + DA.mock()) +
        '<div class="kpis" style="grid-template-columns:repeat(4,1fr)"><div><span>Overdue, nothing booked</span><b class="s-amb">' + list.filter(function (p) { return p.overdue_days > 0; }).length + '</b></div><div><span>Due in the next ' + win + ' days</span><b>' + list.filter(function (p) { return p.overdue_days === 0; }).length + '</b></div><div><span>Perio maintenance patients</span><b>' + list.filter(function (p) { return p.type === 'Perio maintenance'; }).length + '</b><small>every 3 months</small></div><div><span>Last campaign</span><b>' + (camps[0] ? camps[0].booked + ' / ' + camps[0].sent : '-') + '</b><small>booked / texted</small></div></div>' +
        '<div class="main" style="grid-template-columns:minmax(0,1fr) 330px">' +
        U.pane('Due and overdue', rows.length + ' with no hygiene visit booked', '', U.grid(['Patient', 'Type', 'Last visit', 'Due', 'Overdue', 'Mobile', 'Last texted'], rows.map(function (p) {
          return { checked: !!picked[p.mrn], attrs: ' data-pk="' + E(p.mrn) + '"', cells: [V.link(p.patient, chartHref(p.mrn)), E(p.type), DA.us(p.last), DA.us(p.due), p.overdue_days ? '<span class="' + (p.overdue_days > 90 ? 's-red' : 's-amb') + '">' + p.overdue_days + ' days</span>' : '', p.opted_out ? '<span class="s-red">Opted out</span>' : E(p.phone), p.contacted ? DA.us(p.contacted) : ''] };
        }), { cb: 1, selChecks: false }).replace('<tbody></tbody>', '<tbody>' + U.empty(8, 'Everyone is booked. Nice.') + '</tbody>')) +
        U.pane('Recall text', '', '', '<div class="dp-sec"><textarea class="ed" id="tpl" rows="6" style="width:100%">' + E(TPL) + '</textarea><div class="mut" style="font-size:11.5px;margin-top:4px">{first} {type} {office} {link} {phone} are filled in for each patient. The link opens online booking.</div></div>' +
          '<div class="dp-sec"><div class="dp-h">Campaigns</div>' + (camps.map(function (c) { return '<div style="display:flex;justify-content:space-between;padding:2px 0"><span>' + E(c.name) + '</span><span class="mut">' + c.booked + ' booked / ' + c.sent + ' sent</span></div>'; }).join('') || '<span class="mut">None yet</span>') + '</div>') + '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '#only', 'change', function (s) { only = s.value; draw(); });
    U.on(app, 'tr[data-pk] input[type=checkbox]', 'change', function (cb) { picked[cb.closest('tr').dataset.pk] = cb.checked; });
    U.on(app, '[data-cball]', 'change', function (cb) { app.querySelectorAll('tr[data-pk]').forEach(function (tr) { picked[tr.dataset.pk] = cb.checked; }); draw(); });
    U.on(app, '#send', 'click', function () { var m = Object.keys(picked).filter(function (k) { return picked[k]; }); if (!m.length) return U.toast('Tick the patients to text.', 'warn'); DA.post('/recall/campaign', { mrns: m, template: document.getElementById('tpl').value }).then(function (r) { picked = {}; U.toast(r.sent + ' recall texts sent.', 'ok'); load(); }, DA.fail); });
  };

  /* ================= Forms ================= */
  V.pages.dforms = function () {
    var app = V.app, F = null;
    function load() { DA.get('/forms').then(function (f) { F = f; draw(); }, function (e) { stop(app, 'dforms', 'Forms', e); }); }
    function draw() {
      app.innerHTML = DA.frame('dforms', 'Intake and medical history forms', [['Send forms to a patient', 'send', { pri: 1, id: 'send' }]], DA.mock()) +
        '<div class="main" style="grid-template-columns:320px minmax(0,1fr)">' +
        U.pane('Forms', F.templates.length + '', '', F.templates.map(function (t) { return '<div class="dp-sec"><div class="dp-h">' + E(t.name) + '</div><div class="mut" style="font-size:12px">' + t.fields.filter(function (f) { return f.type !== 'signature'; }).map(function (f) { return E(f.label); }).join(' · ') + '</div><div class="mut" style="font-size:11.5px;margin-top:4px">Writes into the chart: ' + (t.fields.filter(function (f) { return f.maps_to || f.flag; }).map(function (f) { return f.maps_to || 'alert'; }).join(', ') || 'a signed copy') + '</div></div>'; }).join('')) +
        U.pane('Sent and completed', F.submissions.length + '', '', U.grid(['Sent', 'Patient', 'Form', 'Status', 'Completed'], F.submissions.map(function (s) { return { cells: [DA.us(s.created), V.link(s.patient, chartHref(s.mrn, 'forms')), E(s.form), DA.statusChip(s.status === 'applied' ? 'completed' : s.status === 'sent' ? 'pending' : s.status), DA.us(s.submitted_at)] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'No forms sent yet.') + '</tbody>') +
          '<div class="dp-sec mut" style="font-size:12px">Answers go straight into the chart as patient-reported (allergies, medications, conditions, alerts like blood thinners or bisphosphonates), and the chart flags the update for the dentist to review.</div>') + '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '#send', 'click', function () { DA.pickPatient('Send forms to', function (mrn) { U.form('Send forms', F.templates.map(function (t) { return { k: 't' + t.id, label: t.name, type: 'check', span: 2, def: true }; }), {}, function (v) { var ids = F.templates.filter(function (t) { return v['t' + t.id]; }).map(function (t) { return t.id; }); DA.post('/patients/' + encodeURIComponent(mrn) + '/forms', { template_ids: ids }).then(function (r) { U.toast(r.sent.length + ' form link(s) texted. Open one to try it: ' + r.sent[0].url, 'ok'); window.open(r.sent[0].url, '_blank'); load(); }, DA.fail); }, { save: 'Text the link' }); }); });
  };

  /* ================= Reviews ================= */
  V.pages.dreviews = function () {
    var app = V.app;
    DA.get('/reviews').then(function (R) {
      var stars = function (n) { return n ? '<span style="color:#d29a00;letter-spacing:1px">' + '★★★★★'.slice(0, n) + '<span style="color:var(--b)">' + '★★★★★'.slice(n) + '</span></span>' : ''; };
      app.innerHTML = DA.frame('dreviews', 'Reviews', [], '') +
        '<div class="kpis" style="grid-template-columns:repeat(4,1fr)"><div><span>Requests sent</span><b>' + R.sent + '</b></div><div><span>Rated</span><b>' + R.rated + '</b><small>' + pct(R.rated, R.sent) + '% response</small></div><div><span>Average rating</span><b>' + (R.average || '-') + '</b>' + stars(Math.round(R.average || 0)) + '</div><div><span>Follow-ups</span><b class="s-amb">' + R.requests.filter(function (r) { return r.rating && r.rating <= 3; }).length + '</b><small>3 stars or less</small></div></div>' +
        '<div class="main" style="grid-template-columns:minmax(0,1fr)">' + U.pane('Post-visit review requests', 'sent from the schedule after a completed visit; every patient sees the public review link', '', U.grid(['Sent', 'Patient', 'Rating', 'Comment', 'Status'], R.requests.map(function (r) { return { cells: [DA.us(r.created), V.link(r.patient, chartHref(r.mrn)), stars(r.rating), '<span class="wrap">' + E(r.comment || '') + '</span>', E(r.status)] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(5, 'None yet.') + '</tbody>')) + '</div>';
      U.fit(); U.search();
    }, function (e) { stop(app, 'dreviews', 'Reviews', e); });
  };

  /* ================= Production & collections ================= */
  V.pages.dreports = function () {
    var app = V.app, t = DA.today(), range = V.q.get('range') || 'mtd', loc = 0, R = null;
    var RANGES = { mtd: ['Month to date', t.slice(0, 8) + '01', t], last: ['Last month', '', ''], qtd: ['Last 90 days', DA.addDays(t, -90), t], ytd: ['Year to date', t.slice(0, 4) + '-01-01', t] };
    (function () { var d = new Date(t.slice(0, 8) + '01T12:00:00'); d.setDate(0); var end = d.toISOString().slice(0, 10); RANGES.last[1] = end.slice(0, 8) + '01'; RANGES.last[2] = end; })();
    function load() { var r = RANGES[range]; Promise.all([DA.loadSetup(), DA.get('/reports/production?frm=' + r[1] + '&to=' + r[2] + '&location_id=' + loc)]).then(function (x) { R = x[1]; draw(); }, function (e) { stop(app, 'dreports', 'Production & collections', e); }); }
    function draw() {
      var T = R.totals, max = Math.max.apply(null, R.by_day.map(function (d) { return Math.max(d.production, d.collections); }).concat([1]));
      var provMax = Math.max.apply(null, R.providers.map(function (p) { return Math.max(p.gross, p.goal_production || 0); }).concat([1]));
      var cats = Object.keys(R.by_category), catMax = Math.max.apply(null, cats.map(function (k) { return R.by_category[k]; }).concat([1]));
      app.innerHTML = DA.frame('dreports', 'Production & collections', [['Export CSV', 'download', { id: 'csv' }]], '<label class="chipf">Period <select id="rng">' + Object.keys(RANGES).map(function (k) { return '<option value="' + k + '"' + (k === range ? ' selected' : '') + '>' + RANGES[k][0] + '</option>'; }).join('') + '</select></label> ' +
        (DA.setup.locations.length > 1 ? '<label class="chipf">Office <select id="loc"><option value="0">All offices</option>' + DA.setup.locations.map(function (l) { return '<option value="' + l.id + '"' + (l.id === loc ? ' selected' : '') + '>' + E(l.name) + '</option>'; }).join('') + '</select></label>' : '')) +
        '<div class="kpis"><div><span>Gross production</span><b>' + M0(T.gross) + '</b><small>' + M0(T.per_day) + ' per day</small></div><div><span>Net production</span><b>' + M0(T.net) + '</b><small>after write-offs</small></div><div><span>Collections</span><b class="s-grn">' + M0(T.collections) + '</b><small>' + (T.collection_rate != null ? T.collection_rate + '% of net' : '') + '</small></div>' +
          '<div><span>Case acceptance</span><b>' + (T.case_acceptance != null ? T.case_acceptance + '%' : '-') + '</b><small>' + M0(T.accepted) + ' of ' + M0(T.presented) + '</small></div><div><span>Clean claim rate</span><b>' + (T.clean_claim_rate != null ? T.clean_claim_rate + '%' : '-') + '</b><small>' + (T.days_to_pay != null ? T.days_to_pay + ' days to pay' : '') + '</small></div><div><span>Hygiene reappointment</span><b>' + (T.hygiene_reappointment != null ? T.hygiene_reappointment + '%' : '-') + '</b><small>' + T.new_patients + ' new patients</small></div></div>' +
        '<div class="main" style="grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);height:calc(100vh - var(--chrome, 82px) - 68px)">' +
        '<section class="pane"><div class="ph">By day <span class="sub"><span style="color:var(--blue)">■</span> production <span style="color:var(--grn)">■</span> collections</span></div><div class="pbody"><div class="bars">' + R.by_day.map(function (d) { return '<div class="b" title="' + DA.us(d.date) + ': production ' + M0(d.production) + ', collections ' + M0(d.collections) + '"><i style="height:' + Math.round(116 * d.production / max) + 'px"></i><i class="c" style="height:' + Math.round(116 * d.collections / max) + 'px"></i></div>'; }).join('') + '</div><div class="bars-x">' + R.by_day.map(function (d, i) { return '<span>' + (i % Math.ceil(R.by_day.length / 12) === 0 ? DA.short(d.date) : '') + '</span>'; }).join('') + '</div>' +
          U.sect('Providers', 'production against this month’s goal', '<div class="dpad">' + R.providers.map(function (p) { return '<div class="hbar"><span class="l"><span style="color:' + p.color + '">●</span> ' + E(p.provider) + '</span><span class="m"><i style="width:' + (100 * p.gross / provMax) + '%;background:' + p.color + '"></i>' + (p.goal_production ? '<em style="left:' + (100 * p.goal_production / provMax) + '%" title="Goal ' + M0(p.goal_production) + '"></em>' : '') + '</span><span class="v">' + M0(p.gross) + (p.goal_pct != null ? ' <span class="mut">' + p.goal_pct + '%</span>' : '') + '</span></div>'; }).join('') + '</div>' +
            U.grid(['Provider', ['Gross', 'num'], ['Net', 'num'], ['Collected', 'num'], ['Coll. %', 'num'], ['Procedures', 'num'], ['Patients', 'num'], ['Goal', 'num'], ''], R.providers.map(function (p) { return { cells: [E(p.provider), M0(p.gross), M0(p.net), M0(p.collections), p.collection_rate != null ? p.collection_rate + '%' : '', p.procedures, p.patients, p.goal_production ? M0(p.goal_production) : '', p.provider_id && DA.setup.role === 'admin' ? '<button class="mini" data-goal="' + p.provider_id + '">Set goal</button>' : ''] }; }), { tight: 1 })) + '</div></section>' +
        '<section class="pane"><div class="ph">By category</div><div class="pbody"><div class="dpad">' + cats.map(function (k) { return '<div class="hbar"><span class="l" style="text-transform:capitalize">' + E(k.replace('_', ' ')) + '</span><span class="m"><i style="width:' + (100 * R.by_category[k] / catMax) + '%"></i></span><span class="v">' + M0(R.by_category[k]) + '</span></div>'; }).join('') + '</div>' +
          U.sect('Offices', '', U.grid(['Office', ['Production', 'num'], ['Collections', 'num']], R.by_location.map(function (l) { return { cells: [E(l.location), M0(l.production), M0(l.collections)] }; }), { tight: 1 })) +
          U.sect('Claims', '', '<div class="dpad">' + U.kv([['Clean claim rate', T.clean_claim_rate != null ? T.clean_claim_rate + '%' : '-'], ['Denial rate', T.denial_rate != null ? T.denial_rate + '%' : '-'], ['Average days to pay', T.days_to_pay != null ? T.days_to_pay : '-']]) + '</div>') + '</div></section></div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '#rng', 'change', function (s) { range = s.value; load(); });
    U.on(app, '#loc', 'change', function (s) { loc = +s.value; load(); });
    U.on(app, '[data-goal]', 'click', function (b) { U.form('Monthly goal', [{ k: 'production', label: 'Production goal', type: 'number', req: 1 }, { k: 'collections', label: 'Collections goal', type: 'number' }], {}, function (v) { DA.post('/goals', { provider_id: +b.dataset.goal, month: DA.today().slice(0, 7), production: +v.production, collections: +v.collections || +v.production * .97 }).then(function () { U.toast('Goal saved.', 'ok'); load(); }, DA.fail); }); });
    U.on(app, '#csv', 'click', function () { U.download('production-' + R.from + '-' + R.to + '.csv', U.csv([['Provider', 'Gross', 'Net', 'Collections', 'Procedures', 'Patients', 'Goal']].concat(R.providers.map(function (p) { return [p.provider, p.gross, p.net, p.collections, p.procedures, p.patients, p.goal_production || '']; })))); });
  };

  /* ================= Lab cases ================= */
  V.pages.dlabs = function () {
    var app = V.app, list = [];
    var COLS = [['to_send', 'To send'], ['sent', 'At the lab'], ['received', 'Back, ready to seat'], ['seated', 'Seated'], ['remake', 'Remake']];
    function load() { DA.get('/labs').then(function (l) { list = l; draw(); }, function (e) { stop(app, 'dlabs', 'Lab cases', e); }); }
    function draw() {
      app.innerHTML = DA.frame('dlabs', 'Lab cases', [['New lab case', 'plus', { pri: 1, id: 'new' }]], '') +
        '<div class="main" style="grid-template-columns:repeat(5,minmax(0,1fr))">' + COLS.map(function (c) {
          var its = list.filter(function (x) { return x.status === c[0]; });
          return U.pane(c[1], its.length + '', '', its.map(function (x) { return '<div class="dp-sec"><div class="dp-h">' + E(x.item) + (x.tooth ? ' #' + E(x.tooth) : '') + '</div><div>' + V.link(x.patient, chartHref(x.mrn, 'rx')) + '</div><div class="mut" style="font-size:12px">' + E(x.lab) + (x.shade ? ' · shade ' + E(x.shade) : '') + '</div>' +
            '<div style="font-size:12px;margin-top:3px">Due ' + DA.us(x.due) + (x.late ? ' <span class="chip red">late</span>' : '') + (x.risk ? ' <span class="chip amb">due after the seat date</span>' : '') + (x.seat ? '<br>Seat ' + DA.us(x.seat) + ' ' + DA.time(x.seat) : '') + '</div>' +
            '<div style="display:flex;gap:4px;margin-top:6px;flex-wrap:wrap">' + COLS.filter(function (n) { return n[0] !== c[0]; }).slice(0, 3).map(function (n) { return '<button class="mini" data-lab="' + x.id + '" data-to="' + n[0] + '">' + n[1].split(',')[0] + '</button>'; }).join('') + '</div></div>'; }).join('') || '<div class="dp-sec mut">None</div>');
        }).join('') + '</div>';
      U.fit(); U.search();
    }
    load();
    U.on(app, '[data-lab]', 'click', function (b) { DA.post('/labs', { id: +b.dataset.lab, status: b.dataset.to }).then(load, DA.fail); });
    U.on(app, '#new', 'click', function () { DA.pickPatient('Lab case for', function (mrn) { U.form('New lab case', [{ k: 'item', label: 'Item', req: 1, ph: 'Crown, zirconia' }, { k: 'tooth', label: 'Tooth' }, { k: 'shade', label: 'Shade' }, { k: 'lab', label: 'Lab', req: 1 }, { k: 'due', label: 'Due back', type: 'date', req: 1 }, { k: 'cost', label: 'Lab fee', type: 'number', step: '0.01' }], {}, function (v) { DA.post('/labs', Object.assign(v, { mrn: mrn, cost: +v.cost || 0 })).then(load, DA.fail); }); }); });
  };

  /* ================= Practice setup ================= */
  V.pages.dsetup = function () {
    var app = V.app, sec = V.q.get('sec') || 'locations', S = null, imp = { source: 'opendental', kind: 'patients', text: '', preview: null }, jobs = [], OD = null;
    var SECS = [['locations', 'Offices'], ['operatories', 'Chairs'], ['providers', 'Providers'], ['hours', 'Provider hours'], ['fees', 'Fee schedule'], ['membership-plans', 'Membership plans'], ['integrations', 'Integrations'], ['import', 'Import data'], ['sample', 'Synthetic practice']];
    var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    function load() { DA._setup = null; Promise.all([DA.loadSetup(), sec === 'import' ? DA.get('/import/jobs').catch(function () { return []; }) : [], sec === 'import' ? DA.get('/import/opendental').catch(function () { return null; }) : null]).then(function (r) { S = r[0]; jobs = r[1]; OD = r[2]; draw(); }, function (e) { stop(app, 'dsetup', 'Practice setup', e); }); }
    var admin = function () { return S.role === 'admin'; };
    function grid(cols, rows, kind, fields) {
      return U.grid(cols.concat(admin() ? [''] : []), rows.map(function (r) { return { cells: r.cells.concat(admin() ? ['<button class="mini" data-edit="' + kind + '" data-id="' + r.id + '">Edit</button> <button class="mini" data-delk="' + kind + '" data-id="' + r.id + '">' + U.I.x + '</button>'] : []) }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(cols.length + 1, 'None yet.') + '</tbody>') +
        (admin() ? '<div class="dpad"><button class="btn" data-add="' + kind + '">' + U.I.plus + 'Add</button></div>' : '');
    }
    var locName = function (id) { return (S.locations.filter(function (l) { return l.id === id; })[0] || {}).name || ''; }, opName = function (id) { return (S.operatories.filter(function (o) { return o.id === id; })[0] || {}).name || ''; }, provName = function (id) { return (S.providers.filter(function (p) { return p.id === id; })[0] || {}).name || ''; };
    var FIELDS = {
      locations: function () { return [{ k: 'name', label: 'Office name', req: 1, span: 2 }, { k: 'phone', label: 'Phone' }, { k: 'region', label: 'Region (groups)' }, { k: 'addr', label: 'Street', span: 2 }, { k: 'city', label: 'City' }, { k: 'state', label: 'State' }, { k: 'zip', label: 'ZIP' }, { k: 'npi', label: 'Group NPI', help: '10 digits' }, { k: 'tin', label: 'Tax ID' }, { k: 'online_booking', label: 'Online booking on', type: 'select', options: [[1, 'Yes'], [0, 'No']] }]; },
      operatories: function () { return [{ k: 'location_id', label: 'Office', type: 'select', options: S.locations.map(function (l) { return [l.id, l.name]; }) }, { k: 'name', label: 'Chair name', req: 1, ph: 'Op 3' }, { k: 'kind', label: 'Used for', type: 'select', options: ['restorative', 'hygiene', 'surgery', 'consult'] }, { k: 'sort', label: 'Order', type: 'number' }]; },
      providers: function () { return [{ k: 'name', label: 'Name as shown', req: 1, span: 2, ph: 'Dr. Maya Okafor' }, { k: 'first', label: 'First name' }, { k: 'last', label: 'Last name' }, { k: 'cred', label: 'Credentials', ph: 'DDS, DMD, RDH' }, { k: 'kind', label: 'Role', type: 'select', options: ['dentist', 'hygienist', 'specialist'] }, { k: 'npi', label: 'NPI' }, { k: 'dea', label: 'DEA # (for controlled Rx)' }, { k: 'color', label: 'Schedule color', type: 'color' }, { k: 'user_email', label: 'Their Althais login (email)', span: 2 }]; },
      hours: function () { return [{ k: 'provider_id', label: 'Provider', type: 'select', options: S.providers.map(function (p) { return [p.id, p.name]; }) }, { k: 'location_id', label: 'Office', type: 'select', options: S.locations.map(function (l) { return [l.id, l.name]; }) }, { k: 'operatory_id', label: 'Chair', type: 'select', options: S.operatories.map(function (o) { return [o.id, locName(o.location_id) + ' · ' + o.name]; }) }, { k: 'weekday', label: 'Day', type: 'select', options: DAYS.map(function (d, i) { return [i, d]; }) }, { k: 'start', label: 'Start', type: 'time' }, { k: 'end', label: 'End', type: 'time' }, { k: 'lunch_start', label: 'Lunch from', type: 'time' }, { k: 'lunch_end', label: 'Lunch to', type: 'time' }]; },
      fees: function () { return [{ k: 'code', label: 'CDT code', req: 1 }, { k: 'fee', label: 'Office fee', type: 'number', step: '0.01', req: 1 }, { k: 'desc', label: 'Description', span: 2 }, { k: 'minutes', label: 'Chair minutes', type: 'number' }]; },
      'membership-plans': function () { return [{ k: 'name', label: 'Plan name', req: 1, span: 2 }, { k: 'monthly', label: 'Monthly price', type: 'number', step: '0.01' }, { k: 'annual', label: 'Annual price', type: 'number', step: '0.01' }, { k: 'discount_pct', label: 'Discount on other care (%)', type: 'number' }, { k: 'included_txt', label: 'Included per year', span: 3, ph: 'D0120 x2, D1110 x2, D0274 x1' }]; },
    };
    var LIST = { locations: 'locations', operatories: 'operatories', providers: 'providers', hours: 'hours', fees: 'fees', 'membership-plans': 'membership_plans' };
    function body() {
      if (sec === 'locations') return grid(['Office', 'Address', 'Phone', 'NPI', 'Region', 'Online booking'], S.locations.map(function (l) { return { id: l.id, cells: [E(l.name), E(l.addr + ', ' + l.city + ', ' + l.state), E(l.phone), '<span class="mono">' + E(l.npi) + '</span>', E(l.region), l.online_booking ? 'On' : 'Off'] }; }), 'locations');
      if (sec === 'operatories') return grid(['Office', 'Chair', 'Used for'], S.operatories.map(function (o) { return { id: o.id, cells: [E(locName(o.location_id)), E(o.name), E(o.kind)] }; }), 'operatories');
      if (sec === 'providers') return grid(['Name', 'Role', 'NPI', 'DEA', 'Color', 'Login'], S.providers.map(function (p) { return { id: p.id, cells: [E(p.name), E(p.kind), '<span class="mono">' + E(p.npi) + '</span>', E(p.dea ? 'on file' : ''), '<span style="color:' + p.color + '">■</span> ' + E(p.color), E(p.user_email)] }; }), 'providers');
      if (sec === 'hours') return grid(['Provider', 'Day', 'Office', 'Chair', 'Hours', 'Lunch'], S.hours.slice().sort(function (a, b) { return a.provider_id - b.provider_id || a.weekday - b.weekday; }).map(function (h) { return { id: h.id, cells: [E(provName(h.provider_id)), DAYS[h.weekday], E(locName(h.location_id)), E(opName(h.operatory_id)), h.start + '–' + h.end, h.lunch_start ? h.lunch_start + '–' + h.lunch_end : ''] }; }), 'hours') + '<div class="dpad mut" style="font-size:12px">Online booking offers only these hours, in these chairs, around existing appointments.</div>';
      if (sec === 'fees') return grid(['Code', 'Description', ['Fee', 'num'], ['Minutes', 'num']], S.fees.map(function (f) { return { id: f.id, cells: ['<span class="mono">' + E(f.code) + '</span>', E(f.desc), M(f.fee), f.minutes] }; }), 'fees');
      if (sec === 'membership-plans') return grid(['Plan', ['Monthly', 'num'], ['Annual', 'num'], 'Included per year', 'Discount'], S.membership_plans.map(function (p) { return { id: p.id, cells: [E(p.name), M(p.monthly), M(p.annual), E(Object.keys(p.included || {}).map(function (k) { return k + ' x' + p.included[k]; }).join(', ')), p.discount_pct + '%'] }; }), 'membership-plans');
      if (sec === 'integrations') return U.grid(['Service', 'Set to', 'Status', 'What a real one needs'], S.vendors.map(function (v) {
        var need = { clearinghouse: 'Claim.MD is built (eligibility, 837D, predeterminations, ERAs): set DENTAL_VENDOR_CLEARINGHOUSE=claimmd with Althais’s Claim.MD account connected in Admin. Payer enrollment for ERA.', attachments: 'Claim.MD is built (X-rays, perio charts and narratives as one PDF with the claim): DENTAL_VENDOR_ATTACHMENTS=claimmd.', sms: 'Twilio or Bandwidth number, 10DLC registration, BAA', payments: 'Stripe or Rectangle Health merchant account; card form tokenizes in the browser', radiograph_ai: 'Overjet, Pearl or VideaHealth contract and BAA (FDA-cleared)', transcriber: 'Deepgram / AssemblyAI / AWS Transcribe Medical with a BAA', erx: 'DoseSpot or DrFirst; EPCS identity proofing for each prescriber', imaging: 'Bridge software for each sensor/pano/CBCT/scanner brand', reviews: 'Google Business Profile link per office' }[v.kind];
        return { cells: [E(v.kind.replace('_', ' ')), E(v.vendor), v.mock ? '<span class="mockflag">MOCK</span>' : '<span class="s-grn">Live</span>', '<span class="wrap mut" style="font-size:12px">' + E(need) + '</span>'] };
      }), { tight: 1 }) + '<div class="dpad mut" style="font-size:12px">Each service sits behind an interface (dental_vendors.py). Switch with DENTAL_VENDOR_&lt;KIND&gt;. Mocks never send anything; the real ones refuse synthetic patients.</div>';
      if (sec === 'import') {
        var p = imp.preview;
        return odPanel() + '<div class="dpad"><div class="form" style="padding:0;grid-template-columns:repeat(4,minmax(0,1fr))"><div><label>From</label><select id="isrc">' + [['opendental', 'Open Dental'], ['dentrix', 'Dentrix'], ['eaglesoft', 'Eaglesoft'], ['csv', 'Other (CSV)']].map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === imp.source ? ' selected' : '') + '>' + o[1] + '</option>'; }).join('') + '</select></div><div><label>What</label><select id="ikind"><option value="patients"' + (imp.kind === 'patients' ? ' selected' : '') + '>Patients</option><option value="procedures"' + (imp.kind === 'procedures' ? ' selected' : '') + '>Procedure history</option></select></div><div style="grid-column:span 2;display:flex;align-items:flex-end;gap:6px"><button class="btn" id="ifile">' + U.I.upload + 'Choose export file</button><button class="btn" id="iprev">Preview</button>' + (p && p.count ? '<button class="btn pri" id="icommit">Import ' + p.count + ' rows</button>' : '') + '</div></div>' +
          '<textarea class="ed" id="itext" rows="6" style="width:100%;margin-top:8px;font-family:var(--mono);font-size:11.5px" placeholder="Or paste the CSV export here. Import patients first, then their procedure history.">' + E(imp.text) + '</textarea>' +
          '<div class="mut" style="font-size:12px;margin-top:4px">Open Dental: patient and procedurelog exports (PatNum, LName, FName, Birthdate, ProcDate, ProcCode, ToothNum, Surf, ProcFee, ProcStatus). Dentrix and Eaglesoft: their patient list and procedure history CSV reports. Headers are matched by name; anything not recognized is listed, not guessed.</div></div>' +
          (p ? U.sect('Preview', p.count + ' rows, ' + p.errors.length + ' problems', '<div class="dpad">' + U.kv([['Matched columns', E(Object.keys(p.mapping).map(function (k) { return k + ' ← ' + p.mapping[k]; }).join(', '))], ['Not used', E(p.unmapped.join(', ') || 'none')]]) + (p.errors.length ? '<div class="s-amb" style="margin-top:6px">' + p.errors.slice(0, 8).map(E).join('<br>') + '</div>' : '') + '</div>' +
            U.grid(Object.keys(p.rows[0] || {}), p.rows.slice(0, 12).map(function (r) { return { cells: Object.keys(r).map(function (k) { return E(r[k]); }) }; }), { tight: 1 })) : '') +
          U.sect('Past imports', '', U.grid(['When', 'From', 'What', 'Rows', 'Result', 'By'], jobs.map(function (j) { return { cells: [DA.us(j.created), E(j.source), E(j.kind), j.rows, E(j.summary.added + ' added, ' + j.summary.updated + ' updated, ' + j.summary.skipped + ' skipped'), E(j.by)] }; }), { tight: 1 }).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'None.') + '</tbody>'));
      }
      if (sec === 'sample') return '<div class="dpad" style="max-width:640px;line-height:1.6">The synthetic practice is made-up data for trying every screen and for demos: two offices, four providers, 48 patients with two years of history, perio charts, treatment plans, a full schedule this week and next, and claims that went through eligibility, the 837D, the mock clearinghouse and ERA posting. Names, phone numbers (555-01xx) and IDs are fictional; images say SYNTHETIC.<div style="display:flex;gap:8px;margin-top:12px"><button class="btn pri" id="ld-sample">Load synthetic practice</button><button class="btn" id="rm-sample">Remove synthetic data</button></div></div>';
      return '';
    }
    // Open Dental, live: patients, their plans and completed procedures straight from the office's database (opendental.py)
    function odPanel() {
      if (!OD) return '';
      var head = '<div class="dp-h" style="margin-bottom:6px">Open Dental, live ' + (OD.connected ? '<span class="s-grn" style="font-weight:500">· Connected' + (OD.clinic_num ? ', clinic ' + E(OD.clinic_num) : '') + '</span>' : '') + '</div>';
      var body;
      if (!OD.available) body = '<div class="mut">Built. It switches on once Althais’s Open Dental developer key is set up; until then, use exports below. Open Dental charges the office a monthly API fee per location.</div>';
      else if (!OD.connected) body = '<div class="mut" style="margin-bottom:8px">Reads patients, their primary dental plan and completed procedures (with teeth and surfaces). Nothing in Open Dental is changed. Althais generates a Customer Key for your office; add it in Open Dental (Setup › Advanced Setup › API › Add Key), then here.</div>' +
        (admin() ? '<button class="btn pri sm" id="od-connect">Connect Open Dental</button>' : '');
      else {
        var ls = OD.last_summary || {};
        body = '<div class="mut" style="margin-bottom:8px">' + (OD.last_pull ? 'Last pull ' + DA.us(OD.last_pull.slice(0, 10)) + ': ' + (ls.patients || 0) + ' patients, ' + (ls.procedures || 0) + ' new procedures.' : 'Connected. Pull to bring patients and procedure history in.') +
          ' Patients come in as MRN OD + their Open Dental number, the same as an export, so pulling again updates them.</div>' +
          (admin() ? '<button class="btn pri sm" data-odpull="30">Pull Last 30 Days</button> <button class="btn sm" data-odpull="120">Pull Last 120 Days</button> <button class="btn sm" id="od-off">Disconnect</button>' : '');
      }
      return '<div class="dpad" style="border-bottom:1px solid var(--b2)">' + head + body + '</div>';
    }
    function draw() {
      app.innerHTML = DA.frame('dsetup', 'Practice setup', [], admin() ? '' : '<span class="mut">Only admins can change setup</span>') +
        '<div class="main" style="grid-template-columns:210px minmax(0,1fr)">' + U.pane('Setup', '', '', U.queue([{ h: '', items: SECS.map(function (s) { return { key: s[0], label: s[1], color: null, n: LIST[s[0]] ? (S[LIST[s[0]]] || []).length : '' }; }) }], sec)) +
        U.pane(SECS.filter(function (s) { return s[0] === sec; })[0][1], '', '', body()) + '</div>';
      U.fit(); U.search();
    }
    function edit(kind, row) {
      var f = FIELDS[kind](), v = Object.assign({}, row || {});
      if (kind === 'membership-plans') v.included_txt = Object.keys(v.included || {}).map(function (k) { return k + ' x' + v.included[k]; }).join(', ');
      U.form((row ? 'Edit ' : 'Add ') + SECS.filter(function (s) { return s[0] === kind; })[0][1].toLowerCase().replace(/s$/, ''), f, v, function (x) {
        ['location_id', 'operatory_id', 'provider_id', 'weekday', 'sort', 'minutes', 'online_booking'].forEach(function (k) { if (x[k] !== undefined && x[k] !== '') x[k] = +x[k]; });
        ['fee', 'monthly', 'annual', 'discount_pct'].forEach(function (k) { if (x[k] !== undefined) x[k] = +x[k] || 0; });
        if (x.code) x.code = x.code.toUpperCase();
        if (kind === 'membership-plans') { x.included = {}; (x.included_txt || '').split(',').forEach(function (p) { var m = /(D\d{4})\s*x?\s*(\d+)/i.exec(p); if (m) x.included[m[1].toUpperCase()] = +m[2]; }); delete x.included_txt; }
        if (row) x.id = row.id;
        DA.post('/setup/' + kind, x).then(function () { U.toast('Saved.', 'ok'); load(); }, DA.fail);
      }, { wide: 1 });
    }
    load();
    U.on(app, '[data-q]', 'click', function (el) { sec = el.dataset.q; history.replaceState(null, '', '/v4/dental/setup?sec=' + sec); load(); });
    U.on(app, '[data-add]', 'click', function (b) { edit(b.dataset.add); });
    U.on(app, '[data-edit]', 'click', function (b) { edit(b.dataset.edit, (S[LIST[b.dataset.edit]] || []).filter(function (r) { return r.id === +b.dataset.id; })[0]); });
    U.on(app, '[data-delk]', 'click', function (b) { U.confirm('Delete', '<p class="cf">Delete this?</p>', 'Delete', function () { DA.del('/setup/' + b.dataset.delk + '/' + b.dataset.id).then(load, DA.fail); }, { danger: 1 }); });
    U.on(app, '#rm-sample', 'click', function () { U.confirm('Remove synthetic data', '<p class="cf">Delete every synthetic patient, appointment, claim and setting? Your own records stay.</p>', 'Remove', function () { DA.del('/sample').then(function (r) { U.toast('Removed ' + r.removed + ' synthetic records.', 'ok'); load(); }, DA.fail); }, { danger: 1 }); });
    U.on(app, '#od-connect', 'click', function () {
      U.form('Connect Open Dental', [{ k: 'customer_key', label: 'Customer Key', req: 1, span: 2, help: 'From Althais. Add it in Open Dental first (Setup > Advanced Setup > API > Add Key) and enable it.' },
        { k: 'clinic_num', label: 'Clinic number (optional)', span: 2, help: 'Only if your Open Dental database has several clinics.' }], {}, function (v) {
        var busy = U.toast('Checking the key with Open Dental…', 'busy');
        DA.post('/import/opendental/connect', v).then(function (r) { busy.remove(); OD = r; U.toast('Open Dental connected.', 'ok'); draw(); }, function (e) { busy.remove(); DA.fail(e); });
      }, { save: 'Connect', width: 520, intro: 'The key is stored encrypted and never shown again.' });
    });
    U.on(app, '[data-odpull]', 'click', function (b) {
      b.disabled = true; var busy = U.toast('Pulling from Open Dental…', 'busy');
      DA.post('/import/opendental/pull', { days: +b.dataset.odpull }).then(function (r) { busy.remove();
        U.toast('From Open Dental: ' + r.patients.added + ' new and ' + r.patients.updated + ' updated patients, ' + r.procedures.added + ' procedures' + (r.procedures.skipped ? ' (' + r.procedures.skipped + ' already here)' : '') + '.', 'ok');
        if (r.notes && r.notes.length) setTimeout(function () { U.toast(r.notes.join(' '), 'info'); }, 1200);
        load();
      }, function (e) { busy.remove(); b.disabled = false; DA.fail(e); });
    });
    U.on(app, '#od-off', 'click', function () { U.confirm('Disconnect Open Dental', '<p class="cf">Althais stops reading from Open Dental. Patients and history already brought in stay.</p>', 'Disconnect', function () { DA.post('/import/opendental/disconnect').then(function (r) { OD = r; draw(); }, DA.fail); }, { danger: 1 }); });
    U.on(app, '#isrc', 'change', function (s) { imp.source = s.value; imp.preview = null; });
    U.on(app, '#ikind', 'change', function (s) { imp.kind = s.value; imp.preview = null; });
    U.on(app, '#ifile', 'click', function () { U.pickFile('.csv,.txt,text/csv', false, function (f) { U.readFile(f[0], true).then(function (t) { imp.text = t; imp.preview = null; draw(); }); }); });
    var txt = function () { var t = document.getElementById('itext'); if (t) imp.text = t.value; return imp.text; };
    U.on(app, '#iprev', 'click', function () { DA.post('/import/preview', { source: imp.source, kind: imp.kind, text: txt() }).then(function (p) { imp.preview = p; draw(); }, DA.fail); });
    U.on(app, '#icommit', 'click', function () { DA.post('/import/commit', { source: imp.source, kind: imp.kind, text: txt() }).then(function (j) { U.toast('Imported: ' + j.summary.added + ' added, ' + j.summary.updated + ' updated, ' + j.summary.skipped + ' skipped.', 'ok'); imp.preview = null; imp.text = ''; load(); }, DA.fail); });
  };
})();

/* Every dental page draws its header and a loading line at once, then fills in when the server answers, so the screen
   is never blank while the data loads (over the hosted database that can take a few seconds). */
(function () {
  if (!window.V || !window.DA) return;
  var TITLES = { dtoday: 'Today', dschedule: 'Schedule', dpatients: 'Patients', dchart: 'Chart', dbenefits: 'Verify benefits', dclaims: 'Claims', dpreauths: 'Pre-authorizations',
    dera: 'ERA posting', ddenials: 'Denials & follow-up', dbilling: 'Patient billing', dmessages: 'Messages', drecall: 'Hygiene recall', dforms: 'Forms', dreviews: 'Reviews',
    dreports: 'Production & collections', dlabs: 'Lab cases', dsetup: 'Practice setup' };
  Object.keys(TITLES).forEach(function (k) {
    var page = V.pages[k]; if (!page) return;
    V.pages[k] = function () {
      V.app.innerHTML = DA.frame(k, TITLES[k]) + '<div class="emptyst"><span class="toast-spin" aria-hidden="true" style="display:inline-block;vertical-align:-2px;margin-right:8px"></span>Loading ' + U.esc(TITLES[k].toLowerCase()) + '…</div>';
      U.fit();
      return page.apply(this, arguments);
    };
  });
})();
