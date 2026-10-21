// Althais on-page overlay. Runs in every frame of a site the biller allowed (and on Althais's synthetic test screens).
// Every frame: reports page changes, answers "scan" with what parse.js reads, outlines the fields a check is about, and
//              notices a click on the EMR's own Submit button. It never changes the EMR page's own content or blocks a click.
// Top frame:   draws the floating pill and result card in an isolated shadow root.
(() => {
  // After the extension updates, the old copy in this page stops working. It removes itself (see the end of this file), and a
  // fresh copy is injected; a live copy is never doubled.
  if (window.__althaisContent && window.__althaisContent()) return;
  if (window.__althaisTeardown) try { window.__althaisTeardown(); } catch (e) {}
  const alive = () => { try { return !!chrome.runtime.id; } catch (e) { return false; } };
  window.__althaisContent = alive;
  const S = globalThis.AlthaisShared;
  const TOP = window === window.top;
  const send = (m) => { try { chrome.runtime.sendMessage(m).catch(() => {}); } catch (e) {} };   // the extension may have been reloaded
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
  const MARK = '<svg viewBox="120 85.5 1114 1114" aria-hidden="true"><path d="M353 179Q390 142 428 177L676 402L420 648L197 406Q162 368 199 331ZM924 177Q963 142 1000 178L1157 332Q1194 368 1158 406L930 648L676 402ZM420 648L676 884L429 1107Q390 1142 353 1105L199 952Q162 915 198 878ZM930 648L1151 878Q1187 915 1150 951L992 1106Q955 1142 917 1107L676 884Z"/></svg>';

  let verdict = null;              // latest result for this tab: { verdict, score, flags, fields }
  let outlines = true;
  chrome.storage.local.get(["outlines"], (s) => { outlines = s.outlines !== false; });

  // ---------------- change detection ----------------
  const OURS = new Set(["althais-overlay", "althais-marks"]);
  let ct = 0;
  const changed = () => { clearTimeout(ct); ct = setTimeout(() => send({ t: "changed" }), 600); };
  const mo = new MutationObserver((list) => { if (list.some((m) => !(m.target && OURS.has(m.target.id)) && !(m.addedNodes.length === 1 && OURS.has(m.addedNodes[0].id)))) changed(); });
  mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  document.addEventListener("input", changed, true);
  document.addEventListener("change", changed, true);
  window.addEventListener("hashchange", changed);
  window.addEventListener("popstate", changed);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) changed(); });
  changed();

  // ---------------- outlines (every frame) ----------------
  let marksRoot = null, boxes = [], raf = 0;
  function marksLayer() {
    if (marksRoot) return marksRoot;
    const host = document.createElement("div");
    host.id = "althais-marks";
    host.style.cssText = "all:initial;position:fixed;inset:0;pointer-events:none;z-index:2147482999;";
    (document.body || document.documentElement).appendChild(host);
    marksRoot = host.attachShadow({ mode: "closed" });
    marksRoot.innerHTML = `<style>
      .b{position:fixed;box-sizing:border-box;border:2px solid var(--c);border-radius:5px;background:color-mix(in srgb,var(--c) 7%,transparent);transition:opacity .15s}
      .b span{position:absolute;right:-2px;top:-20px;font:600 11px/18px system-ui,-apple-system,"Segoe UI",sans-serif;color:#fff;background:var(--c);padding:0 7px;border-radius:4px 4px 0 4px;white-space:nowrap;letter-spacing:.01em}
      .b.below span{top:auto;bottom:-20px;border-radius:4px 0 4px 4px}
      .b.pulse{animation:p 1.1s ease-out 2}
      @keyframes p{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--c) 55%,transparent)}100%{box-shadow:0 0 0 12px transparent}}
      @media (prefers-reduced-motion:reduce){.b.pulse{animation:none;outline:3px solid var(--c)}}
    </style><div id="w"></div>`;
    return marksRoot;
  }
  const COLOR = { error: "#d92d20", warn: "#dc8a09", info: "#1d5bb8" };
  const WORD = { error: "Fix", warn: "Check", info: "Note" };

  function targetsFor(flag) {
    const R = globalThis.__althaisRefs;
    if (!R) return [];
    const kind = S.KIND_OF[flag.code];
    if (!kind) return [];
    const live = (els) => (els || []).filter((el) => el.isConnected);
    if ((kind === "tooth" || kind === "surface" || kind === "quad" || (kind === "cpt" && flag.tooth)) && R.rows && R.rows.length) {
      const rows = R.rows.filter((r) => flag.tooth ? r.tooth === flag.tooth : (flag.codes || []).includes(r.code) || String(flag.message || "").toUpperCase().includes(r.code));
      const want = flag.code === "no_tooth" ? rows.filter((r) => !r.tooth) : rows;
      const hit = live(want.map((r) => r.els[kind] || r.els.cpt).filter(Boolean));
      if (hit.length) return hit.slice(0, 6);
    }
    let els = live(R.refs[kind]);
    if (flag.codes && flag.codes.length && els.length) {
      const hit = els.filter((el) => { const t = String(el.value || el.innerText || el.textContent || "").toUpperCase(); return flag.codes.some((c) => t.includes(String(c).toUpperCase())); });
      if (hit.length) els = hit;
    }
    const missing = /^no_/.test(flag.code);
    if (!els.length || missing) { const lab = live(R.labels[kind]); if (lab.length) els = missing ? lab : els.concat(lab).slice(0, 1); }
    // Point at the box the biller types in, not its label, whenever the field is a form control.
    const ctl = els.filter((el) => /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName));
    if (ctl.length) els = ctl;
    return els.slice(0, 6);
  }

  function drawMarks() {
    boxes = [];
    if (!verdict || !verdict.verdict || !outlines) { if (marksRoot) marksRoot.getElementById("w").innerHTML = ""; return; }
    const w = marksLayer().getElementById("w");
    w.innerHTML = "";
    const rank = { error: 3, warn: 2, info: 1 };
    const byEl = new Map();
    verdict.flags.forEach((f, i) => {
      const s = S.sev(f.severity);
      targetsFor(f).forEach((el) => {
        const cur = byEl.get(el);
        if (!cur || rank[s] > rank[cur.s]) byEl.set(el, { s, i, kind: S.KIND_OF[f.code], flags: [i].concat(cur ? cur.flags : []) });
        else cur.flags.push(i);
      });
    });
    const labelled = new Set();                 // one tag per kind, so neighbouring cells don't stack labels
    byEl.forEach((v, el) => {
      const d = document.createElement("div");
      d.className = "b";
      d.style.setProperty("--c", COLOR[v.s]);
      const key = v.s + v.kind;
      if (!labelled.has(key)) { labelled.add(key); d.innerHTML = "<span>" + WORD[v.s] + " · " + esc(S.KIND_LABEL[v.kind] || "") + "</span>"; }
      w.appendChild(d);
      boxes.push({ el, d, flags: v.flags });
    });
    place();
  }
  function place() {
    raf = 0;
    const vh = innerHeight, vw = innerWidth;
    boxes.forEach((b) => {
      const r = b.el.getBoundingClientRect();
      const vis = r.width > 2 && r.height > 2 && r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
      b.d.style.opacity = vis ? "1" : "0";
      if (!vis) return;
      Object.assign(b.d.style, { left: r.left - 3 + "px", top: r.top - 3 + "px", width: r.width + 6 + "px", height: r.height + 6 + "px" });
      b.d.classList.toggle("below", r.top < 24);
    });
  }
  const queuePlace = () => { if (!raf && boxes.length) raf = requestAnimationFrame(place); };
  addEventListener("scroll", queuePlace, true);
  addEventListener("resize", queuePlace);
  setInterval(queuePlace, 800);

  function focusFlag(i, code, codes) {
    if (code && verdict) {                     // the side panel names the problem by its check, not its position
      const k = JSON.stringify(codes || []);
      i = verdict.flags.findIndex((f) => f.code === code && JSON.stringify(f.codes || []) === k);
      if (i < 0) i = verdict.flags.findIndex((f) => f.code === code);
    }
    const b = boxes.find((x) => x.flags.includes(i));
    if (!b) return;
    b.el.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    b.d.classList.remove("pulse"); void b.d.offsetWidth; b.d.classList.add("pulse");
    setTimeout(queuePlace, 350);
  }

  // ---------------- the EMR's own Submit button ----------------
  document.addEventListener("click", (e) => {
    if (!verdict || verdict.verdict !== "fix") return;
    const el = e.target && e.target.closest && e.target.closest('button, input[type="submit"], input[type="button"], [role="button"], a');
    if (!el || el.closest("#althais-overlay")) return;
    const label = String(el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim();
    if (label.length <= 40 && /\b(submit|send|release|bill)\b/i.test(label)) {
      send({ t: "submitWarn", n: verdict.flags.filter((x) => x.severity === "error").length });
    }
  }, true);

  // ---------------- chart note, patient details, and filling codes (only when the biller asks) ----------------
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const textOf = (el) => (el.value != null && /^(TEXTAREA|INPUT)$/.test(el.tagName) ? el.value : el.innerText || "").trim();
  const NOTE_HINT = /\b(note|progress note|visit note|chart note|soap|assessment|plan|hpi|history of present illness|chief complaint|subjective|objective|exam|impression|medical decision)\b/i;

  // The visit note: the person's selection if there is one, else the largest note-like block on the screen.
  function readNote() {
    const sel = String(getSelection ? getSelection() : "").trim();
    if (sel.length >= 40) return { text: sel.slice(0, 20000), how: "selection", score: 1e9 };
    let best = null, bestScore = 0;
    document.querySelectorAll("textarea, [contenteditable=''], [contenteditable='true'], article, section, main, div, pre").forEach((el) => {
      if (el.closest("#althais-overlay, #althais-marks") || !vis(el)) return;
      const t = textOf(el);
      if (t.length < 80 || t.length > 40000) return;
      const head = (el.getAttribute("aria-label") || el.id || el.className || "") + " " + t.slice(0, 200);
      // prefer a block that looks like a note and isn't the whole page: note words count, size counts, the outermost wrappers don't
      const kids = el.querySelectorAll("div, section, article").length, links = el.querySelectorAll("a, button, li").length;
      const score = Math.min(t.length, 6000) * (NOTE_HINT.test(head) ? 3 : 1) * (el.tagName === "TEXTAREA" ? 2 : 1) / (1 + kids / 40) / (1 + links / 8);
      if (score > bestScore) { bestScore = score; best = t; }
    });
    return best ? { text: best.slice(0, 20000), how: "page", score: bestScore } : null;
  }

  // Patient and insurance details for a claim. Read only when the biller presses Create Claim.
  function readParty() {
    const out = {};
    const body = (document.body && document.body.innerText) || "";
    const ctlBy = (re) => {
      for (const el of document.querySelectorAll("input, select")) {
        let lab = el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.name || el.id || "";
        if (el.id) { const l = document.querySelector('label[for="' + el.id.replace(/"/g, "") + '"]'); if (l) lab = l.innerText + " " + lab; }
        if (re.test(lab)) { const v = el.tagName === "SELECT" ? (el.value ? el.options[el.selectedIndex].text : "") : el.value; if (v && !/^\(|^select|^none/i.test(v)) return v.trim(); }
      }
      return "";
    };
    const rx = (re) => { const m = body.match(re); return m ? m[m.length - 1].trim() : ""; };
    out.patient = ctlBy(/patient\s*name|^\s*patient\b/i);
    if (!out.patient) {
      const box = document.querySelector('[id*="banner" i], [class*="banner" i], [id*="patient" i], [class*="patient" i], [class*="demograph" i]');
      const b = box && box.querySelector("b, strong, h1, h2, h3");
      if (b && b.innerText.trim().length <= 60) out.patient = b.innerText.trim();
    }
    out.dob = ctlBy(/date of birth|\bdob\b|birth ?date/i) || rx(/\b(?:DOB|Date of birth)[:\s]+(\d{1,2}\/\d{1,2}\/\d{2,4}|\d{4}-\d{2}-\d{2})/i);
    out.sex = ctlBy(/\b(sex|gender)\b/i) || rx(/\b(?:Sex|Gender)[:\s]+(Male|Female|M|F|Other|Unknown)\b/i);
    out.insuredId = ctlBy(/member|subscriber|policy|insured id|insurance id/i) || rx(/\b(?:Member|Subscriber|Policy|Insured)\s*(?:ID|#|No\.?|Number)[:#\s]+([A-Z0-9-]{4,20})/i);
    out.provider = ctlBy(/rendering|provider|physician|attending|dentist|billing doctor/i);
    out.mrn = rx(/\b(?:MRN|Patient ID|Account(?: #| No\.?)?)[:#\s]+([A-Z0-9-]{3,20})/i);
    return out;
  }

  // Put codes into the EMR's empty code fields, as if typed, so the EMR saves them like any other edit.
  function fill(kind, codes) {
    const R = globalThis.__althaisRefs;
    const slots = ((R && R.empties[kind]) || []).filter((el) => el.isConnected && !el.value);
    const setter = (el, v) => {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);       // works with React and other frameworks too
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    };
    let n = 0;
    codes.forEach((c, i) => {
      if (!slots[i]) return;
      const item = typeof c === "string" ? { code: c } : c;
      setter(slots[i], item.code); n++;
      // dental: put the tooth, surfaces and quadrant in the same row as the code
      ["tooth", "surface", "quad"].forEach((k) => { if (item[k]) { const el = sameRow(slots[i], k); if (el && !el.value) setter(el, item[k]); } });
    });
    return { n, more: Math.max(0, codes.length - slots.length) };
  }
  const COL = { tooth: /^(tooth|tooth number|tooth no|tooth num|th|teeth)$/, surface: /^(surface|surfaces|surf|tooth surface)$/, quad: /^(quad|quadrant|arch)$/ };
  function sameRow(el, kind) {
    const tr = el.closest("tr"), tb = tr && tr.closest("table");
    if (!tb) return null;
    const head = tb.querySelector("thead tr") || tb.querySelector("tr");
    const norm = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
    const i = Array.prototype.findIndex.call(head.children, (h) => COL[kind].test(norm(h.innerText)));
    const td = i >= 0 ? tr.children[i] : null;
    return td ? td.querySelector("input, textarea") : null;
  }

  // ---------------- messages ----------------
  chrome.runtime.onMessage.addListener((m, _s, reply) => {
    if (m.t === "scan") {
      try { const r = globalThis.__althaisParse({ keep: true }); delete r.diag; reply(r); } catch (e) { reply(null); }
      return;
    }
    if (m.t === "verdict") {
      verdict = m.verdict ? m : null;
      drawMarks();
      if (TOP) ui.result(m);
      return;
    }
    if (m.t === "focus") { focusFlag(m.i, m.code, m.codes); return; }
    if (m.t === "note") { reply(readNote()); return; }
    if (m.t === "party") { reply(readParty()); return; }
    if (m.t === "fill") {
      try { globalThis.__althaisParse({ keep: true }); } catch (e) {}
      const present = (k) => { const R = globalThis.__althaisRefs; return ((R && R.refs[k]) || []).map((el) => String(el.value || el.innerText || "").toUpperCase()).join(" "); };
      const icd = (m.icd || []).filter((c) => !present("icd").includes(c.toUpperCase()));
      const R0 = globalThis.__althaisRefs, rows = (R0 && R0.rows) || [];
      const cpt = (m.cpt || []).filter((c) => {
        if (typeof c !== "string" && c.tooth) return !rows.some((r) => r.code === c.code && r.tooth === String(c.tooth).toUpperCase());   // the same procedure can be on several teeth
        const code = typeof c === "string" ? c : c.code;
        return !present("cpt").includes(code.toUpperCase());
      });
      const a = fill("icd", icd), b = fill("cpt", cpt);
      reply({ icd: a.n, cpt: b.n, skipped: (m.icd || []).length - icd.length + (m.cpt || []).length - cpt.length, noRoom: a.more + b.more });
      return;
    }
    if (!TOP) return;
    if (m.t === "state") ui.state(m.state, m.text);
    if (m.t === "submitWarn") ui.submitWarn(m.n);
    if (m.t === "toggle") ui.toggle();
    if (m.t === "panel") ui.panel(m.open);
  });
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.outlines) { outlines = ch.outlines.newValue !== false; drawMarks(); if (TOP) ui.render(); }
  });

  // ---------------- top frame: pill + card ----------------
  const ui = TOP ? makeUi() : null;

  window.__althaisTeardown = () => {
    try { mo.disconnect(); } catch (e) {}
    const m = document.getElementById("althais-marks"); if (m) m.remove();
    if (ui) ui.remove();
    verdict = null; boxes = [];
    window.__althaisContent = null; window.__althaisTeardown = null;
  };
  if (!TOP) { const wd = setInterval(() => { if (!alive()) { clearInterval(wd); window.__althaisTeardown && window.__althaisTeardown(); } }, 1500); }

  function makeUi() {
    const host = document.createElement("div");
    host.id = "althais-overlay";
    host.style.cssText = "all:initial;position:fixed;right:20px;bottom:20px;z-index:2147483000;";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `<style>
      :host{all:initial}
      *{box-sizing:border-box}
      .wrap{--brand:#1d5bb8;--fg:#1c2733;--fg2:#4a5868;--mut:#6b7785;--line:#d9dfe6;--bg:#fff;--soft:#f5f7fa;
        --red:#b42318;--red-bg:#fef3f2;--amb:#a15c07;--amb-bg:#fffaeb;--grn:#17784a;--grn-bg:#ecfdf3;
        font:13px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--fg);position:relative}
      @media (prefers-color-scheme:dark){.wrap{--fg:#e6e9ef;--fg2:#b3bbc9;--mut:#8a94a6;--line:#2d333e;--bg:#161a21;--soft:#1d222b;
        --red:#f97066;--red-bg:#2b1715;--amb:#fdb022;--amb-bg:#2a2011;--grn:#47cd89;--grn-bg:#11251b;--brand:#3b7ddd}}
      button{font:inherit;color:inherit;cursor:pointer}
      .pill{display:flex;align-items:center;gap:8px;height:38px;padding:0 14px 0 6px;border-radius:19px;border:1px solid var(--line);background:var(--bg);
        box-shadow:0 6px 20px rgba(16,24,40,.16),0 1px 3px rgba(16,24,40,.1);user-select:none;touch-action:none;transition:box-shadow .15s,transform .15s}
      .pill:hover{box-shadow:0 8px 26px rgba(16,24,40,.22),0 1px 3px rgba(16,24,40,.1)}
      .pill:focus-visible,.x:focus-visible,.btn:focus-visible,.lnk:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
      .m{width:26px;height:26px;border-radius:50%;background:var(--brand);display:grid;place-items:center;flex:none}
      .m svg{width:15px;height:15px;fill:#fff}
      .pill b{font-weight:650;white-space:nowrap}
      .n{min-width:20px;height:20px;padding:0 6px;border-radius:10px;font:700 11.5px/20px system-ui,sans-serif;text-align:center;color:#fff}
      .wrap.idle .pill,.wrap.off .pill,.wrap.dot .pill{padding:0 6px;width:38px;justify-content:center}
      .wrap.idle .pill b,.wrap.idle .n,.wrap.off .pill b,.wrap.dot .pill b{display:none}
      .wrap.dot .n{position:absolute;top:-5px;right:-5px;min-width:18px;height:18px;line-height:18px;font-size:10.5px;padding:0 5px;box-shadow:0 0 0 2px var(--bg)}
      .wrap.idle .m{background:#8090a3}
      .wrap.clean .m{background:var(--grn)} .wrap.clean b{color:var(--grn)}
      .wrap.review .m{background:var(--amb)} .wrap.review b{color:var(--amb)} .wrap.review .n{background:var(--amb)}
      .wrap.fix .m{background:var(--red)} .wrap.fix b{color:var(--red)} .wrap.fix .n{background:var(--red)}
      .wrap.checking .m svg{animation:spin 1.1s linear infinite}
      .wrap.shake .pill{animation:shake .5s ease 2}
      @keyframes spin{to{transform:rotate(1turn)}}
      @keyframes shake{0%,100%{transform:none}20%,60%{transform:translateX(-5px)}40%,80%{transform:translateX(5px)}}
      .card[hidden]{display:none}
      .card{position:absolute;right:0;bottom:48px;width:384px;max-width:calc(100vw - 24px);max-height:min(70vh,560px);display:flex;flex-direction:column;
        background:var(--bg);border:1px solid var(--line);border-radius:12px;box-shadow:0 16px 40px rgba(16,24,40,.22),0 2px 6px rgba(16,24,40,.08);overflow:hidden;
        transform-origin:bottom right;animation:in .14s ease-out}
      .wrap.down .card{bottom:auto;top:48px;transform-origin:top right}
      .wrap.left .card{right:auto;left:0}
      @keyframes in{from{opacity:0;transform:translateY(6px) scale(.98)}}
      .hd{display:flex;align-items:center;gap:10px;padding:12px 12px 10px 14px;border-bottom:1px solid var(--line)}
      .hd .ic{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--soft);color:var(--mut)}
      .hd .ic svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
      .wrap.clean .hd .ic{background:var(--grn-bg);color:var(--grn)} .wrap.review .hd .ic{background:var(--amb-bg);color:var(--amb)} .wrap.fix .hd .ic{background:var(--red-bg);color:var(--red)}
      .ttl{flex:1;min-width:0}.ttl b{display:block;font-size:14.5px}.ttl span{display:block;font-size:12px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .score{font:700 15px/1 ui-monospace,Menlo,Consolas,monospace;color:var(--fg2)}.score small{font-size:10px;color:var(--mut)}
      .tally{font-weight:700;font-size:12px;white-space:nowrap;padding:2px 8px;border-radius:10px;background:var(--soft);color:var(--fg2)}
      .wrap.fix .tally{background:var(--red-bg);color:var(--red)} .wrap.review .tally{background:var(--amb-bg);color:var(--amb)}
      .x{border:0;background:none;width:28px;height:28px;border-radius:6px;display:grid;place-items:center;color:var(--mut)}.x:hover{background:var(--soft)}
      .x svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round}
      .warn{margin:10px 12px 0;padding:9px 11px;border-radius:8px;background:var(--red-bg);color:var(--red);font-weight:600;font-size:12.5px}
      .list{overflow:auto;padding:4px 0}
      .it{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;padding:10px 14px;border-bottom:1px solid var(--line)}
      .it:last-child{border-bottom:0}
      .chip{grid-row:span 3;align-self:start;margin-top:1px;font:700 10px/18px system-ui,sans-serif;letter-spacing:.04em;text-transform:uppercase;padding:0 7px;border-radius:9px}
      .chip.error{background:var(--red-bg);color:var(--red)} .chip.warn{background:var(--amb-bg);color:var(--amb)} .chip.info{background:var(--soft);color:var(--fg2)}
      .msg{color:var(--fg)} .fix{color:var(--fg2);font-size:12px}
      .lnk{justify-self:start;border:0;background:none;padding:0;color:var(--brand);font-weight:600;font-size:12px}
      .lnk:hover{text-decoration:underline}
      .empty{padding:18px 16px;color:var(--fg2)}
      .empty b{display:block;color:var(--fg);margin-bottom:2px}
      .ft{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 12px;border-top:1px solid var(--line);background:var(--soft)}
      .btn{border:1px solid var(--line);background:var(--bg);border-radius:7px;padding:5px 10px;font-weight:600;font-size:12.5px}
      .btn:hover{border-color:var(--brand);color:var(--brand)}
      .sw{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--fg2);cursor:pointer}
      .sw input{accent-color:var(--brand);margin:0}
      .sp{flex:1}
      .kbd{font:11px ui-monospace,Menlo,Consolas,monospace;color:var(--mut)}
      .hide{border:0;background:none;color:var(--mut);font-size:12px;padding:0}.hide:hover{color:var(--fg);text-decoration:underline}
      .wrap .card[data-view=code] .hd b,.wrap .card[data-view=claim] .hd b,.wrap .pane b{color:var(--fg)}
      .wrap .card[data-view=code] .hd .ic,.wrap .card[data-view=claim] .hd .ic{background:var(--soft);color:var(--brand)}
      .tabs{display:flex;gap:4px;padding:8px 12px 0;border-bottom:1px solid var(--line)}
      .tabs button{border:0;background:none;padding:6px 10px 8px;font-weight:600;font-size:12.5px;color:var(--mut);border-bottom:2px solid transparent;margin-bottom:-1px}
      .tabs button[aria-selected="true"]{color:var(--brand);border-bottom-color:var(--brand)}
      .tabs button:focus-visible{outline:2px solid var(--brand);outline-offset:-2px;border-radius:4px}
      .pane{overflow:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px}
      .pane>*{flex-shrink:0}
      .lede{color:var(--fg2);font-size:12.5px;margin:0}
      .note2{color:var(--mut);font-size:11.5px;margin:0}
      .bad{color:var(--red);font-size:12.5px;margin:0}
      .ok2{color:var(--grn);font-size:12.5px;margin:0;font-weight:600}
      .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
      .btn.pri{background:var(--brand);border-color:var(--brand);color:#fff}.btn.pri:hover{color:#fff;filter:brightness(1.08)}
      .btn:disabled{opacity:.55;cursor:default}
      .cd{display:grid;grid-template-columns:auto 1fr auto;gap:2px 9px;padding:9px 0;border-top:1px solid var(--line)}
      .cd:first-child{border-top:0}
      .cd input{margin:3px 0 0;accent-color:var(--brand)}
      .cd .cc{font:700 13px ui-monospace,Menlo,Consolas,monospace}
      .cd .ty{font:700 9.5px/16px system-ui,sans-serif;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin-left:6px}
      .cd .pc{font:600 11.5px ui-monospace,Menlo,monospace;color:var(--mut)}
      .cd .ds{grid-column:2/4;color:var(--fg)}
      .cd .why{grid-column:2/4;color:var(--fg2);font-size:12px}
      .cd .gap{grid-column:2/4;color:var(--amb);font-size:12px}
      .fg{display:grid;grid-template-columns:1fr 1fr;gap:8px}
      .fg label,.ln label{display:grid;gap:3px;font-size:11px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}
      .fg label.w{grid-column:1/3}
      .fg input,.ln input,.fg select{font:13px system-ui,sans-serif;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;width:100%;text-transform:none;letter-spacing:0}
      .fg input:focus,.ln input:focus{outline:2px solid var(--brand);outline-offset:-1px}
      .ln{display:grid;grid-template-columns:1.3fr .7fr .6fr 1fr auto;gap:6px;align-items:end}
      .ln.dn{grid-template-columns:1.1fr .65fr .65fr .6fr .9fr auto}
      .cd .tt{font:600 11.5px ui-monospace,Menlo,monospace;color:var(--brand);margin-left:7px}
      .svc td.dim{color:var(--red)}
      .ln .x2{border:0;background:none;color:var(--mut);padding:6px 4px}
      .mono{font-family:ui-monospace,Menlo,Consolas,monospace}
      .sec{font-weight:700;font-size:12px;color:var(--fg2);margin:4px 0 -2px}
      .bl{border-left:3px solid var(--amb);padding:4px 0 4px 9px;font-size:12.5px}
      .bl.e{border-color:var(--red)}
      .bl b{display:block}
      .warnbox{background:var(--amb-bg);color:var(--amb);border-radius:8px;padding:8px 10px;font-size:12px}
      .info{background:var(--soft);border-radius:8px;padding:7px 10px;font-size:12px;color:var(--fg2);margin:0}
      .pv{border:1px solid var(--line);border-radius:10px}
      .pvh{border-radius:10px 10px 0 0}.svc tfoot td:first-child{border-bottom-left-radius:10px}.svc tfoot td:last-child{border-bottom-right-radius:10px}
      .pvh{display:flex;justify-content:space-between;align-items:center;gap:8px;background:var(--soft);padding:8px 11px;border-bottom:1px solid var(--line)}
      .pvh span{font:700 10.5px system-ui,sans-serif;letter-spacing:.05em;text-transform:uppercase;color:var(--mut)}
      .pvg{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;padding:10px 11px}
      .kv{display:grid;gap:1px;min-width:0}.kv span{font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;color:var(--mut)}
      .kv b{font-weight:600;overflow-wrap:anywhere}
      .wrap .pane .miss{color:var(--red);font-weight:600}
      .pvs{font:700 10.5px system-ui,sans-serif;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);padding:8px 11px 4px;border-top:1px solid var(--line)}
      .dxl{list-style:none;margin:0;padding:0 11px 8px;display:grid;gap:3px}
      .dxl .ltr{display:inline-block;width:18px;color:var(--mut);font-weight:700}
      .svc{width:100%;border-collapse:collapse;font-size:12.5px}
      .svc th{font-size:10.5px;color:var(--mut);text-align:left;font-weight:600;padding:3px 11px}
      .svc td{padding:5px 11px;border-top:1px solid var(--line);vertical-align:top}
      .svc td small{display:block;color:var(--mut);font-size:11px}
      .svc .r{text-align:right}
      .svc tfoot td{font-weight:700;background:var(--soft)}
      .pane > p.miss{margin:0 11px}
      .spin2{width:14px;height:14px;border-radius:50%;border:2px solid var(--line);border-top-color:var(--brand);animation:spin .8s linear infinite;display:inline-block;vertical-align:-2px;margin-right:6px}
      @media (prefers-reduced-motion:reduce){.card,.wrap.checking .m svg,.wrap.shake .pill,.spin2{animation:none}}
    </style>
    <div class="wrap idle" id="w">
      <div class="card" id="card" role="dialog" aria-label="Althais claim check" hidden></div>
      <button class="pill" id="pill" type="button" aria-expanded="false"><span class="m">${MARK}</span><b id="lbl">Althais</b><span class="n" id="n" hidden></span></button>
    </div>`;
    const $ = (id) => root.getElementById(id);
    const w = $("w"), pill = $("pill"), card = $("card");
    let state = "idle", stateText = "", last = null, open = false, warnN = 0, hidden = false, offHost = false, panelOpen = false;
    let mode = "check";                                                   // check | code | claim
    const cs = { busy: false, err: "", res: null, picked: {}, done: "", info: "", bound: "", label: "" };          // Code note
    const ks = { busy: false, err: "", form: null, out: null, preview: null, done: "", info: "", dirty: false, auto: false, key: "" };   // Create claim
    const call = (msg) => new Promise((res) => { try { chrome.runtime.sendMessage(msg, (r) => { void chrome.runtime.lastError; res(r || { ok: false, error: "The extension didn't answer. Reload the page." }); }); } catch (e) { res({ ok: false, error: "The extension was updated. Reload the page." }); } });
    const hostKey = "pos:" + location.host;

    // position (draggable, remembered per site)
    chrome.storage.local.get([hostKey, "offHosts"], (s) => {
      const p = s[hostKey];
      if (p) { host.style.right = p.r + "px"; host.style.bottom = p.b + "px"; }
      offHost = (s.offHosts || []).includes(location.host);
      orient(); render();
    });
    function orient() {
      const r = host.getBoundingClientRect();
      w.classList.toggle("down", r.top < 340);
      w.classList.toggle("left", r.left < 360 && r.right < innerWidth / 2);
    }
    let drag = null;
    pill.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, r: parseFloat(host.style.right), b: parseFloat(host.style.bottom), moved: false };
      pill.setPointerCapture(e.pointerId);
    });
    pill.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 5) return;
      drag.moved = true;
      const pr = pill.getBoundingClientRect();
      host.style.right = Math.min(Math.max(8, drag.r - dx), innerWidth - pr.width - 8) + "px";
      host.style.bottom = Math.min(Math.max(8, drag.b - dy), innerHeight - pr.height - 8) + "px";
      orient();
    });
    pill.addEventListener("pointerup", () => {
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      if (moved) { chrome.storage.local.set({ [hostKey]: { r: parseFloat(host.style.right), b: parseFloat(host.style.bottom) } }); return; }
      setOpen(!open);
    });
    pill.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } });
    root.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) { setOpen(false); pill.focus(); } });
    addEventListener("resize", orient);

    function setOpen(v) { open = v; if (!v) warnN = 0; pill.setAttribute("aria-expanded", String(v)); render(true); }

    const ICON = {
      clean: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
      review: '<svg viewBox="0 0 24 24"><path d="M12 8v5M12 16.5v.5"/></svg>',
      fix: '<svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7L7 17"/></svg>',
      idle: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/></svg>',
    };
    const TITLE = { clean: "Ready to submit", review: "Worth a look", fix: "Fix before submitting" };
    const CLOSE = '<button class="x" id="close" type="button" aria-label="Minimize"><svg viewBox="0 0 24 24"><path d="M6 12h12"/></svg></button>';

    function render(force) {
      if (offHost || hidden) { host.style.display = "none"; return; }
      host.style.display = "";
      const v = last && last.verdict;
      const cls = state === "result" && v ? v : state === "checking" ? (v || "idle") + " checking" : state === "unpaired" || state === "error" ? "review" : "idle";
      w.className = "wrap " + cls + (w.classList.contains("down") ? " down" : "") + (w.classList.contains("left") ? " left" : "") + (warnN ? " shake" : "") + (panelOpen && !open ? " dot" : "");
      const issues = last ? last.flags.filter((x) => x.severity === "error" || x.severity === "warn").length : 0;
      $("lbl").textContent = state === "unpaired" ? "Connect Althais" : state === "error" ? "Althais can't check" : v === "clean" ? "Ready to submit" : v === "review" ? "Worth a look" : v === "fix" ? "Fix first" : "Althais";
      $("n").hidden = !(v && v !== "clean" && issues && state !== "unpaired");
      $("n").textContent = issues;
      pill.title = state === "idle" ? "Althais: open a claim and it's checked automatically" : "Althais claim check (Alt+Shift+A)";
      pill.setAttribute("aria-label", "Althais: " + $("lbl").textContent + (issues && v !== "clean" ? ", " + issues + " issues" : ""));
      card.hidden = !open;
      if (!open) return;
      if (mode !== "check" && !force && card.dataset.view === mode) return;     // never wipe what the biller is typing
      card.dataset.view = mode;
      if (mode === "code" && state !== "unpaired") return renderCode();
      if (mode === "claim" && state !== "unpaired") return renderClaim();
      let h = "";
      if (state === "unpaired") {
        h = head("idle", "Connect this browser", "Althais isn't connected yet") +
          '<div class="empty"><b>One step to finish setup</b>Open Althais, go to Revenue › Connect EMR › Browser Extension, and press Connect This Browser.</div>';
      } else if (state === "error" && !v) {
        h = head("review", "Couldn't check this claim", stateText || "Try again in a moment") + '<div class="empty">' + esc(stateText) + "</div>";
      } else if (!v) {
        h = head("idle", "No claim on this screen", location.host) + TABS() +
          '<div class="empty"><b>Open a claim</b>Althais reads the codes, payer, date and charge on claim screens and checks them automatically.</div>';
      } else {
        const f = last.fields || {};
        const meta = [f.claim_id && "Claim " + f.claim_id, f.payer, f.dos].filter(Boolean).join(" · ") || location.host;
        const errs = last.flags.filter((x) => x.severity === "error").length, warns = last.flags.filter((x) => x.severity === "warn").length;
        h = head(v, TITLE[v], meta, v === "clean" ? last.score : null, errs ? errs + " to fix" : warns ? warns + " to check" : "") + TABS();
        if (warnN) h += '<div class="warn" role="alert">This claim still has ' + warnN + " problem" + (warnN === 1 ? "" : "s") + " to fix before it goes out.</div>";
        if (!last.flags.length) h += '<div class="empty"><b>No problems found</b>This is a pre-submission check, not a guarantee of payment.</div>';
        else {
          const order = { error: 0, warn: 1, info: 2 };
          const items = last.flags.map((x, i) => ({ x, i })).sort((a, b) => order[S.sev(a.x.severity)] - order[S.sev(b.x.severity)]);
          h += '<div class="list">' + items.map(({ x, i }) => {
            const s = S.sev(x.severity), fix = S.FIX[x.code], kind = S.KIND_OF[x.code];
            return '<div class="it"><span class="chip ' + s + '">' + { error: "Fix", warn: "Check", info: "Note" }[s] + '</span><div class="msg">' + esc(x.message) + "</div>" +
              (fix ? '<div class="fix">' + esc(fix) + "</div>" : "<div></div>") +
              (kind && outlines ? '<button class="lnk" type="button" data-focus="' + i + '">Show on page</button>' : "<div></div>") + "</div>";
          }).join("") + "</div>";
        }
      }
      h += '<div class="ft"><button class="btn" id="re" type="button">Re-check</button><label class="sw"><input type="checkbox" id="ol"' + (outlines ? " checked" : "") +
        '> Outline fields</label><span class="sp"></span><button class="hide" id="off" type="button">Hide on this site</button></div>';
      card.innerHTML = h;
      root.getElementById("close").onclick = () => { setOpen(false); pill.focus(); };
      root.getElementById("re").onclick = () => { state = "checking"; render(); send({ t: "recheck" }); };
      root.getElementById("ol").onchange = (e) => chrome.storage.local.set({ outlines: e.target.checked });
      root.getElementById("off").onclick = () => {
        chrome.storage.local.get(["offHosts"], (s) => {
          const list = (s.offHosts || []).filter((x) => x !== location.host).concat(location.host);
          chrome.storage.local.set({ offHosts: list });
        });
      };
      card.querySelectorAll("[data-focus]").forEach((b) => (b.onclick = () => send({ t: "focus", i: +b.dataset.focus })));
      wireTabs();
    }

    // ---------- tabs ----------
    function TABS() {
      return '<div class="tabs" role="tablist">' + [["check", "Check"], ["code", "Code note"], ["claim", "Create claim"]].map(([k, l]) =>
        '<button type="button" role="tab" data-mode="' + k + '" aria-selected="' + (mode === k) + '">' + l + "</button>").join("") + "</div>";
    }
    function wireTabs() {
      card.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => {
        mode = b.dataset.mode;
        if (mode === "claim" && ks.auto && !ks.form && !ks.busy) { const i = ks.pendingInfo; ks.pendingInfo = ""; startClaim(i); return; }   // pick up the claim now on screen
        render(true);
      }));
    }
    function frame(title, meta, body, kind) {
      card.innerHTML = head(kind || "idle", title, meta) + TABS() + '<div class="pane">' + body + "</div>";
      root.getElementById("close").onclick = () => { setOpen(false); pill.focus(); };
      wireTabs();
    }

    // ---------- Phase 1: code the visit note ----------
    function renderCode() {
      let b = "";
      if (cs.busy) b = '<p class="lede"><span class="spin2"></span>Reading the note and suggesting codes…</p>';
      else if (!cs.res) {
        b = (cs.info ? '<p class="info" role="status">' + esc(cs.info) + "</p>" : "") +
          '<p class="lede">Open the visit note (or select its text) and Althais suggests CPT and ICD-10 codes, each with the line from the note that supports it.</p>' +
          (cs.err ? '<p class="bad" role="alert">' + esc(cs.err) + "</p>" : "") +
          '<div class="row"><button class="btn pri" id="cn" type="button">Code this note</button></div>' +
          '<p class="note2">This sends the note text to Althais. Use synthetic notes until your practice has a signed agreement with Althais. A person must review every code.</p>';
      } else {
        const codes = cs.res.codes;
        if (!codes.length) b = '<p class="lede">No codes came back for this note. Check that the whole note is on screen, or select it and try again.</p><div class="row"><button class="btn" id="cn" type="button">Try again</button></div>';
        else {
          b = '<p class="note2">From the ' + (cs.res.how === "selection" ? "text you selected" : "note on screen") + ". Untick anything you don't want.</p><div>" + codes.map((c, i) => {
            const on = cs.picked[i] !== false;
            return '<label class="cd"><input type="checkbox" data-pick="' + i + '"' + (on ? " checked" : "") + '><span><span class="cc">' + esc(c.code) + "</span>" + (c.modifier ? '<span class="cc">-' + esc(c.modifier) + "</span>" : "") +
              (c.tooth || c.quad ? '<span class="tt">' + esc(c.tooth ? "#" + c.tooth + (c.surface ? " " + c.surface : "") : c.quad) + "</span>" : "") +
              '<span class="ty">' + esc(c.type === "CPT" || c.type === "CDT" ? c.type : "ICD-10") + '</span></span><span class="pc">' + (c.confidence != null ? Math.round(c.confidence) + "%" : "") + "</span>" +
              '<span class="ds">' + esc(c.description || "") + "</span>" + (c.justification ? '<span class="why">Because: ' + esc(c.justification) + "</span>" : "") +
              (c.documentation_gap ? '<span class="gap">Missing: ' + esc(c.documentation_gap) + "</span>" : "") + "</label>";
          }).join("") + "</div>" +
          (cs.done ? '<p class="ok2" role="status">' + esc(cs.done) + "</p>" : "") + (cs.err ? '<p class="bad" role="alert">' + esc(cs.err) + "</p>" : "") +
          '<div class="row"><button class="btn pri" id="cf" type="button">Fill into EMR</button><button class="btn" id="cu" type="button">Use in claim</button><button class="btn" id="cc" type="button">Copy</button><span class="sp"></span><button class="hide" id="cn" type="button">Code again</button></div>' +
          '<p class="note2">Fill into EMR types the picked codes into empty code fields on the claim screen. Check them and save in your EMR as usual.</p>';
        }
      }
      frame("Code this note", cs.res && cs.label ? "For claim " + cs.label : "Suggestions from the visit note", b);
      const go = root.getElementById("cn");
      if (go) go.onclick = async () => {
        cs.busy = true; cs.err = ""; cs.done = ""; render(true);
        const r = await call({ t: "codeNote" });
        cs.busy = false;
        if (r.ok) { cs.res = r.v; cs.picked = {}; cs.info = ""; cs.bound = claimOnScreen && last ? claimKey(last.fields) : ""; cs.label = cs.bound && last.fields ? last.fields.claim_id || "" : ""; (r.v.codes || []).forEach((c, i) => { if (c.confidence != null && c.confidence < 60) cs.picked[i] = false; }); } else cs.err = r.error;
        render(true);
      };
      card.querySelectorAll("[data-pick]").forEach((x) => (x.onchange = () => { cs.picked[x.dataset.pick] = x.checked; }));
      const picked = () => (cs.res ? cs.res.codes.filter((c, i) => cs.picked[i] !== false) : []);
      const isProc = (c) => c.type === "CPT" || c.type === "CDT";
      // dental procedures carry their tooth, surfaces and quadrant so Fill and Create claim keep them together
      const split = () => ({ cpt: picked().filter(isProc).map((c) => c.type === "CDT" ? { code: c.code, tooth: c.tooth || "", surface: c.surface || "", quad: c.quad || "" } : c.code),
        icd: picked().filter((c) => !isProc(c)).map((c) => c.code) });
      const label = (x) => typeof x === "string" ? x : x.code + (x.tooth ? " #" + x.tooth + (x.surface ? " " + x.surface : "") : x.quad ? " " + x.quad : "");
      const f = root.getElementById("cf");
      if (f) f.onclick = async () => {
        const p = split(); f.disabled = true;
        const r = await call({ t: "fill", cpt: p.cpt, icd: p.icd });
        if (!r.ok) cs.err = r.error;
        else {
          const v = r.v, parts = [];
          if (v.icd) parts.push(v.icd + " diagnosis" + (v.icd === 1 ? "" : "es"));
          if (v.cpt) parts.push(v.cpt + " procedure" + (v.cpt === 1 ? "" : "s"));
          cs.err = ""; cs.done = parts.length ? "Filled " + parts.join(" and ") + ". Check them, then save in your EMR." + (v.skipped ? " " + (v.skipped === 1 ? "1 code was" : v.skipped + " codes were") + " already on the claim." : "")
            : v.skipped ? "Those codes are already on the claim." : "";
          if (!parts.length && !v.skipped) cs.err = "Couldn't find empty code fields on this screen. Open the claim's charge screen, or use Copy.";
        }
        render(true);
      };
      const cp = root.getElementById("cc");
      if (cp) cp.onclick = async () => {
        const p = split(), t = "Procedures: " + (p.cpt.map(label).join(", ") || "none") + "\nICD-10: " + (p.icd.join(", ") || "none");
        try { await navigator.clipboard.writeText(t); cs.done = "Copied."; } catch (e) { cs.err = "Couldn't copy. Select the codes and copy them instead."; }
        render(true);
      };
      const u = root.getElementById("cu");
      if (u) u.onclick = () => { const p = split(); ks.form = null; ks.seed = p; mode = "claim"; startClaim(); };
    }

    // ---------- Phase 2: create a claim in Althais ----------
    const POS = { "11": "Office", "19": "Off-campus outpatient hospital", "20": "Urgent care facility", "21": "Inpatient hospital", "22": "On-campus outpatient hospital", "23": "Emergency room, hospital", "02": "Telehealth", "10": "Telehealth at home" };
    const blankLine = () => ({ code: "", mod: "", units: 1, charge: "" });
    async function startClaim(info) {
      Object.assign(ks, { busy: true, err: "", out: null, preview: null, done: "", dirty: false, info: info || "", auto: true });
      render(true);
      const r = await call({ t: "claimPrefill" });
      ks.busy = false;
      if (!r.ok) { ks.err = r.error; render(true); return; }
      const p = r.v.party || {}, f = r.v.fields || {}, seed = ks.seed || {};
      let lines = (f.lines && f.lines.length ? f.lines : (f.cpt || []).map((c) => ({ code: c, mod: "", units: 1, charge: "" })));
      (seed.cpt || []).forEach((c) => {
        const it = typeof c === "string" ? { code: c } : c;
        const same = (l) => l.code === it.code && (!it.tooth || String(l.tooth || "").toUpperCase() === String(it.tooth).toUpperCase());
        const blank = lines.find((l) => l.code === it.code && "tooth" in it && !l.tooth);
        if (blank) Object.assign(blank, { tooth: it.tooth || "", surface: it.surface || blank.surface || "", quad: it.quad || blank.quad || "" });
        else if (!lines.some(same)) lines.push(Object.assign({ code: it.code, mod: "", units: 1, charge: "" }, it.tooth != null ? { tooth: it.tooth, surface: it.surface || "", quad: it.quad || "" } : {}));
      });
      if (!lines.length) lines = [blankLine()];
      const icd = [...new Set([...(f.icd || []), ...(seed.icd || [])])];
      ks.form = { patient: p.patient || "", dob: p.dob || "", sex: p.sex || "", insuredId: p.insuredId || "", mrn: p.mrn || "", payer: f.payer || "", provider: p.provider || "",
        svcDate: f.dos || "", pos: f.pos || "", claimId: f.claim_id || "", icd: icd.join(", "), lines };
      ks.key = claimKey(f) || ks.key;
      ks.seed = null;
      render(true);
    }
    function readForm() {
      if (!ks.form || ks.preview) return;
      const v = (id) => { const el = root.getElementById(id); return el ? el.value.trim() : ""; };
      ["patient", "dob", "sex", "insuredId", "payer", "provider", "svcDate", "pos", "claimId", "icd"].forEach((k) => { ks.form[k] = v("k_" + k); });
      const val = (row, k) => { const el = row.querySelector('[data-f="' + k + '"]'); return el ? el.value.trim() : ""; };
      ks.form.lines = [...card.querySelectorAll(".ln[data-i]")].map((row) => {
        const l = { code: val(row, "code").toUpperCase(), mod: val(row, "mod").toUpperCase(), units: val(row, "units"), charge: val(row, "charge") };
        if (row.querySelector('[data-f="tooth"]')) Object.assign(l, { tooth: val(row, "tooth").toUpperCase(), surface: val(row, "surface").toUpperCase(), quad: val(row, "quad").toUpperCase() });
        return l;
      });
    }
    const toClaim = () => ({ claimId: ks.form.claimId, patient: ks.form.patient, dob: ks.form.dob, sex: ks.form.sex, insuredId: ks.form.insuredId, mrn: ks.form.mrn,
      payer: ks.form.payer, provider: ks.form.provider, svcDate: ks.form.svcDate, pos: ks.form.pos,
      cpts: ks.form.lines.filter((l) => l.code).map((l) => Object.assign({ code: l.code, modifier: l.mod || "", units: l.units || 1, charge: l.charge },
        isCdt(l.code) ? { tooth: l.tooth || "", surface: l.surface || "", quad: l.quad || "" } : {})),
      icds: ks.form.icd.split(/[\s,;]+/).filter(Boolean) });
    // CDT ranges that are billed per tooth (restorative, endo, implants, fixed prosthodontics, extractions, sealants)
    const needsTooth = (code) => { const m = /^D(\d{4})$/.exec(String(code || "").toUpperCase()); if (!m) return false; const n = +m[1];
      return (n >= 2000 && n <= 3999) || (n >= 6000 && n <= 6999) || (n >= 7111 && n <= 7250) || (n >= 1351 && n <= 1355); };
    const isCdt = (code) => /^D\d{4}$/.test(String(code || "").toUpperCase());
    const dentalForm = () => !!ks.form && ks.form.lines.some((l) => isCdt(l.code) || "tooth" in l);
    const usDate = (d) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || ""); return m ? m[2] + "/" + m[3] + "/" + m[1] : d; };
    const money = (n) => "$" + (Number(n) || 0).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    // CPT descriptions are licensed, so the preview only names procedures the coder described on this visit.
    const cptName = (code) => { const c = cs.res && cs.res.codes.find((x) => x.code === code && (x.type === "CPT" || x.type === "CDT")); return c ? c.description : ""; };

    // What the claim will say, laid out like the paper claim form, before anything is saved or approved.
    function previewHtml() {
      const P = ks.preview, c = P.out.claim || {}, o = P.out, F = ks.form, ok = !(o.problems || []).length, dent = !!o.dental;
      const kv = (k, v, miss) => '<div class="kv"><span>' + k + "</span><b" + (v ? "" : ' class="miss"') + ">" + esc(v || miss || "Missing") + "</b></div>";
      let b = '<div class="pv" aria-label="Claim preview"><div class="pvh"><span>' + (dent ? (P.action === "queue" ? "ADA dental claim to approve" : "ADA dental claim draft") : P.action === "queue" ? "Claim to approve" : "Draft claim") + '</span><b class="mono">' + esc(F.claimId || "New claim number") + "</b></div>" +
        '<div class="pvg">' + kv("Patient", c.patient) + kv("Date of birth", usDate(c.dob)) + kv("Sex", c.sex, "Not given") + kv("Payer", c.payer) + kv("Member ID", c.insuredId) +
        kv("Rendering provider", c.provider) + kv("Date of service", usDate(c.svcDate)) + kv("Place of service", c.pos ? c.pos + (POS[c.pos] ? " · " + POS[c.pos] : "") : "") + "</div>" +
        '<div class="pvs">Diagnoses' + (dent ? " (optional on dental claims)" : "") + '</div>' + ((c.icds || []).length ? '<ol class="dxl">' + c.icds.map((d, i) => '<li><span class="ltr">' + "ABCDEFGHIJKL"[i] + '</span><b class="mono">' + esc(d.code) + "</b> " + esc(d.description || "") + "</li>").join("") + "</ol>" : dent ? '<p class="note2" style="padding:0 11px 8px">None. Most dental plans don\'t need one.</p>' : '<p class="miss">No diagnoses</p>') +
        '<div class="pvs">' + (dent ? "Procedures" : "Services") + '</div><table class="svc"><thead><tr><th>' + (dent ? "CDT" : "CPT") + "</th>" + (dent ? "<th>Tooth</th><th>Surf</th>" : "<th>Mod</th>") + '<th>Units</th><th class="r">Charge</th></tr></thead><tbody>' +
        ((c.cpts || []).length ? c.cpts.map((x) => '<tr><td><b class="mono">' + esc(x.code) + "</b>" + (cptName(x.code) ? '<small>' + esc(cptName(x.code)) + "</small>" : "") + "</td>" +
          (dent ? '<td class="mono' + (needsTooth(x.code) && !x.tooth ? " dim" : "") + '">' + esc(x.tooth ? "#" + x.tooth : x.quad || "–") + '</td><td class="mono">' + esc(x.surface || "") + "</td>" : '<td class="mono">' + esc(x.modifier || "") + "</td>") +
          "<td>" + esc(x.units) + '</td><td class="r">' + money(x.charge * 1) + "</td></tr>").join("")
          : '<tr><td colspan="' + (dent ? 5 : 4) + '" class="miss">No services</td></tr>') +
        '</tbody><tfoot><tr><td colspan="' + (dent ? 4 : 3) + '">Total charge</td><td class="r">' + money(c.totalAmt) + "</td></tr></tfoot></table></div>";
      if ((o.problems || []).length) b += '<div class="sec">Fix before ' + (P.action === "queue" ? "approving" : "sending") + "</div>" + o.problems.map((p) => '<div class="bl e">' + esc(p) + "</div>").join("");
      if ((o.findings || []).length) b += '<div class="sec">Dental plans will look at</div>' + o.findings.map((f) => '<div class="bl' + (f.severity === "error" ? " e" : "") + '">' + esc(f.message) + "</div>").join("");
      if ((o.blockers || []).length) b += '<div class="sec">Althais will hold it until</div>' + o.blockers.map((x) => '<div class="bl"><b>' + esc(x.title) + "</b>" + esc(x.reason) + (x.next ? " · " + esc(x.next) : "") + "</div>").join("");
      if (ks.done) b += '<p class="ok2" role="status">' + esc(ks.done) + "</p>";
      if (ks.err) b += '<p class="bad" role="alert">' + esc(ks.err) + "</p>";
      if (ks.done) b += '<div class="row"><button class="btn" id="kedit" type="button">Back to the claim</button></div>';
      else if (P.action === "queue") {
        b += ok ? '<div class="warnbox">Approving sends this claim with Althais\'s end-of-day batch, after Althais checks it again.' + ((o.blockers || []).length ? " Until the items above are done, it waits in Althais." : "") + "</div>" +
            '<div class="row"><button class="btn pri" id="kgo" type="button">Approve for sending</button><button class="btn" id="kedit" type="button">Edit</button></div>'
          : '<div class="row"><button class="btn pri" id="kedit" type="button">Edit the claim</button></div>';
      } else b += '<div class="row"><button class="btn pri" id="kgo" type="button">Save draft</button><button class="btn" id="kedit" type="button">Edit</button></div><p class="note2">A draft is saved to Claims in Althais. Nothing is sent.</p>';
      return b;
    }
    function renderClaim() {
      let b = "";
      if (ks.busy) b = '<p class="lede"><span class="spin2"></span>Working…</p>';
      else if (!ks.form) {
        b = '<p class="lede">Builds a claim from this visit: codes and charges from the claim screen, patient and insurance details from the page. You review every field, then save it to Althais or approve it for Althais to send.</p>' +
          '<div class="warnbox">Only for clinics where Althais sends the claims. If your EMR sends claims itself (athenaOne, Tebra), submit there, or the payer gets the claim twice.</div>' +
          (ks.err ? '<p class="bad" role="alert">' + esc(ks.err) + "</p>" : "") +
          '<div class="row"><button class="btn pri" id="ks" type="button">Start claim from this screen</button></div>' +
          '<p class="note2">This reads the patient\'s name, date of birth and insurance ID from the page, because a claim can\'t be sent without them. After you start one, the form follows you as you open other claims. Use test patients until your practice has a signed agreement with Althais.</p>';
      } else if (ks.preview) b = previewHtml();
      else {
        const F = ks.form, inp = (k, label, cls, ph) => '<label class="' + (cls || "") + '">' + label + '<input id="k_' + k + '" value="' + esc(F[k]) + '"' + (ph ? ' placeholder="' + esc(ph) + '"' : "") + "></label>";
        b = (ks.info ? '<p class="info" role="status">' + esc(ks.info) + "</p>" : "") +
          '<div class="sec">Patient and insurance</div><div class="fg">' + inp("patient", "Patient", "w") + inp("dob", "Date of birth", "", "MM/DD/YYYY") + inp("sex", "Sex", "", "F or M") +
          inp("payer", "Payer", "w") + inp("insuredId", "Member ID", "", "") + inp("claimId", "Claim number", "", "Althais makes one") + "</div>" +
          '<div class="sec">Visit</div><div class="fg">' + inp("provider", "Rendering provider", "w") + inp("svcDate", "Date of service", "", "MM/DD/YYYY") + inp("pos", "Place of service", "", "e.g. 20") + "</div>" +
          '<div class="sec">' + (dentalForm() ? "Procedures" : "Charges") + '</div>' + F.lines.map((l, i) => dentalForm()
            ? '<div class="ln dn" data-i="' + i + '"><label>' + (i ? "" : "CDT") + '<input class="mono" data-f="code" value="' + esc(l.code) + '"></label><label>' + (i ? "" : "Tooth") +
              '<input class="mono" data-f="tooth" value="' + esc(l.tooth || "") + '" placeholder="#"></label><label>' + (i ? "" : "Surf") + '<input class="mono" data-f="surface" value="' + esc(l.surface || "") + '"></label><label>' + (i ? "" : "Quad") +
              '<input class="mono" data-f="quad" value="' + esc(l.quad || "") + '"></label><label>' + (i ? "" : "Fee $") + '<input data-f="charge" inputmode="decimal" value="' + esc(l.charge) + '"></label>' +
              '<input type="hidden" data-f="units" value="' + esc(l.units || 1) + '"><button class="x2" type="button" data-del="' + i + '" aria-label="Remove line">✕</button></div>'
            : '<div class="ln" data-i="' + i + '"><label>' + (i ? "" : "CPT") + '<input class="mono" data-f="code" value="' + esc(l.code) + '"></label><label>' + (i ? "" : "Mod") +
            '<input class="mono" data-f="mod" value="' + esc(l.mod) + '"></label><label>' + (i ? "" : "Units") + '<input data-f="units" inputmode="numeric" value="' + esc(l.units) + '"></label><label>' + (i ? "" : "Charge $") +
            '<input data-f="charge" inputmode="decimal" value="' + esc(l.charge) + '"></label><button class="x2" type="button" data-del="' + i + '" aria-label="Remove line">✕</button></div>').join("") +
          '<div class="row"><button class="hide" id="kadd" type="button">+ Add a line</button></div>' +
          '<div class="fg">' + inp("icd", dentalForm() ? "Diagnoses (ICD-10, optional for dental)" : "Diagnoses (ICD-10, in order)", "w", dentalForm() ? "Usually left empty" : "e.g. J02.9, R50.9") + "</div>" +
          (ks.err ? '<p class="bad" role="alert">' + esc(ks.err) + "</p>" : "") +
          '<div class="row"><button class="btn" id="kdraft" type="button">Preview and save draft</button><button class="btn pri" id="kq" type="button">Preview and approve</button><span class="sp"></span><button class="hide" id="kre" type="button">Reload from screen</button></div>' +
          '<p class="note2">You\'ll see the whole claim before anything is saved or approved. Approved claims go out with Althais\'s end-of-day batch, and you can follow them in Claim Tracker.</p>';
      }
      const title = ks.preview ? (ks.done ? (ks.preview.action === "queue" ? "Claim approved" : "Draft saved") : "Review the claim") : "Create claim";
      frame(title, ks.form && ks.form.claimId ? "Claim " + ks.form.claimId : "For clinics Althais bills for", b);
      const pane = card.querySelector(".pane");
      if (pane && ks.form && !ks.preview) pane.addEventListener("input", () => { ks.dirty = true; });
      const on = (id, fn) => { const el = root.getElementById(id); if (el) el.onclick = fn; };
      on("ks", () => startClaim());
      on("kre", () => startClaim());
      card.querySelectorAll("[data-del]").forEach((x) => (x.onclick = () => { readForm(); ks.form.lines.splice(+x.dataset.del, 1); if (!ks.form.lines.length) ks.form.lines.push(blankLine()); ks.dirty = true; render(true); }));
      on("kadd", () => { readForm(); ks.form.lines.push(blankLine()); render(true); });
      const preview = async (action) => {
        readForm(); ks.busy = true; ks.err = ""; ks.done = ""; ks.info = ""; render(true);
        const r = await call({ t: "claimAction", action: "check", claim: toClaim() });
        ks.busy = false;
        if (r.ok) ks.preview = { action, out: r.v }; else ks.err = r.error;
        render(true);
      };
      const commit = async () => {
        const action = ks.preview.action;
        ks.busy = true; ks.err = ""; render(true);
        const r = await call({ t: "claimAction", action, claim: toClaim() });
        ks.busy = false;
        if (r.ok) {
          ks.preview.out = r.v; ks.dirty = false;
          if (r.v.claim && r.v.claim.claimId) ks.form.claimId = r.v.claim.claimId;
          ks.done = action === "draft" ? r.v.message || "Saved to Althais." : r.v.status === "already_sent" ? "This claim was already sent." : r.v.message || "Approved.";
        } else { ks.err = r.error; if (r.data && (r.data.problems || r.data.blockers)) ks.preview.out = Object.assign({}, ks.preview.out, r.data); }
        render(true);
        const pn = card.querySelector(".pane"); if (pn) pn.scrollTop = pn.scrollHeight;
      };
      on("kdraft", () => preview("draft"));
      on("kq", () => preview("queue"));
      on("kgo", commit);
      on("kedit", () => { ks.preview = null; ks.done = ""; ks.err = ""; render(true); });
    }

    // A claim is "the same claim" while its number (or, without one, its date and payer) stays the same.
    const claimKey = (f) => (f ? f.claim_id || (f.dos ? f.dos + "|" + (f.payer || "") : "") : "");
    let seenKey = "", claimOnScreen = false;
    function claimChanged(k, f) {
      // Code suggestions belong to one visit: drop them once the biller moves to a different claim.
      if (cs.res) {
        if (cs.bound && cs.bound !== k) Object.assign(cs, { res: null, picked: {}, done: "", err: "", bound: "", label: "", info: "Cleared the codes from the last claim's note." });
      }
      // The claim form follows the screen once the biller has started one.
      if (ks.auto && ks.key !== k && !ks.busy) {
        const was = ks.form && ks.form.claimId, lost = ks.dirty && !ks.done;
        const info = "Now showing claim " + (f.claim_id || "on this screen") + "." + (lost && was ? " Changes you hadn't saved on claim " + was + " were dropped." : "");
        if (mode === "claim" && open) { ks.key = k; startClaim(info); }
        else Object.assign(ks, { form: null, preview: null, out: null, done: "", err: "", dirty: false, key: "", pendingInfo: info });
      }
      if (mode !== "check" && open) render(true);
    }
    function head(kind, title, meta, score, tally) {
      return '<div class="hd"><span class="ic">' + (ICON[kind] || ICON.idle) + '</span><div class="ttl"><b>' + esc(title) + "</b><span>" + esc(meta) + "</span></div>" +
        (tally ? '<span class="tally">' + esc(tally) + "</span>" : score != null ? '<span class="score">' + Math.round(score) + "<small>/100</small></span>" : "") + CLOSE + "</div>";
    }

    chrome.storage.onChanged.addListener((ch) => {
      if (ch.offHosts) { offHost = (ch.offHosts.newValue || []).includes(location.host); send({ t: "recheck" }); render(); }
    });

    const attach = () => { if (!host.isConnected) (document.body || document.documentElement).appendChild(host); };
    attach();
    setInterval(attach, 2000);              // single-page EMRs sometimes replace <body>
    // pick up a result the worker already has for this tab (e.g. after the page reloads)
    try { chrome.runtime.sendMessage({ t: "current" }, (r) => { void chrome.runtime.lastError; if (!r) return; panelOpen = !!r.panelOpen; if (r.result) { last = Object.assign({ fields: r.out && r.out.fields }, r.result); state = "result"; } render(); }); } catch (e) {}

    // If the extension is updated or removed, this copy stops working: take its badge and outlines off the page.
    const watchdog = setInterval(() => { if (!alive()) { clearInterval(watchdog); window.__althaisTeardown && window.__althaisTeardown(); } }, 1500);

    return {
      render,
      result(m) {
        if (!m.verdict) { last = null; state = "idle"; warnN = 0; claimOnScreen = false; render(); return; }
        claimOnScreen = true;
        const k = claimKey(m.fields);
        if (k && k !== seenKey) { const first = !seenKey; seenKey = k; if (!first) claimChanged(k, m.fields || {}); }
        if (k && cs.res && !cs.bound) { cs.bound = k; cs.label = (m.fields || {}).claim_id || ""; if (mode === "code" && open) render(true); }   // codes made on a note screen belong to the next claim opened
        const changedClaim = !last || JSON.stringify(last.fields) !== JSON.stringify(m.fields);
        last = m; state = "result";
        if (changedClaim) warnN = 0;
        if (!m.quiet && changedClaim && m.verdict === "fix" && !open) { w.classList.add("shake"); setTimeout(() => w.classList.remove("shake"), 1100); }
        render();
      },
      state(s, text) { state = s; stateText = text || ""; render(); },
      submitWarn(n) { warnN = Math.max(1, n || 1); hidden = false; mode = "check"; setOpen(true); setTimeout(() => w.classList.remove("shake"), 1100); },
      toggle() { hidden = !hidden; render(); },
      panel(v) { panelOpen = !!v; render(); },
      remove() { host.remove(); },
    };
  }
})();
