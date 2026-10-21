/* v4 working preview: the claim appeal packet as a PDF (Denials › Appeal Claim).
   Page 1 is the appeal request form: the provider, the patient and member, the claim and its denial, the services
   and diagnoses, why it should be paid, what's enclosed, and the provider's attestation and signature line. Medicare
   claims get a redetermination request (first-level Medicare appeal); every other payer a first-level appeal /
   reconsideration request. The letter from the Denials screen follows on its own page(s).
   The PDF is built in the browser (static/v4/vendor/jspdf.umd.min.js, loaded on first use), so nothing about the
   patient is sent anywhere to make it. */
(function () {
  var S = D.S;
  var loadPdf = function () {
    if (window.jspdf) return Promise.resolve(window.jspdf);
    return new Promise(function (ok, no) {
      var s = document.createElement('script'); s.src = '/static/v4/vendor/jspdf.umd.min.js';
      s.onload = function () { ok(window.jspdf); }; s.onerror = function () { no(new Error('The PDF tool didn’t load. Check your connection and try again.')); };
      document.head.appendChild(s);
    });
  };
  var money = function (n) { return '$' + Number(n || 0).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ','); };
  var nameFirstLast = function (n) { return V.personName(n); };

  V.appealPdf = function (c) {
    return loadPdf().then(function (lib) {
      var s = V.claimSnap(c), p = D.pt(c.mrn) || {}, prov = D.staff(c.prov) || {}, clinic = S.clinic || {};
      var medicare = /^medicare/i.test(s.payer || '');
      var doc = new lib.jsPDF({ unit: 'pt', format: 'letter' });
      var W = 612, H = 792, M = 42, CW = W - 2 * M, y = M;
      var ink = [17, 24, 39], mut = [96, 104, 116], line = [205, 211, 219], band = [238, 241, 245], blue = [29, 91, 184];

      var font = function (sz, bold, col) { doc.setFont('helvetica', bold ? 'bold' : 'normal'); doc.setFontSize(sz); doc.setTextColor.apply(doc, col || ink); };
      var room = function (h) { if (y + h > H - 44) { footer(); doc.addPage(); y = M; } };
      var pages = 1, footer = function () {
        font(7.5, false, mut);
        doc.text('Claim ' + c.id + ' · ' + (medicare ? 'Medicare redetermination request' : 'Claim appeal request') + ' · prepared ' + D.us(D.today()), M, H - 26);
        doc.text('Page ' + doc.internal.getNumberOfPages(), W - M, H - 26, { align: 'right' });
      };
      var section = function (t) {
        room(40); y += 5;
        doc.setFillColor.apply(doc, band); doc.rect(M, y, CW, 18, 'F');
        font(8.5, true, ink); doc.text(t.toUpperCase(), M + 8, y + 12.5); y += 18;
      };
      // a row of labeled boxes: [[label, value, widthFraction], ...]
      var boxes = function (cells, h) {
        h = h || 30; room(h);
        var x = M; doc.setDrawColor.apply(doc, line); doc.setLineWidth(0.6);
        cells.forEach(function (cell) {
          var w = CW * cell[2];
          doc.rect(x, y, w, h);
          font(6.8, false, mut); doc.text(String(cell[0]).toUpperCase(), x + 6, y + 10);
          font(9.5, !!cell[3], ink);
          var v = doc.splitTextToSize(String(cell[1] == null || cell[1] === '' ? '—' : cell[1]), w - 12);
          doc.text(v.slice(0, Math.max(1, Math.floor((h - 14) / 11))), x + 6, y + 21.5);
          x += w;
        });
        y += h;
      };
      var para = function (t, sz, gap) {
        font(sz || 9.5, false, ink);
        doc.splitTextToSize(String(t || ''), CW - 12).forEach(function (l) { room(13); doc.text(l, M + 6, y + 11); y += 12.5; });
        y += gap == null ? 4 : gap;
      };
      var check = function (on, label) {
        room(16); doc.setDrawColor.apply(doc, ink); doc.setLineWidth(0.7); doc.rect(M + 6, y + 3, 9, 9);
        if (on) { font(9, true, ink); doc.text('X', M + 8.2, y + 10.8); }
        font(9.5, false, ink); doc.text(doc.splitTextToSize(label, CW - 30)[0], M + 22, y + 11); y += 15;
      };

      /* ----- header ----- */
      font(15, true, ink); doc.text(medicare ? 'Medicare Redetermination Request' : 'Claim Appeal / Reconsideration Request', M, y + 12);
      font(9, false, mut); doc.text(medicare ? 'First-level appeal of a Medicare initial determination' : 'First-level provider appeal of a denied claim', M, y + 27);
      font(8.5, true, blue); doc.text(String(clinic.name || ''), W - M, y + 10, { align: 'right' });
      font(8, false, mut); doc.text([clinic.addr, clinic.city].filter(Boolean).join(', ') || ' ', W - M, y + 21, { align: 'right' });
      doc.text(['Date prepared ' + D.us(D.today()), clinic.phone ? 'Phone ' + clinic.phone : ''].filter(Boolean).join(' · '), W - M, y + 31, { align: 'right' });
      y += 42; doc.setDrawColor.apply(doc, ink); doc.setLineWidth(1.2); doc.line(M, y, W - M, y); y += 6;

      boxes([['To', (s.payer || '') + ' · Appeals / Reconsideration', 0.6, 1], ['Appeal level', medicare ? 'Redetermination (level 1)' : 'First-level appeal', 0.4]]);

      /* ----- patient / member ----- */
      section(medicare ? '1. Beneficiary' : '1. Patient and member');
      boxes([[medicare ? 'Beneficiary name' : 'Patient name', nameFirstLast(p.name), 0.4, 1], ['Date of birth', D.us(p.dob), 0.2], [medicare ? 'Medicare number (MBI)' : 'Member / subscriber ID', p.member, 0.25], ['Account / MRN', c.mrn, 0.15]]);
      var ins = p.insurance || {};
      boxes([['Subscriber', ins.subscriber || nameFirstLast(p.name), 0.4], ['Relationship to patient', ins.relationship || 'Self', 0.2], ['Group number', ins.group, 0.2], ['Plan', ins.plan, 0.2]]);

      /* ----- provider ----- */
      section('2. Provider requesting the appeal');
      boxes([['Billing provider / practice', clinic.name, 0.4, 1], ['Billing NPI', clinic.npi, 0.2], ['Tax ID (TIN)', clinic.ein, 0.2], ['Phone', clinic.phone, 0.2]]);
      boxes([['Rendering provider', V.personName(prov.name || s.provider) + (prov.cred && String(prov.name || '').indexOf(prov.cred) < 0 ? ', ' + prov.cred : ''), 0.4], ['Rendering NPI', prov.npi || s.npi, 0.2], ['Address', [clinic.addr, clinic.city].filter(Boolean).join(', '), 0.4]]);

      /* ----- claim and denial ----- */
      section('3. Claim and denial');
      boxes([['Claim number', c.id, 0.22, 1], ['Payer claim / ICN', c.icn || c.payerClaim || c.ext, 0.2], ['Date(s) of service', D.us(c.dos), 0.18], ['Billed amount', money(D.charge(c)), 0.2], ['Amount denied', money(c.deniedAmt != null ? c.deniedAmt : D.charge(c)), 0.2]]);
      boxes([['Denial date / remit date', D.us(c.deniedOn), 0.22], ['Denial reason (CARC)', (c.carc || '') + (c.reason ? ' · ' + c.reason : ''), 0.43], ['Remark (RARC) / note', c.remark, 0.2], ['Appeal deadline', D.us(c.appealBy), 0.15]], 32);

      /* ----- services ----- */
      section('4. Services appealed');
      var cols = [['Line', 0.06], [D.DENTAL ? 'CDT' : 'CPT / HCPCS', 0.13], [D.DENTAL ? 'Tooth / surface' : 'Modifier', 0.1], ['Description', 0.45], ['Units', 0.08], ['Charge', 0.18]];
      room(18); var x = M; font(7, true, mut);
      cols.forEach(function (k) { doc.text(k[0].toUpperCase(), x + 5, y + 12); x += CW * k[1]; });
      y += 16; doc.setDrawColor.apply(doc, line); doc.line(M, y, W - M, y);
      s.cpts.forEach(function (l, i) {
        var desc = doc.splitTextToSize(l.description || '', CW * 0.45 - 10).slice(0, 2), h = 8 + desc.length * 11;
        room(h + 2); x = M; font(9, false, ink);
        [String(i + 1), l.code, D.DENTAL ? [l.tooth ? '#' + l.tooth : '', l.surface || '', l.quad || ''].filter(Boolean).join(' ') : l.modifier || '', desc, String(l.units || 1), money(l.charge)].forEach(function (v, k) { doc.text(v, k === 5 ? x + CW * cols[k][1] - 6 : x + 5, y + 12, k === 5 ? { align: 'right' } : undefined); x += CW * cols[k][1]; });
        y += h; doc.line(M, y, W - M, y);
      });
      if (!D.DENTAL || s.icds.length) { y += 4; font(7, true, mut); room(14); doc.text('DIAGNOSES (ICD-10-CM)', M + 5, y + 10); y += 14; }
      s.icds.forEach(function (d, i) { para(String.fromCharCode(65 + i) + '.  ' + d.code + '  ' + (d.description || ''), 9, 0); });

      /* ----- reason ----- */
      section('5. Why this claim should be paid');
      var basis = String(c.basis || '').trim();
      if (basis) basis.split(/\n+/).forEach(function (b) { para('•  ' + b.replace(/^[-•*]\s*/, ''), 9.5, 0); });
      else para('See the enclosed letter.', 9.5, 0);
      y += 4;

      /* ----- enclosures ----- */
      section('6. Enclosed');
      var att = c.attach || [], docs = S.docs.filter(function (d) { return att.indexOf(d.id) >= 0; });
      check(true, 'Appeal letter (following page)');
      check(true, 'Copy of the remittance advice / denial notice for claim ' + c.id);
      check(att.indexOf('note') >= 0, 'Signed progress note for the date of service');
      docs.forEach(function (d) { check(true, d.name); });
      check(false, 'Other: ____________________________________________'); y -= 2;

      /* ----- attestation ----- */
      section('7. Attestation');
      para('I request ' + (medicare ? 'a redetermination' : 'reconsideration') + ' of the claim above. The information in this request and its enclosures is true, accurate and complete to the best of my knowledge, and the services were medically necessary and provided as documented.', 9.5, 10);
      room(52); doc.setDrawColor.apply(doc, ink); doc.setLineWidth(0.7);
      doc.line(M + 6, y + 30, M + 300, y + 30); doc.line(M + 330, y + 30, W - M - 6, y + 30);
      font(9.5, false, ink); doc.text(V.personName(prov.name || s.provider) + (prov.cred && String(prov.name || '').indexOf(prov.cred) < 0 ? ', ' + prov.cred : '') + (prov.npi ? '  ·  NPI ' + prov.npi : ''), M + 6, y + 26);
      font(7, false, mut); doc.text('SIGNATURE OF PROVIDER OR AUTHORIZED REPRESENTATIVE / PRINTED NAME', M + 6, y + 40); doc.text('DATE', M + 330, y + 40);
      y += 52;
      footer();

      /* ----- letter ----- */
      doc.addPage(); y = M;
      font(8.5, true, blue); doc.text(String(clinic.name || ''), M, y + 10);
      font(8, false, mut); doc.text([[clinic.addr, clinic.city].filter(Boolean).join(', '), clinic.phone].filter(Boolean).join(' · ') || ' ', M, y + 21);
      font(9.5, false, ink); doc.text(D.us(D.today()), W - M, y + 10, { align: 'right' });
      y += 40;
      font(10, true, ink); doc.text('Re: Appeal of claim ' + c.id + ' · ' + nameFirstLast(p.name) + ' · DOS ' + D.us(c.dos) + (p.member ? ' · ID ' + p.member : ''), M, y); y += 18;
      var letter = String(c.letter || '').trim();
      font(10.5, false, ink);
      letter.split(/\n/).forEach(function (ln) {
        var parts = ln ? doc.splitTextToSize(ln, CW) : [''];
        parts.forEach(function (t) { room(15); doc.text(t, M, y + 11); y += 14.5; });
      });
      footer();

      var file = 'Appeal-' + c.id.replace(/[^A-Za-z0-9-]+/g, '-') + '.pdf';
      doc.save(file);
      return file;
    });
  };
})();
/* "Last, First" (patients) → "First Last"; "First Last, CRED" (staff) stays as written */
V.personName = function (n) {
  n = String(n || '').trim(); var i = n.indexOf(',');
  if (i < 0) return n;
  var tail = n.slice(i + 1).trim();
  // a credential tail: every word is an abbreviation with two or more capitals (MD, FNP-C, MD MBA, Ph.D., DO)
  var cred = tail.split(/[\s,/]+/).filter(Boolean).every(function (w) { return (w.match(/[A-Z]/g) || []).length >= 2 && !/[a-z]{3}/.test(w); });
  return cred ? n : tail + ' ' + n.slice(0, i).trim();
};
