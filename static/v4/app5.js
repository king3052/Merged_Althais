/* v4 working preview, part 5: Revenue › Billing Activation. The same server workflow as before (billing_activation.py,
   /api/billing/*): the clinic's billing profile, providers and payers, authorization, clearinghouse, each payer's
   applications with evidence, billers' claim access, tasks and signatures, documents, held and sent claims, history.
   Owners and admins see everything; billers see their checklist and the clinic's payer setup; providers see their
   own enrollment. Nothing here sends a claim; Claims does, through the claim gate. */
(function () {
  var app = V.app, E = U.esc, q = V.q;
  var DENTAL = !!(window.D && window.D.DENTAL);   // the clinic's version (D below is this page's billing profile, not the clinic store)
  var D = null, SEC = (location.hash || '#checklist').slice(1), ERR = '';
  var SECTIONS = [['checklist', 'Setup Checklist'], ['routes', 'Payers & Providers'], ['items', 'Applications'], ['billers', 'Billers'],
    ['tasks', 'Tasks'], ['documents', 'Documents'], ['claims', 'Held & Sent Claims'], ['activity', 'Activity']];

  function api(method, url, body, form) {
    return fetch(url, { method: method, credentials: 'same-origin', headers: body && !form ? { 'Content-Type': 'application/json' } : {}, body: form || (body ? JSON.stringify(body) : undefined) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || j.detail || 'Something went wrong.'); return j; }); });
  }
  var fmt = function (d) { return d ? window.D.us(String(d).slice(0, 10)) : '-'; };
  var OK = ['APPROVED', 'VERIFIED', 'NOT_REQUIRED', 'ACTIVE'], WAIT = ['SUBMITTED', 'IN_REVIEW'], BAD = ['DENIED', 'EXPIRED', 'NEEDS_REVIEW', 'PAYER_REQUEST', 'REVOKED'], MUTE = ['NOT_CONNECTED', 'MANUAL', 'NOT_STARTED', 'NONE', 'NO_LOGIN'];
  function pill(s, label) {
    var cls = OK.indexOf(s) >= 0 ? 'grn' : WAIT.indexOf(s) >= 0 ? 'blue' : BAD.indexOf(s) >= 0 ? 'red' : MUTE.indexOf(s) >= 0 ? 'mut' : 'amb';
    return '<span class="tc s-' + (cls === 'red' ? 'red' : cls) + '">' + (cls === 'grn' ? U.I.check : cls === 'red' || cls === 'amb' ? U.warn : '') + E(label || s) + '</span>';
  }
  var none = function (t) { return '<div class="mut" style="padding:12px 10px">' + E(t) + '</div>'; };
  var assigneeLabel = function (a) { return a === 'owner' ? 'Owners and admins' : String(a).indexOf('person:') === 0 ? (((D.profile || { providers: [] }).providers.filter(function (p) { return p.personId === a.slice(7); })[0] || {}).name || 'Staff member') : 'Assigned'; };
  function step(n, ok, title, body) {
    return '<div class="ba-step"><span class="ba-dot' + (ok ? ' ok' : '') + '">' + (ok ? U.I.check : n) + '</span><div class="ba-sb"><div class="ba-st">' + E(title) + '</div>' + (body || '') + '</div></div>';
  }

  // ---------- sections (owner / admin) ----------
  function checklist() {
    var P = D.profile, it = function (r) { return D.items.filter(function (i) { return i.requirement === r; }); };
    var allOk = function (r) { return it(r).length && it(r).every(function (i) { return i.ready; }); };
    var confirmedAll = ['legal_name', 'npi', 'tin', 'address'].every(function (k) { return P.confirmed[k]; });
    var steps = [
      ['Confirm your clinic’s billing details', confirmedAll && allOk('entity_npi'), 'profile'],
      ['Add the providers who see patients', P.providers.length > 0 && P.providers.every(function (p) { return p.confirmed; }), 'providers'],
      ['List the payers you bill, and whether you already bill each one', P.payers.length > 0 && P.payers.every(function (p) { return p.alreadyBills !== null && p.alreadyBills !== undefined; }), 'payers'],
      ['Authorize Althais to prepare applications and send claims', allOk('clinic_authorization'), 'auth'],
      ['Althais submits your claims to payers', allOk('clearinghouse_account'), 'ch'],
      ['Finish each payer’s applications', D.items.filter(function (i) { return i.blocksClaims && i.payerKey; }).every(function (i) { return i.ready; }) && D.items.some(function (i) { return i.payerKey; }), 'apps'],
      ['Give your billers claim submission access', D.billers.length ? D.billers.every(function (b) { return b.access === 'ACTIVE'; }) : false, 'billers']];
    var done = steps.filter(function (s) { return s[1]; }).length;
    return U.sect('Setup Checklist', done + ' of ' + steps.length + ' done', '<p class="ba-lead">Your whole workspace is open' + (D.purchasingOwner ? ' to ' + E(D.purchasingOwner) + ' and your team' : '') + '. Only sending claims waits for these steps. Drafts are kept safely until you’re ready.</p>' +
      (DENTAL ? '<p class="ba-lead"><b>Dental claims:</b> Althais checks every dental claim and keeps it ready. Sending dental claims on the ADA form (837D) opens once Althais’s dental clearinghouse route is set up; until then, dental claims are held here and you send them from your practice software.</p>' : '') +
      '<div class="ba-steps">' + steps.map(function (s, i) { return step(i + 1, s[1], s[0], stepBody(s[2])); }).join('') + '</div>');
  }
  function src(k, ck) {
    var s = D.profile.sources[k], c = D.profile.confirmed[ck || k], from = s && s !== 'Entered in Billing Activation' ? 'from ' + s : '';
    var bits = [from, c ? 'confirmed' : ''].filter(Boolean).join(' · ');
    return bits ? ' <span class="mut" style="font-weight:400">' + E(bits) + '</span>' : '';
  }
  function fld(id, label, val, o) {
    o = o || {};
    return '<div class="' + (o.span ? 's' + o.span : '') + '"><label for="' + id + '">' + label + '</label><input id="' + id + '" value="' + E(val || '') + '"' + (o.ph ? ' placeholder="' + E(o.ph) + '"' : '') + (o.mono ? ' class="mono"' : '') + (o.max ? ' maxlength="' + o.max + '"' : '') + (o.type ? ' type="' + o.type + '"' : '') + ' autocomplete="off"></div>';
  }
  function stepBody(k) {
    var P = D.profile;
    if (k === 'profile') return '<form id="f-profile" class="form ba-form">' +
      fld('p-name', 'Legal business name' + src('legalName', 'legal_name'), P.legalName, { span: 2 }) + fld('p-npi', 'Organization NPI (Type 2)' + src('npi'), P.npi, { mono: 1 }) +
      fld('p-tin', 'Tax ID (EIN)' + src('tin'), '', { mono: 1, ph: P.hasTin ? P.tinMasked + ' (leave blank to keep)' : '9 digits' }) + fld('p-tax', 'Taxonomy code', P.taxonomy, { mono: 1, ph: DENTAL ? 'e.g. 1223G0001X' : 'e.g. 207Q00000X' }) +
      fld('p-a1', 'Billing address' + src('address'), P.address.line1, { span: 2 }) + fld('p-city', 'City', P.address.city) + fld('p-state', 'State', P.address.state, { max: 2 }) + fld('p-zip', 'ZIP', P.address.zip) +
      fld('p-cname', 'Billing contact', P.contact.name) + fld('p-cemail', 'Contact email', P.contact.email) +
      '<div class="s4 ba-acts"><button class="btn pri" type="submit">Save Details</button><button class="btn" type="button" data-confirm-profile>These Are Correct</button><span class="mut">Prefilled details count only after you confirm them. Althais checks the NPI with the NPI Registry.</span></div></form>';
    if (k === 'providers') return '<div class="ba-rows">' + (P.providers.map(function (p, i) {
        return '<div class="form ba-prov" data-prov="' + E(p.id) + '">' +
          '<div class="s2"><label>Name</label><input data-k="name" value="' + E(p.name) + '"></div><div><label>NPI (Type 1)</label><input class="mono" data-k="npi" value="' + E(p.npi || '') + '"></div><div><label>Taxonomy</label><input class="mono" data-k="taxonomy" value="' + E(p.taxonomy || '') + '"></div>' +
          '<div><label>License #</label><input class="mono" data-k="licenseNumber" value="' + E(p.licenseNumber || '') + '"></div><div><label>State</label><input data-k="licenseState" maxlength="2" value="' + E(p.licenseState || '') + '"></div><div class="s2"><label>License expires</label><input type="date" data-k="licenseExpires" value="' + E(p.licenseExpires || '') + '"></div>' +
          '<div class="s4 mut" style="font-size:12px">' + E(p.source || 'Entered here') + (p.confirmed ? ' · confirmed by ' + E(p.confirmed.by) : ' · not confirmed yet') + ' · <a href="#" class="s-red" data-prov-del="' + i + '">Remove</a></div></div>';
      }).join('') || none('No providers yet.')) +
      '<div class="ba-acts"><select id="prov-add"><option value="">Add a provider from your staff…</option>' + D.staffPhysicians.filter(function (s) { return !P.providers.some(function (p) { return p.personId === s.personId; }); }).map(function (s) { return '<option value="' + E(s.personId) + '">' + E(s.name) + '</option>'; }).join('') + '<option value="__new">Someone not in Staff</option></select>' +
      '<button class="btn pri" data-save-providers>Save Providers</button><button class="btn" data-confirm-providers>These Are Correct</button></div></div>';
    if (k === 'payers') return '<div class="ba-rows">' + U.grid(['Payer', 'Type', 'Already billing them?', 'Network', 'Claim.MD payer ID', ''], P.payers.map(function (p, i) {
        return { attrs: ' data-payer="' + i + '"', cells: ['<input class="inl" data-k="name" value="' + E(p.name) + '">' + (p.reported && (p.reported.status || p.reported.providerId) ? '<div class="mut" style="font-size:11.5px;margin-top:3px;white-space:normal">Clinic Onboarding says: ' + E(p.reported.status || '') + (p.reported.providerId ? ', ID on file' : '') + '. Althais needs proof before it counts.</div>' : ''),
          '<select class="inl" data-k="type"><option value=""' + (p.type ? '' : ' selected') + '>Choose…</option>' + Object.keys(D.payerTypes).map(function (k) { return '<option value="' + k + '"' + (p.type === k ? ' selected' : '') + '>' + E(D.payerTypes[k]) + '</option>'; }).join('') + '</select>',
          '<select class="inl" data-k="alreadyBills"><option value=""' + (p.alreadyBills == null ? ' selected' : '') + '>Choose…</option><option value="yes"' + (p.alreadyBills === true ? ' selected' : '') + '>Yes, we already bill them</option><option value="no"' + (p.alreadyBills === false ? ' selected' : '') + '>No, we need to enroll</option></select>',
          '<select class="inl" data-k="network"><option value="">Not decided</option><option value="in"' + (p.network === 'in' ? ' selected' : '') + '>In-network</option><option value="out"' + (p.network === 'out' ? ' selected' : '') + '>Out-of-network</option></select>',
          '<input class="inl mono" data-k="payerId" value="' + E(p.payerId || '') + '" placeholder="e.g. 60054" style="width:100px" title="Claim.MD payer ID; leave blank to match by name">',
          '<a href="#" class="s-red" data-payer-del="' + i + '">Remove</a>'] };
      })).replace('<tbody></tbody>', '<tbody>' + U.empty(6, 'No payers yet.') + '</tbody>') +
      '<div class="ba-acts"><input id="payer-new" placeholder="Add a payer, e.g. ' + (DENTAL ? 'Delta Dental' : 'Aetna') + '" style="width:240px"><button class="btn" data-add-payer>Add</button><button class="btn pri" data-save-payers>Save Payers</button></div></div>';
    if (k === 'auth') { var a = D.items.filter(function (x) { return x.requirement === 'clinic_authorization'; })[0];
      return a ? '<div class="ba-note">' + pill(a.status, a.statusLabel) + ' ' + (a.ready ? 'Signed by ' + E(a.evidence.verifiedBy) : 'A clinic owner signs this in Althais. See Waiting On You.') + '</div>' : ''; }
    if (k === 'ch') return chBody();
    if (k === 'apps') { var n = D.items.filter(function (i) { return i.payerKey; }), ok = n.filter(function (i) { return i.ready; }).length;
      return '<div class="ba-note">' + ok + ' of ' + n.length + ' payer items done. <a href="#items" data-go="items">See Applications</a></div>'; }
    if (k === 'billers') return '<div class="ba-note">' + (D.billers.length ? D.billers.map(function (b) { return E(b.name) + ': ' + (b.access === 'ACTIVE' ? 'can send claims' : 'not yet'); }).join(' · ') : 'No billers yet. Add someone with the Biller role in Staff Onboarding.') + ' <a href="#billers" data-go="billers">Manage Billers</a></div>';
    return '';
  }
  function routes() {
    return U.sect('Payers & Providers', D.routes.filter(function (r) { return r.ready; }).length + ' of ' + D.routes.length + ' ready to send claims', D.routes.length ? U.grid(['Payer', 'Provider', 'Claims (837P)', ["What's left", 'wrap']], D.routes.map(function (r) {
      return { cells: ['<b>' + E(r.payer) + '</b>', E(r.provider || '-'), r.ready ? pill('APPROVED', 'Ready') : pill('NOT_STARTED', 'Not ready'),
        r.ready ? '<span class="s-grn">Claims can be sent.</span>' : r.blockers.map(function (b) { return '<div><b>' + E(b.title) + '</b>: ' + E(b.reason) + (b.next ? ' <span class="s-blue">· ' + E(b.next) + '</span>' : '') + '</div>'; }).join('')] };
    })) : none('Add payers and providers in the Setup Checklist first.'));
  }
  function items() {
    return U.sect('Applications', 'enrollment, credentialing, contracting, electronic claims and connection, each on its own timeline', '<p class="ba-lead">Nothing counts as ready without evidence and an effective date that has arrived.</p>' +
      '<div class="ba-scroll">' + U.grid([['Requirement', 'wrap'], 'Status', ['Next action', 'wrap'], 'Responsible', 'Submitted', 'Effective', 'Evidence', 'Last update', ''], D.items.map(function (i) {
        var acts = (['READY_TO_SUBMIT', 'MANUAL', 'SIGNATURE_NEEDED', 'NEEDS_REVIEW', 'INFO_NEEDED'].indexOf(i.status) >= 0 && ['identity', 'authorization', 'connectivity'].indexOf(i.track) < 0 ? '<a class="mini" href="/api/billing/items/' + i.id + '/packet" target="_blank" rel="noopener">Packet</a>' : '') +
          (['READY_TO_SUBMIT', 'MANUAL'].indexOf(i.status) >= 0 ? '<button class="mini" data-submitted="' + i.id + '">Record Submission</button>' : '') +
          (!i.ready && i.track !== 'authorization' ? '<button class="mini" data-confirm-item="' + i.id + '">Confirm With Evidence</button>' : '');
        return { attrs: ' id="item-' + i.id + '"', cells: [
          '<b>' + E(i.title) + '</b><div class="mut" style="font-size:11.5px">' + ([i.payer, i.provider, i.transaction].filter(Boolean).map(E).join(' · ') || 'Clinic') + (i.supported ? '' : ' · you submit this one') + '</div>' + (i.source && i.source.title ? '<div class="mut" style="font-size:11px">Source: ' + (i.source.url ? '<a href="' + E(i.source.url) + '" target="_blank" rel="noopener">' + E(i.source.title) + '</a>' : E(i.source.title)) + '</div>' : ''),
          pill(i.status, i.statusLabel) + (!i.ready && i.why && i.why !== i.statusLabel ? '<div class="mut" style="font-size:11px">' + E(i.why) + '</div>' : '') + (i.environment === 'sandbox' ? '<div class="mut" style="font-size:11px">Test mode</div>' : ''),
          E(i.next || '-'), E(i.responsible || '-'),
          (i.submittedAt ? fmt(i.submittedAt) + (i.daysPending != null ? '<div class="mut" style="font-size:11px">' + i.daysPending + ' days pending</div>' : '') : '-') + (i.reference ? '<div class="mono mut" style="font-size:11px">' + E(i.reference) + '</div>' : ''),
          (i.effectiveDate ? fmt(i.effectiveDate) : '-') + (i.expiresAt ? '<div class="mut" style="font-size:11px">until ' + fmt(i.expiresAt) + '</div>' : ''),
          (i.evidence.documentId ? '<a href="/api/billing/documents/' + i.evidence.documentId + '/file" target="_blank" rel="noopener">Document</a>' : i.evidence.source ? E(i.evidence.source === 'nppes' ? 'NPI Registry' : i.evidence.source) : '-') + (i.evidence.verifiedBy ? '<div class="mut" style="font-size:11px">' + E(i.evidence.verifiedBy) + (i.evidence.verifiedAt ? ', ' + fmt(i.evidence.verifiedAt) : '') + '</div>' : ''),
          i.lastUpdate ? fmt(i.lastUpdate) + '<div class="mut" style="font-size:11px">' + E(i.lastUpdateSource || '') + '</div>' : '-',
          '<div class="ba-btns">' + acts + '</div>'] };
      })).replace('<tbody></tbody>', '<tbody>' + U.empty(9, 'No applications yet. Add payers and providers in the Setup Checklist.') + '</tbody>') + '</div>');
  }
  function billers() {
    var pol = D.profile.policy || {}, auto = pol.autoActivateBillers || {}, rel = pol.autoReleaseHeld || {};
    var opt = function (k, on, t, s) { return '<label class="ba-opt"><input type="checkbox" data-policy="' + k + '"' + (on ? ' checked' : '') + '><span><b>' + t + '</b><small>' + s + '</small></span></label>'; };
    var LBL = { ACTIVE: 'Can send claims', PENDING: 'Not yet', REVOKED: 'Turned off', NO_LOGIN: 'Hasn’t signed in' };
    return U.sect('Billers', D.billers.length + ' with the Biller role', '<div class="ba-opts">' +
        opt('autoActivateBillers', auto.enabled, 'Turn on access automatically when a biller finishes their checklist', 'Only for active staff with the Biller role who finished training, the practice claim and the acknowledgment. Off unless you turn it on.' + (auto.enabled ? ' Turned on by ' + E(auto.approvedBy) + ' on ' + fmt(auto.approvedAt) + '.' : '')) +
        opt('autoReleaseHeld', rel.enabled, 'Send held claims automatically once everything is ready', 'Each claim is checked again right before it goes. Off means the biller gets a task to send it.' + (rel.enabled ? ' Turned on by ' + E(rel.approvedBy) + '.' : '')) + '</div>' +
      (D.billers.length ? U.grid(['Biller', ['Checklist', 'wrap'], 'Claim submission', ''], D.billers.map(function (b) {
        return { cells: ['<b>' + E(b.name) + '</b><div class="mut" style="font-size:11.5px">' + E(b.lifecycle || '') + '</div>',
          b.checklist.steps.map(function (s) { return '<div style="font-size:12px">' + (s.done ? '<span class="s-grn">' + U.I.check + '</span>' : '<span class="mut">○</span>') + ' ' + E(s.title) + '</div>'; }).join(''),
          pill(b.access, LBL[b.access] || b.access) + (b.lock ? '<div style="margin-top:3px">' + pill('PENDING', 'In training') + '<div class="mut" style="font-size:11px">Staff Portal only until they ' + E(b.lock.reason) + '</div></div>' : '') + (b.grantedBy ? '<div class="mut" style="font-size:11px">' + E(b.grantedVia === 'rule' ? 'By rule' : 'By ' + b.grantedBy) + ', ' + fmt(b.grantedAt) + '</div>' : ''),
          '<div class="ba-btns">' + (b.lock ? '<button class="mini" data-unlock="' + E(b.personId) + '">Unlock</button>' : '<button class="mini" data-lock="' + E(b.personId) + '" title="Only their Staff Portal until they finish their billing training">Send To Training</button>') +
            (b.access === 'ACTIVE' ? '<button class="mini" data-biller="' + E(b.personId) + '" data-act="revoke">Turn Off</button>' : b.access !== 'NO_LOGIN' ? '<button class="mini pri" data-biller="' + E(b.personId) + '" data-act="approve"' + (b.checklist.readyForAccess ? '' : ' disabled title="Their checklist isn’t finished"') + '>Approve Access</button>' : '') + '</div>'] };
      })) : none('No one has the Biller role yet. Billers get their own checklist automatically when you add them in Staff Onboarding.')) +
      '<div class="ba-note" style="margin:10px">Do you send claims yourself? ' + (D.me && D.me.access === 'ACTIVE' ? '<b class="s-grn">Your login can send claims.</b>' : '<button class="btn" data-self-access>Allow My Login To Send Claims</button>') + '</div>');
  }
  function taskRow(x) {
    return '<div class="ba-task"><span class="ba-dot' + (x.blocking ? ' warn' : '') + '">' + (x.blocking ? '!' : '•') + '</span><div class="ba-sb"><div class="ba-st">' + E(x.title) + '</div>' + (x.detail ? '<div class="t2">' + E(x.detail) + '</div>' : '') +
      '<div class="' + (x.overdue ? 's-red' : 'mut') + '" style="font-size:11.5px;margin-top:2px">Due ' + fmt(x.due) + (x.overdue ? ' · overdue' : '') + (D.view === 'owner' ? ' · ' + E(assigneeLabel(x.assignee)) : '') + '</div></div>' +
      '<div class="ba-btns">' + (x.itemId && D.view === 'owner' ? '<button class="mini" data-open-item="' + x.itemId + '">Open</button>' : '') + (x.kind !== 'signature' ? '<button class="mini" data-task-done="' + x.id + '">Mark Done</button>' : '') + '</div></div>';
  }
  function tasks() {
    return (D.signatures.length ? U.sect('Signatures Requested', D.signatures.length, D.signatures.map(function (x) { return '<div class="ba-task"><span class="ba-dot">✎</span><div class="ba-sb"><div class="t2">' + E(x.statement.slice(0, 180)) + '…</div><div class="mut" style="font-size:11.5px">For: ' + E(assigneeLabel(x.signer)) + ' · requested ' + fmt(x.requested) + '</div></div></div>'; }).join('')) : '') +
      U.sect('Open Tasks', D.tasks.length, D.tasks.length ? D.tasks.map(taskRow).join('') : none('No open tasks.'));
  }
  function documents() {
    return U.sect('Documents', D.documents.length + ' on file', '<form id="f-doc" class="form ba-form" style="grid-template-columns:repeat(4,minmax(0,1fr))">' +
        '<div><label for="doc-type">Document</label><select id="doc-type">' + Object.keys(D.docTypes).map(function (k) { return '<option value="' + k + '">' + E(D.docTypes[k]) + '</option>'; }).join('') + '</select></div>' +
        '<div><label for="doc-item">For</label><select id="doc-item"><option value="">General</option>' + D.items.filter(function (i) { return !i.ready; }).map(function (i) { return '<option value="' + i.id + '">' + E(i.title) + (i.payer ? ' · ' + E(i.payer) : '') + (i.provider ? ' · ' + E(i.provider) : '') + '</option>'; }).join('') + '</select></div>' +
        '<div><label for="doc-file">File (PDF or photo)</label><input id="doc-file" type="file" accept="application/pdf,image/*"></div><div class="ba-acts" style="align-self:end"><button class="btn pri" type="submit">Upload And Check</button></div>' +
        '<div class="s4 mut" style="font-size:12px">Althais reads the document, checks dates and whether it matches your billing profile, and sends anything uncertain to you. Reading a document never approves anything by itself.</div></form>' +
      (D.documents.length ? U.grid(['Document', ['What Althais found', 'wrap'], 'Status', ''], D.documents.map(function (d) {
        return { cells: ['<b>' + E(d.typeLabel) + '</b><div class="mut" style="font-size:11.5px">' + E(d.filename) + ' · ' + fmt(d.uploaded) + '</div>',
          (Object.keys(d.fields).map(function (k) { return '<div style="font-size:12px"><span class="mut">' + E(k.replace(/_/g, ' ')) + ':</span> ' + E(d.fields[k]) + '</div>'; }).join('') || '<span class="mut">-</span>') + d.issues.map(function (x) { return '<div class="s-red" style="font-size:12px">' + E(x) + '</div>'; }).join(''),
          pill(d.status === 'checked' || d.status === 'reviewed' ? 'VERIFIED' : 'NEEDS_REVIEW', { checked: 'Checked', reviewed: 'Reviewed', needs_review: 'Needs review', checking: 'Checking' }[d.status]),
          '<div class="ba-btns"><a class="mini" href="/api/billing/documents/' + d.id + '/file" target="_blank" rel="noopener">View</a>' + (d.status === 'needs_review' ? '<button class="mini" data-doc-reviewed="' + d.id + '">I Checked It</button>' : '') + '</div>'] };
      })) : none('No documents yet.')));
  }
  function claims() {
    return U.sect('Held Claims', D.held.length + ' waiting', D.held.length ? D.held.map(function (h) {
        return '<div class="ba-task"><span class="ba-dot warn">⏸</span><div class="ba-sb"><div class="ba-st"><a href="' + V.claimHref(h.claimId) + '">' + E(h.claimId) + '</a> · ' + E(h.patient) + ' · ' + E(h.payer) + '</div><div class="mut" style="font-size:11.5px">Held since ' + fmt(h.since) + '. Saved as-is; nothing was sent.</div>' +
          h.blockers.map(function (b) { return '<div style="font-size:12.5px"><b>' + E(b.title) + '</b>: ' + E(b.reason) + '</div>'; }).join('') + '</div></div>'; }).join('') : none('No held claims.')) +
      U.sect('Recently Sent', (D.sent || []).length + ', submitted by Althais', (D.sent || []).length ? U.grid(['Claim', 'Delivered', 'Clearinghouse', 'Payer', 'Payment', ['Details', 'wrap']], D.sent.map(function (h) {
        var tone = function (v) { return v === 'ACCEPTED' || v === 'PAID' ? 'APPROVED' : v === 'REJECTED' || v === 'DENIED' ? 'DENIED' : 'SUBMITTED'; };
        return { cells: ['<a href="' + V.claimHref(h.claimId) + '"><b>' + E(h.claimId) + '</b></a>' + (h.environment === 'sandbox' ? ' ' + pill('NONE', 'Test') : '') + '<div class="mono mut" style="font-size:11px">' + E(h.reference || '') + '</div>', fmt(h.sentAt), pill(tone(h.ack), h.ack || 'Pending'), pill(tone(h.payerStatus), h.payerStatus || 'Pending'), h.payment ? pill(tone(h.payment), h.payment) : '-',
          (h.blockers || []).map(function (b) { return '<div class="s-red" style="font-size:12px"><b>' + E(b.title) + '</b>: ' + E(b.reason) + '</div>'; }).join('') || '<span class="mut">-</span>'] };
      })) : none('Nothing sent yet.'));
  }
  function activity() {
    var m = D.metrics, v = function (x, u) { return x == null ? '-' : x + ' ' + u; };
    return U.sect('Activity', D.activity.length + ' events', U.strip([['Staff ready', v(m.staffReadinessDays, 'days'), 'biller added to access'], ['Applications prepared', v(m.applicationPrepDays, 'days'), 'started to submitted'], ['Payer approval', v(m.externalApprovalDays, 'days'), 'submitted to approved'], ['First accepted claim', v(m.daysToFirstAcceptedClaim, 'days'), 'from the start of setup']]) +
      '<p class="ba-lead mut" style="font-size:12px">' + E(m.note || '') + '</p>' +
      (D.activity.length ? U.grid(['When', ['What', 'wrap'], 'Source', ['Note', 'wrap']], D.activity.map(function (e) {
        return { cells: [fmt(e.at), E(e.kind.replace(/_/g, ' ')) + (e.to ? ': ' + E(e.from || '-') + ' → ' + E(e.to) : '') + (e.environment === 'sandbox' ? ' ' + pill('NONE', 'Test') : ''), '<span class="mut">' + E(e.source || '') + '</span>', E(e.note || '')] };
      })) : none('Nothing yet.')));
  }

  function chBody() {
    var ok = D.clearinghouse.capabilities.claim_submit;
    return '<div class="ba-note">' + (ok ? pill('APPROVED', 'Ready') + ' <span>You send claims from <a href="/v4/revenue/claims?q=ready">Claims</a>; Althais submits them to each payer and tracks every response here and on the claim.</span>'
      : pill('SUBMITTED', 'Althais is setting this up') + ' <span class="mut">Nothing for you to do. Claims you send are held safely and go out as soon as it’s ready.</span>') + '</div>';
  }

  // ---------- right side: what needs this person ----------
  function waiting() {
    var t = D.myTasks || [], s = D.mySignatures || [];
    return U.pane('Waiting On You', (t.length + s.length) + ' open', '', (s.length || t.length) ? s.map(function (x) {
        return '<div class="ba-task"><span class="ba-dot">✎</span><div class="ba-sb"><div class="ba-st">Signature requested</div><div class="t2">' + E(x.statement.slice(0, 140)) + '…</div><button class="btn pri" style="margin-top:6px" data-sign="' + x.id + '">Review And Sign</button></div></div>';
      }).join('') + t.filter(function (x) { return x.kind !== 'signature'; }).map(taskRow).join('') : none('Nothing needs you right now.'), { id: 'ba-side' });
  }

  function main() {
    if (D.view === 'owner') return ({ checklist: checklist, routes: routes, items: items, billers: billers, tasks: tasks, documents: documents, claims: claims, activity: activity }[SEC] || checklist)();
    var me = D.me || { checklist: { steps: [] }, access: 'PENDING' };
    if (D.view === 'biller') return U.sect('Your Billing Checklist', '', '<div class="ba-steps">' + me.checklist.steps.map(function (s, i) { return step(i + 1, s.done, s.title, s.done ? '' : '<a class="btn pri" style="margin-top:6px;display:inline-flex" href="' + E(s.link) + '">Start</a>'); }).join('') +
        step(me.checklist.steps.length + 1, me.access === 'ACTIVE', 'Claim submission access', '<div class="t2">' + (me.access === 'ACTIVE' ? 'You can send claims once the clinic’s payer setup is ready.' : me.checklist.readyForAccess ? 'Your checklist is done. A clinic owner approves your access.' : 'A clinic owner approves this after your checklist is done.') + '</div>') + '</div>') +
      U.sect('Clinic Payer Setup', '', D.routes.length ? U.grid(['Payer', 'Provider', 'Status', ['Next', 'wrap']], D.routes.map(function (r) { return { cells: ['<b>' + E(r.payer) + '</b>', E(r.provider || ''), r.ready ? pill('APPROVED', 'Ready') : pill('NOT_STARTED', 'Not ready yet'), r.ready ? '' : E((r.blockers[0] || {}).title || '')] }; })) : none('Your clinic hasn’t added payers yet.')) +
      U.sect('Your Held Claims', '', (D.held || []).length ? D.held.map(function (h) { return '<div class="ba-task"><span class="ba-dot warn">⏸</span><div class="ba-sb"><div class="ba-st">' + E(h.claimId) + ' · ' + E(h.patient) + '</div>' + h.blockers.map(function (b) { return '<div style="font-size:12.5px"><b>' + E(b.title) + '</b>: ' + E(b.reason) + '</div>'; }).join('') + '</div></div>'; }).join('') : none('None. Drafts you create are kept until they can be sent.'));
    if (D.view === 'provider') return U.sect('Your Enrollment Status', '', (D.items || []).length ? U.grid(['Requirement', 'Status', ['Next', 'wrap']], D.items.map(function (i) { return { cells: ['<b>' + E(i.title) + '</b><div class="mut" style="font-size:11.5px">' + E(i.payer || '') + '</div>', pill(i.status, i.statusLabel), E(i.next || '')] }; })) : none('Nothing yet.'));
    return U.sect('Billing Activation', '', none('Your clinic’s owners set up billing here. Nothing here needs you.'));
  }

  function draw() {
    if (!D) { app.innerHTML = U.header('billing') + U.toolbar('Billing Activation', []) + '<div class="main" style="grid-template-columns:1fr"><section class="pane"><div class="pbody">' + (ERR ? '<div class="s-red" style="padding:14px">' + E(ERR) + '</div>' : none('Loading…')) + '</div></section></div>'; U.fit(); return; }
    var ch = D.clearinghouse, R = D.routes || [], ready = R.filter(function (r) { return r.ready; }).length, nT = (D.tasks || []).length + (D.signatures || []).length;
    var strip = U.strip([['Ready To Send Claims', R.length ? ready + ' of ' + R.length : '-', 'payer and provider pairs', ready ? 'grn' : 'amb'], ['Claim Submission', ch.capabilities.claim_submit ? 'By Althais' : 'Setting up', ch.capabilities.claim_submit ? 'ready to submit' : 'by Althais', ch.capabilities.claim_submit ? 'grn' : 'blue'],
      ['Your Open Tasks', ((D.myTasks || []).length + (D.mySignatures || []).length) || '0', 'only you can do these'], ['Held Claims', (D.held || []).length, (D.sent || []).length + ' sent'], ['Requirements Registry', 'v' + E(D.registry.version), D.registry.review.reviewedBy ? 'reviewed by ' + E(D.registry.review.reviewedBy) : 'confirm details with each payer']]);
    var owner = D.view === 'owner', side = ((D.myTasks || []).length + (D.mySignatures || []).length) > 0 || !owner;   // Waiting On You only takes room when something waits
    var nav = owner ? U.pane('Sections', '', '', U.queue([{ h: 'Setup', items: SECTIONS.slice(0, 4).map(function (s) { return { key: s[0], label: s[1], n: s[0] === 'items' ? D.items.filter(function (i) { return !i.ready; }).length || '' : s[0] === 'billers' ? D.billers.length : s[0] === 'routes' ? ready + '/' + R.length : '', color: null }; }) },
      { h: 'Work', items: SECTIONS.slice(4).map(function (s) { return { key: s[0], label: s[1], n: s[0] === 'tasks' ? nT : s[0] === 'claims' ? (D.held || []).length : s[0] === 'documents' ? D.documents.length : '', color: null }; }) }], SEC)) : '';
    app.innerHTML = U.header('billing') +
      U.toolbar('Billing Activation', owner ? [['Check Again', 'refresh', { id: 'barefresh' }], '|', ['Claims', 'send', { href: '/v4/revenue/claims?q=ready' }]] : [], D.environment === 'sandbox' ? '<span class="tc s-mut" title="Test mode: nothing reaches a payer">Test Mode</span>' : '') + strip +
      '<div class="main" style="grid-template-columns:' + (owner ? '200px minmax(0,1fr)' : 'minmax(0,1fr)') + (side ? ' 320px' : '') + '">' + nav + '<section class="pane"><div class="pbody">' + main() + '</div></section>' + (side ? waiting() : '') + '</div>';
    U.fit(); U.search();
  }
  function load() { return api('GET', '/api/billing/overview').then(function (j) { D = j; ERR = ''; draw(); }, function (e) { ERR = e.message; D = null; draw(); }); }
  function act(fn, msg) { return fn().then(function () { if (msg) U.toast(msg); return load(); }, function (e) { U.toast(e.message, 'warn'); }); }
  function collectProviders() { return [].slice.call(app.querySelectorAll('[data-prov]')).map(function (row, i) { var o = { id: row.dataset.prov, personId: (D.profile.providers[i] || {}).personId || '' }; row.querySelectorAll('[data-k]').forEach(function (x) { o[x.dataset.k] = x.value; }); return o; }); }
  function collectPayers() { return [].slice.call(app.querySelectorAll('[data-payer]')).map(function (row) { var o = {}; row.querySelectorAll('[data-k]').forEach(function (x) { o[x.dataset.k] = x.value; }); o.alreadyBills = o.alreadyBills === 'yes' ? true : o.alreadyBills === 'no' ? false : null; return o; }); }
  var val = function (id) { var el = document.getElementById(id); return el ? el.value : ''; };

  V.pages.billing = function () {
    V.redraw = draw; draw(); load();
    var go = function (k) { SEC = k; history.replaceState(null, '', '#' + k); draw(); };
    U.on(app, '[data-q]', 'click', function (el) { go(el.dataset.q); });
    U.on(app, '[data-go]', 'click', function (el, e) { e.preventDefault(); go(el.dataset.go); });
    U.on(app, '#barefresh', 'click', function () { act(function () { return api('POST', '/api/billing/sync'); }, 'Checked again.'); });
    U.on(app, '[data-confirm-profile]', 'click', function () { act(function () { return api('POST', '/api/billing/profile/confirm', { fields: ['legal_name', 'npi', 'tin', 'address', 'taxonomy'] }); }, 'Billing details confirmed.'); });
    U.on(app, '[data-confirm-providers]', 'click', function () { act(function () { return api('POST', '/api/billing/profile/confirm', { providers: D.profile.providers.map(function (p) { return p.id; }) }); }, 'Providers confirmed.'); });
    U.on(app, '[data-save-providers]', 'click', function () { act(function () { return api('PUT', '/api/billing/providers', { providers: collectProviders() }); }, 'Providers saved.'); });
    U.on(app, '[data-prov-del]', 'click', function (b, e) { e.preventDefault(); D.profile.providers = collectProviders(); D.profile.providers.splice(+b.dataset.provDel, 1); draw(); });
    U.on(app, '[data-save-payers]', 'click', function () { act(function () { return api('PUT', '/api/billing/payers', { payers: collectPayers() }); }, 'Payers saved.'); });
    U.on(app, '[data-add-payer]', 'click', function () { var n = val('payer-new').trim(); if (!n) return; D.profile.payers = collectPayers(); D.profile.payers.push({ name: n, type: '', alreadyBills: null }); draw(); });
    U.on(app, '[data-payer-del]', 'click', function (b, e) { e.preventDefault(); D.profile.payers = collectPayers(); D.profile.payers.splice(+b.dataset.payerDel, 1); draw(); });
    U.on(app, '[data-task-done]', 'click', function (b) { act(function () { return api('POST', '/api/billing/tasks/' + b.dataset.taskDone + '/done', {}); }, 'Done.'); });
    U.on(app, '[data-doc-reviewed]', 'click', function (b) { act(function () { return api('POST', '/api/billing/documents/' + b.dataset.docReviewed + '/reviewed'); }, 'Marked as checked.'); });
    U.on(app, '[data-biller]', 'click', function (b) { var a = b.dataset.act; act(function () { return api('POST', '/api/billing/billers/' + encodeURIComponent(b.dataset.biller) + '/access', { action: a }); }, a === 'approve' ? 'Access approved.' : 'Access turned off.'); });
    U.on(app, '[data-lock]', 'click', function (b) { U.confirm('Send To Training', '<p class="cf">Keep them in their Staff Portal until they finish their billing training? The EMR is blocked until then, and it opens again automatically.</p>', 'Send To Training', function () {
      act(function () { return api('POST', '/api/staff/onboarding/people/' + encodeURIComponent(b.dataset.lock) + '/portal-lock', { action: 'lock', mode: 'billing' }); }, 'Sent to training.'); }); });
    U.on(app, '[data-unlock]', 'click', function (b) { act(function () { return api('POST', '/api/staff/onboarding/people/' + encodeURIComponent(b.dataset.unlock) + '/portal-lock', { action: 'unlock' }); }, 'Access restored.'); });
    U.on(app, '[data-self-access]', 'click', function () { act(function () { return api('POST', '/api/billing/me/access'); }, 'Your login can send claims.'); });
    U.on(app, '[data-open-item]', 'click', function (b) { go('items'); var r = document.getElementById('item-' + b.dataset.openItem); if (r) { r.scrollIntoView({ block: 'center' }); r.classList.add('sel'); } });
    U.on(app, '[data-sign]', 'click', function (b) {
      var s = (D.mySignatures || []).filter(function (x) { return String(x.id) === b.dataset.sign; })[0]; if (!s) return;
      U.modal('Review And Sign', '<div class="so"><div class="so-sec" style="padding:10px 12px;line-height:1.55">' + E(s.statement) + '</div>' +
        '<label class="chk" style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" id="sg-agree"> I have read this and agree. I’m signing for myself.</label>' +
        '<div><label class="ask-l" for="sg-name">Type your full name, exactly as on your account' + (ME_NAME() ? ': <b>' + E(ME_NAME()) + '</b>' : '') + '</label><input id="sg-name" autocomplete="name" style="width:100%;height:30px;border:1px solid var(--b);padding:0 8px"></div><div class="s-red" id="sg-err" style="font-size:12px"></div></div>',
        [['Cancel', '', function () {}], ['Sign', 'pri', function (close, w) {
          var agree = w.querySelector('#sg-agree').checked, nm = w.querySelector('#sg-name').value.trim();
          if (!agree || !nm) { w.querySelector('#sg-err').textContent = 'Check the box and type your full name.'; return false; }
          act(function () { return api('POST', '/api/billing/signatures/' + s.id + '/sign', { agree: agree, typedName: nm }); }, 'Signed.');
        }]], { width: 520 });
    });
    U.on(app, '[data-submitted]', 'click', function (b) {
      U.modal('Record The Submission', '<p class="cf" style="margin:0 0 10px">After you submit it to the payer, record what they gave you. Althais tracks it from here and reminds you if it takes long.</p>' +
        U.fields([{ k: 'ref', label: 'Reference or confirmation number', span: 2 }, { k: 'on', label: 'Submitted on', type: 'date', span: 2 }], { on: D_today() }),
        [['Cancel', '', function () {}], ['Save', 'pri', function (close, w) { var v = U.values(w); act(function () { return api('POST', '/api/billing/items/' + b.dataset.submitted + '/submitted', { reference: v.ref, submittedOn: v.on }); }, 'Recorded.'); }]], { width: 460 });
    });
    U.on(app, '[data-confirm-item]', 'click', function (b) {
      var i = D.items.filter(function (x) { return String(x.id) === b.dataset.confirmItem; })[0]; if (!i) return;
      U.modal('Confirm: ' + i.title, '<p class="cf" style="margin:0 0 10px">Only confirm what you have proof of. It counts only once its effective date arrives.</p>' + U.fields([
          { k: 'status', label: 'Outcome', type: 'select', span: 2, options: [['APPROVED', 'Approved']].concat(i.track === 'identity' ? [['VERIFIED', 'Verified']] : []).concat([['NOT_REQUIRED', 'Not required for us'], ['DENIED', 'Denied']]) },
          { k: 'eff', label: 'Effective date', type: 'date' }, { k: 'exp', label: 'Expires (if any)', type: 'date' }, { k: 'ref', label: 'Payer reference or ID', span: 2 },
          { k: 'doc', label: 'Evidence document', type: 'select', span: 2, blank: 'None, I’ll describe it below', options: D.documents.map(function (d) { return [d.id, d.typeLabel + ' · ' + d.filename]; }) },
          { k: 'note', label: 'Or describe the evidence', type: 'textarea', rows: 2, span: 4, ph: 'e.g. Confirmed by phone with the payer’s enrollment team, call reference 12345' }]),
        [['Cancel', '', function () {}], ['Confirm', 'pri', function (close, w) { var v = U.values(w);
          act(function () { return api('POST', '/api/billing/items/' + i.id + '/confirm', { status: v.status, effectiveDate: v.eff, expiresAt: v.exp, reference: v.ref, documentId: v.doc || null, note: v.note }); }, 'Confirmed.'); }]], { width: 560 });
    });
    U.on(app, '[data-policy]', 'change', function (cb) { var o = {}; o[cb.dataset.policy] = cb.checked; act(function () { return api('PUT', '/api/billing/policy', o); }, 'Saved.'); });
    U.on(app, '#prov-add', 'change', function (sel) {
      var v = sel.value; if (!v) return; var s = D.staffPhysicians.filter(function (x) { return x.personId === v; })[0];
      D.profile.providers = collectProviders(); D.profile.providers.push({ id: '', personId: v === '__new' ? '' : v, name: s ? s.name : '', npi: '', source: s ? 'Staff record' : 'Entered here' }); draw();
    });
    app.addEventListener('submit', function (e) {
      if (e.target.id === 'f-profile') { e.preventDefault();
        var body = { legalName: val('p-name'), npi: val('p-npi'), taxonomy: val('p-tax'), address: { line1: val('p-a1'), city: val('p-city'), state: val('p-state'), zip: val('p-zip') }, contact: { name: val('p-cname'), email: val('p-cemail') } };
        if (val('p-tin').trim()) body.tin = val('p-tin');
        act(function () { return api('PUT', '/api/billing/profile', body); }, 'Billing details saved.'); }
      if (e.target.id === 'f-doc') { e.preventDefault();
        var f = document.getElementById('doc-file').files[0]; if (!f) return U.toast('Choose a file first.', 'warn');
        var fd = new FormData(); fd.append('doc_type', val('doc-type')); fd.append('item_id', val('doc-item')); fd.append('file', f);
        U.toast('Uploading and checking…', 'busy');
        act(function () { return api('POST', '/api/billing/documents', null, fd); }, 'Document checked.'); }
    });
  };
  function D_today() { return window.D.today(); }
  function ME_NAME() { var u = window.__ALTHAIS_USER__ || {}; return u.full_name || u.name || ''; }
})();
