// Reads claim fields from the page in front of the person. It never looks for a patient name or date of birth.
// Runs two ways: injected by the side panel ("Read page"), and as part of the on-page overlay (content.js), which calls
// __althaisParse({keep: true}) so it can remember which elements held each field and outline them when there's a problem.
// Returns { fields, found, guessed, diag, title, host }.
globalThis.__althaisParse = function (opts) {
  opts = opts || {};
  var MAXTXT = 80;
  var txt = function (el) { return (el && (el.innerText || el.textContent) || "").replace(/\s+/g, " ").trim(); };
  var norm = function (s) { return String(s || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim(); };
  var ICD = /\b[A-TV-Z][0-9][0-9AB](?:\.[0-9A-TV-Z]{1,4})?\b/gi;
  var CPT = /\b(?:\d{5}|[A-V]\d{4})\b/gi;
  var MOD = /\b[A-Z0-9]{2}\b/g;

  var KINDS = [
    ["claim_id", /^(claim|claim id|claim number|claim no|claim #|encounter id|encounter number|visit id|invoice number)$/],
    ["dos", /^(dos|date of service|service date|service dates?|billed service date|visit date|encounter date|from date)$/],
    ["payer", /^(payer|payor|payer name|insurance|insurance name|insurance company|primary insurance|primary payer|primary insurance payer|carrier|plan|dental plan|primary dental plan|dental insurance|primary dental insurance|dental carrier)$/],
    ["cpt", /^(cpt|cpt code|cpt codes|cpt hcpcs|hcpcs|procedure|procedures|procedure code|procedure codes|proc code|proc|service code|charge code|code|cdt|cdt code|ada code)$/],
    // dental screens: which tooth, which surfaces, which quadrant/arch each procedure is on
    ["tooth", /^(tooth|tooth number|tooth no|tooth num|th|teeth)$/],
    ["surface", /^(surface|surfaces|surf|tooth surface)$/],
    ["quad", /^(quad|quadrant|arch)$/],
    ["icd", /^(icd|icd 10|icd10|icd 10 code|icd 10 codes|diagnosis|diagnoses|diagnosis code|diagnosis codes|dx|dx code|dx codes|dx codes icd 10)$/],
    ["modifiers", /^(modifier|modifiers|mod|mods|mod 1|mod 2|modifier 1|modifier 2)$/],
    ["amount", /^(charge|charges|charge amount|amount|total charge|total charges|billed|billed amount|fee|total)$/],
    ["pos", /^(pos|place of service)$/]
  ];
  function kindOf(label) {
    var n = norm(label);
    if (!n || n.length > 40) return null;
    for (var i = 0; i < KINDS.length; i++) if (KINDS[i][1].test(n)) return KINDS[i][0];
    return null;
  }

  var raw = { claim_id: [], dos: [], payer: [], cpt: [], icd: [], modifiers: [], amount: [], pos: [], tooth: [], surface: [], quad: [] };
  // Where each field lives on the page: refs = elements holding a value, labels = the label, empty input or table to point at
  // when the value is missing. Only kept in memory in this tab; never sent anywhere.
  var refs = {}, labelEls = {}, empties = {}, lines = [], rowRefs = [], toothColumn = false;
  var note = function (map, k, el) { if (!el || !el.nodeType) return; (map[k] = map[k] || []); if (map[k].indexOf(el) < 0 && map[k].length < 40) map[k].push(el); };
  var add = function (k, v, el, lab) {
    v = String(v || "").replace(/\s+/g, " ").trim();
    if (lab) note(labelEls, k, lab);
    if (v && v.length <= 400) { if (raw[k].indexOf(v) < 0) raw[k].push(v); note(refs, k, el); }
    else if (el) note(labelEls, k, el);
  };

  // 1) form controls: label[for], wrapping label, aria-label, placeholder, name
  var controls = document.querySelectorAll("input, select, textarea");
  controls.forEach(function (el) {
    var t = (el.type || "").toLowerCase();
    if (/^(password|hidden|file|checkbox|radio|submit|button|search)$/.test(t)) return;
    var label = el.getAttribute("aria-label") || "";
    if (!label && el.id) { var l = document.querySelector('label[for="' + el.id.replace(/"/g, "") + '"]'); if (l) label = txt(l); }
    if (!label) { var w = el.closest("label"); if (w) label = txt(w).replace(txt({ innerText: el.value }), ""); }
    var k = kindOf(label) || kindOf(el.getAttribute("placeholder")) || kindOf(el.getAttribute("name")) || kindOf(el.id);
    var v = el.tagName === "SELECT" ? (el.value ? txt(el.options[el.selectedIndex]) : "") : el.value;
    if (k) add(k, v, el, el);
  });

  // 2) label/value pairs: dt/dd, th/td, two-cell rows, and "Label: value" text nodes
  document.querySelectorAll("dt").forEach(function (dt) { var k = kindOf(txt(dt)); var dd = dt.nextElementSibling; if (k && dd && dd.tagName === "DD") add(k, txt(dd), dd, dt); });
  document.querySelectorAll("tr").forEach(function (tr) {
    var cells = tr.children;
    if (cells.length === 2) { var k = kindOf(txt(cells[0])); if (k) add(k, txt(cells[1]), cells[1], cells[0]); }
  });
  document.querySelectorAll("label, span, div, p, strong, b, th, td, dt").forEach(function (el) {
    if (el.children.length > 1) return;
    var t = txt(el);
    if (!t || t.length > MAXTXT) return;
    var m = t.match(/^([^:]{2,40}):\s*(.+)$/);
    if (m) { var k = kindOf(m[1]); if (k) { add(k, m[2], el, el); return; } }
    var k2 = kindOf(t.replace(/:$/, ""));
    if (k2 && !m && !el.closest("thead") && !(el.tagName === "TH" && el.parentElement.children.length > 2)) {
      var nx = el.nextElementSibling;
      if (nx && kindOf(txt(nx).replace(/:$/, ""))) nx = null;
      if (nx && txt(nx) && txt(nx).length <= 200) add(k2, txt(nx), nx, el);
      else if (!nx && el.parentElement && el.parentElement.children.length === 2 && el.parentElement.children[0] === el && !kindOf(txt(el.parentElement.children[1]))) add(k2, txt(el.parentElement.children[1]), el.parentElement.children[1], el);
    }
  });

  var tableAmounts = [];
  // 3) table columns: header names decide which column holds what
  document.querySelectorAll("table").forEach(function (tb) {
    var head = tb.querySelector("thead tr") || tb.querySelector("tr");
    if (!head) return;
    var cols = Array.prototype.map.call(head.children, function (h) { return kindOf(txt(h)); });
    if (!cols.some(Boolean) || cols.filter(Boolean).length < 1) return;
    cols.forEach(function (k) { if (k === "cpt" || k === "icd" || k === "modifiers" || k === "amount") note(labelEls, k, tb); });
    var rows = tb.querySelectorAll("tbody tr");
    if (!rows.length) rows = Array.prototype.slice.call(tb.querySelectorAll("tr"), 1);
    // Cells can hold plain text or an input (editable claim screens); an empty input in a code column is a place to fill.
    var cell = function (td) { var c = td.querySelector("input, select, textarea"); return { v: c ? (c.tagName === "SELECT" ? (c.value ? txt(c.options[c.selectedIndex]) : "") : c.value) : txt(td), el: c || td, ctl: c }; };
    var hasCpt = cols.indexOf("cpt") >= 0;
    if (hasCpt && cols.indexOf("tooth") >= 0) toothColumn = true;
    rows.forEach(function (tr, ri) {
      var line = {}, els = {};
      Array.prototype.forEach.call(tr.children, function (td, i) {
        var k = cols[i];
        if (!k) { if (hasCpt && /^units?$/i.test(txt(head.children[i] || {}))) line.units = cell(td).v; return; }
        var c = cell(td);
        if (k === "tooth" || k === "surface" || k === "quad") {            // per-line dental detail, kept with its procedure
          if (!hasCpt) { add(k, c.v, c.el); return; }
          line[k] = c.v; els[k] = c.el; note(refs, k, c.el); if (c.ctl && !c.v) note(empties, k, c.ctl);
          return;
        }
        if (k === "cpt") els.cpt = c.el;
        if (c.ctl && !c.v && (k === "cpt" || k === "icd" || k === "modifiers")) { note(empties, k, c.ctl); return; }
        if (k === "claim_id" || k === "dos" || k === "payer" || k === "pos") { if (ri === 0) add(k, c.v, c.el); }
        else if (k === "amount") { tableAmounts.push(c.v); note(refs, k, c.el); line.charge = c.v; }
        else { add(k, c.v, c.el); if (k === "cpt") line.code = c.v; if (k === "modifiers" && c.v) line.mod = (line.mod ? line.mod + " " : "") + c.v; }
      });
      if (line.code && lines.length < 40) { lines.push(line); rowRefs.push({ code: String(line.code).toUpperCase().trim(), tooth: String(line.tooth || "").replace(/[#\s]/g, "").toUpperCase(), els: els }); }
    });
  });

  // ---- tidy into the shape the server wants
  function splitCodes(list, re, upper) {
    var out = [];
    list.forEach(function (s) { (String(s).toUpperCase().match(re) || []).forEach(function (c) { if (out.indexOf(c) < 0) out.push(c); }); });
    return out;
  }
  // Placeholders like "(no insurance on file)" or "Select..." mean the field is empty.
  var EMPTY = /^[\(\[\-\s]*(none|no |not |n\/a|na$|select|choose|unknown|tbd|--)/i;
  var pick = function (list) { for (var i = 0; i < list.length; i++) if (!EMPTY.test(list[i])) return list[i]; return ""; };
  var fields = { claim_id: pick(raw.claim_id), dos: pick(raw.dos), payer: pick(raw.payer), pos: pick(raw.pos) };
  // Labelled fields keep code-like tokens even when they are malformed (e.g. an ICD-9 "7865"), so Althais can flag them
  // instead of the reader silently dropping them.
  function labelled(list, re, shape) {
    var out = splitCodes(list, re);
    list.forEach(function (s) {
      String(s).toUpperCase().split(/[\s,;\/|]+/).forEach(function (t) {
        t = t.replace(/^[^A-Z0-9]+|[^A-Z0-9.]+$/g, "");
        if (shape.test(t) && /\d/.test(t) && out.indexOf(t) < 0 && !out.some(function (o) { return t.indexOf(o) === 0; })) out.push(t);
      });
    });
    return out.slice(0, 20);
  }
  fields.cpt = labelled(raw.cpt, CPT, /^[A-Z0-9]{4,6}$/);
  fields.icd = labelled(raw.icd, ICD, /^[A-Z0-9][A-Z0-9.]{2,7}$/);
  fields.modifiers = [];
  raw.modifiers.forEach(function (s) { String(s).toUpperCase().split(/[\s,;\/]+/).forEach(function (m) { if (/^[A-Z0-9]{2}$/.test(m) && fields.modifiers.indexOf(m) < 0) fields.modifiers.push(m); }); });
  var amt = 0;
  var num = function (s) { return parseFloat(String(s).replace(/[^0-9.\-]/g, "")); };
  if (tableAmounts.length) tableAmounts.forEach(function (s) { var n = num(s); if (!isNaN(n)) amt += n; });
  else for (var ai = 0; ai < raw.amount.length; ai++) { var an = num(raw.amount[ai]); if (!isNaN(an) && an > 0) { amt = an; break; } }
  amt = Math.round(amt * 100) / 100;
  fields.amount = amt || "";
  if (fields.payer.length > 120) fields.payer = fields.payer.slice(0, 120);

  if (!fields.claim_id) {
    var um = (location.search + "&" + location.hash.replace(/^#\/?/, "")).match(/[?&#;](?:claim_?id|claimnumber|claim_?no|claim)=([A-Za-z0-9\-_]{1,40})/i);
    if (um) fields.claim_id = um[1];
  }
  var found = {};
  ["claim_id", "dos", "payer", "cpt", "icd", "modifiers", "amount", "pos"].forEach(function (k) { found[k] = Array.isArray(fields[k]) ? fields[k].length > 0 : !!fields[k]; });

  // 4) last resort: pattern-match the visible text. These are guesses and the panel says so.
  var guessed = [];
  var body = (document.body && document.body.innerText || "");
  var ovHost = document.getElementById("althais-overlay");                    // never read Althais's own overlay text
  if (ovHost && ovHost.innerText) body = body.split(ovHost.innerText).join(" ");
  body = body.slice(0, 60000);
  if (!fields.icd.length) { var gi = splitCodes([body], ICD); if (gi.length && gi.length <= 12) { fields.icd = gi; guessed.push("icd"); found.icd = true; } }
  if (!fields.cpt.length) {
    var gc = [];
    var re = /(?:cpt|procedure|hcpcs)\D{0,15}?(\d{5}|[A-V]\d{4})\b/gi, mm;
    while ((mm = re.exec(body)) && gc.length < 8) if (gc.indexOf(mm[1].toUpperCase()) < 0) gc.push(mm[1].toUpperCase());
    if (gc.length) { fields.cpt = gc; guessed.push("cpt"); found.cpt = true; }
  }
  var diag = null;
  if (opts.diag) {
    var shape = function (v) { return String(v || "").replace(/[A-Z]/g, "A").replace(/[a-z]/g, "a").replace(/[0-9]/g, "9").slice(0, 14); };
    var labels = [], seenL = {};
    document.querySelectorAll("label, th, dt, span, div, td, strong, b").forEach(function (el) {
      if (labels.length >= 120 || el.children.length > 1) return;
      var t = txt(el);
      if (!t || t.length > 40) return;
      var colon = /:$/.test(t) || el.tagName === "TH" || el.tagName === "LABEL" || el.tagName === "DT";
      if (!colon) return;
      var nx = el.nextElementSibling, key = t;
      if (seenL[key]) return; seenL[key] = 1;
      labels.push({ label: t.replace(/:$/, ""), tag: el.tagName.toLowerCase(), kind: kindOf(t.replace(/:$/, "")), next: nx ? shape(txt(nx)) : "" });
    });
    var inputs = [];
    controls.forEach(function (el) {
      if (inputs.length >= 60 || /^(hidden|password)$/i.test(el.type || "")) return;
      inputs.push({ tag: el.tagName.toLowerCase(), type: el.type || "", name: el.name || "", id: el.id || "", aria: el.getAttribute("aria-label") || "", ph: el.getAttribute("placeholder") || "", val: shape(el.value) });
    });
    var heads = [];
    document.querySelectorAll("table").forEach(function (tb) {
      var h = tb.querySelector("thead tr") || tb.querySelector("tr");
      if (h && heads.length < 20) heads.push(Array.prototype.map.call(h.children, function (c) { return txt(c).slice(0, 30); }));
    });
    var shadow = 0; document.querySelectorAll("*").forEach(function (e) { if (e.shadowRoot) shadow++; });
    var qnames = []; try { new URLSearchParams(location.search).forEach(function (v, k) { qnames.push(k); }); } catch (e) {}
    diag = { top: window === window.top, path: location.origin + location.pathname, query: qnames, labels: labels, inputs: inputs, tableHeaders: heads,
      counts: { tables: document.querySelectorAll("table").length, iframes: document.querySelectorAll("iframe, frame").length, inputs: controls.length, shadowRoots: shadow, textLength: (document.body && document.body.innerText || "").length } };
  }
  if (opts.keep) globalThis.__althaisRefs = { refs: refs, labels: labelEls, empties: empties, rows: rowRefs };
  fields.lines = lines.map(function (l) {
    var code = (String(l.code).toUpperCase().match(CPT) || [""])[0];
    var out = { code: code, mod: String(l.mod || "").toUpperCase().replace(/[^A-Z0-9 ]/g, "").trim().split(/\s+/)[0] || "", units: parseInt(l.units, 10) || 1, charge: l.charge ? Math.round((num(l.charge) || 0) * 100) / 100 : "" };
    if (toothColumn || l.surface || l.quad) {
      out.tooth = String(l.tooth || "").replace(/[#\s]/g, "").toUpperCase().slice(0, 4);
      out.surface = String(l.surface || "").replace(/[^A-Za-z]/g, "").toUpperCase().slice(0, 7);
      out.quad = String(l.quad || "").replace(/[^A-Za-z]/g, "").toUpperCase().slice(0, 4);
    }
    return out;
  }).filter(function (l) { return l.code; });
  fields.tooth_column = toothColumn;
  return { fields: fields, found: found, guessed: guessed, diag: diag, title: (document.title || "").slice(0, 80), host: location.hostname };
};
globalThis.__althaisParse({ diag: !!globalThis.__althaisDiag, keep: true })
