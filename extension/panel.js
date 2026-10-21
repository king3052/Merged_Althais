const $ = (id) => document.getElementById(id);
const S = self.AlthaisShared;
const DEFAULT_SERVER = S.DEFAULT_SERVER;
let cfg = { server: DEFAULT_SERVER, token: "" };

const store = {
  get: () => new Promise((r) => chrome.storage.local.get(["server", "token"], r)),
  set: (o) => new Promise((r) => chrome.storage.local.set(o, r)),
};
const say = (id, text, err) => { const el = $(id); el.textContent = text || ""; el.className = "msg" + (err ? " err" : ""); };
const base = () => (cfg.server || DEFAULT_SERVER).replace(/\/+$/, "");

async function api(path, opts) {
  let r;
  try {
    r = await fetch(base() + path, Object.assign({ headers: { Authorization: "Bearer " + cfg.token, "Content-Type": "application/json" } }, opts));
  } catch (e) { throw new Error("Couldn't reach Althais. Check the address in settings and your connection."); }
  let j = {};
  try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(j.error || "Althais returned an error (" + r.status + ").");
  return j;
}

async function init() {
  const s = await store.get();
  const a = await new Promise((r) => chrome.storage.local.get(["auto"], r));
  auto.on = a.auto !== false; setAutoUi();
  cfg = { server: s.server || DEFAULT_SERVER, token: s.token || "" };
  $("server").value = cfg.server; $("token").value = cfg.token;
  const o = await new Promise((r) => chrome.storage.local.get(["overlay", "offHosts"], r));
  overlayOn = o.overlay !== false; $("overlay").checked = overlayOn; showOffSites(o.offHosts || []);
  if (overlayOn) pullCurrent();
  if (!cfg.token) $("settings").hidden = false;
  status();
  soon(300);
}

$("gear").onclick = () => { $("settings").hidden = !$("settings").hidden; };
$("save").onclick = async () => {
  cfg = { server: $("server").value.trim() || DEFAULT_SERVER, token: $("token").value.trim() };
  await store.set(cfg); say("setmsg", "Saved."); status();
};
$("test").onclick = async () => {
  cfg = { server: $("server").value.trim() || DEFAULT_SERVER, token: $("token").value.trim() };
  try { await api("/api/extension/ping"); await store.set(cfg); say("setmsg", "Connected. Token works."); status(); $("settings").hidden = true; }
  catch (e) { say("setmsg", e.message, true); }
};

function fill(fields) {
  const f = $("form");
  f.claim_id.value = fields.claim_id || ""; f.dos.value = fields.dos || ""; f.payer.value = fields.payer || "";
  f.cpt.value = (fields.cpt || []).join(", "); f.icd.value = (fields.icd || []).join(", "); f.modifiers.value = (fields.modifiers || []).join(", ");
  f.amount.value = fields.amount || "";
}

// Reads every frame of the tab (athenaOne and other EMRs put claim screens inside frames), then merges what each frame found.
async function scan(diag, quiet) {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) throw new Error("Open your EMR claim page in this tab first.");
  let frames = [];
  try { frames = await chrome.webNavigation.getAllFrames({ tabId: tab.id }) || []; } catch (e) {}
  const origins = [...new Set([tab.url, ...frames.map((f) => f.url)].filter((u) => /^(https:|http:\/\/localhost)/.test(u || "")).map((u) => new URL(u).origin + "/*"))];
  if (!origins.length) throw new Error("This page isn't on a secure (https) address, so it can't be read.");
  const has = await chrome.permissions.contains({ origins });
  if (!has && quiet) { const e = new Error("needs-permission"); e.quiet = true; throw e; }
  if (!has && !(await chrome.permissions.request({ origins }))) throw new Error("Chrome needs your OK to read this site. Press the button again and choose Allow.");
  const target = { tabId: tab.id, allFrames: true };
  scan.tabUrl = tab.url;
  await chrome.scripting.executeScript({ target, func: (d) => { globalThis.__althaisDiag = d; }, args: [!!diag] });
  const res = await chrome.scripting.executeScript({ target, files: ["parse.js"] });
  return (res || []).map((r) => r.result).filter(Boolean);
}

function merge(outs) {
  const f = { claim_id: "", dos: "", payer: "", pos: "", cpt: [], icd: [], modifiers: [], amount: "" };
  const found = {}, guessed = new Set(), hosts = new Set();
  // Frames with labelled fields beat frames that only guessed from page text.
  outs.sort((a, b) => a.guessed.length - b.guessed.length);
  outs.forEach((o) => {
    hosts.add(o.host);
    for (const k of ["claim_id", "dos", "payer", "pos", "amount"]) if (!f[k] && o.fields[k]) f[k] = o.fields[k];
    for (const k of ["cpt", "icd", "modifiers"]) if (!f[k].length || !o.guessed.includes(k)) o.fields[k].forEach((x) => { if (!f[k].includes(x)) f[k].push(x); });
    o.guessed.forEach((k) => guessed.add(k));
    Object.keys(o.found).forEach((k) => { if (o.found[k]) found[k] = true; });
  });
  return { fields: f, found, guessed: [...guessed], host: [...hosts].filter(Boolean).join(", "), frames: outs.length };
}

function readMessage(out, auto) {
  const got = Object.keys(out.found).filter((k) => out.found[k]).length;
  let m = got ? (auto ? "Read automatically: " : "Read ") + got + " field" + (got > 1 ? "s" : "") + " from " + out.host + (out.frames > 1 ? " (" + out.frames + " frames)" : "") + "." : "Nothing recognised on this page. Type the codes in below, or create a page report below.";
  if (out.guessed.length) m += " " + out.guessed.join(" and ").toUpperCase() + " were guessed from the page text, so double-check them.";
  say("readmsg", m, !got);
}

$("read").onclick = async () => {
  say("readmsg", "");
  try {
    const outs = await scan(false);
    if (!outs.length) throw new Error("Couldn't read this page. Type the codes in below.");
    const out = merge(outs);
    fill(out.fields); auto.fp = fingerprint(out.fields); auto.dirty = false;
    readMessage(out, false);
    if (auto.on && cfg.token && (out.fields.cpt.length || out.fields.icd.length)) runCheck();
  } catch (e) { say("readmsg", e.message, true); $("edit").open = true; }
};

// ---- Automatic mode. Runs only while this panel is open, and only on sites the person has already allowed once.
// It re-reads when the tab changes, a frame finishes loading, or the page changes in place, and checks the claim when it changes.
const auto = { on: true, fp: "", dirty: false, busy: false, timer: 0, lastTry: 0 };
const fingerprint = (f) => JSON.stringify([f.claim_id, f.dos, f.payer, f.cpt, f.icd, f.modifiers, f.amount]);
function setAutoUi() {
  $("auto").checked = auto.on;
  $("read").classList.toggle("pri", !auto.on);           // reading happens by itself, so the button steps back
  $("autohint").textContent = auto.on ? "checks each claim as you open it" : "off, press Read this page";
}
function soon(ms) { clearTimeout(auto.timer); auto.timer = setTimeout(autoScan, ms || 500); }
async function autoScan() {
  if (!auto.on || auto.busy || document.hidden || overlayOn) return;          // with the overlay on, the background worker does the reading
  auto.busy = true;
  try {
    const outs = await scan(false, true);
    let own = ""; try { own = new URL(base()).host; } catch (e) {}
    const tu = scan.tabUrl ? new URL(scan.tabUrl) : null;
    if (own && tu && tu.host === own && !tu.pathname.startsWith("/static/mock-emr/")) return;   // never auto-read Althais's own app pages (the test screens are fine)
    if (!outs.length) return;
    const out = merge(outs);
    if (!out.fields.cpt.length && !out.fields.icd.length) return;                    // not a claim screen
    const fp = fingerprint(out.fields);
    if (fp === auto.fp) return;
    const f = $("form");
    if (auto.dirty && f.claim_id.value && f.claim_id.value === out.fields.claim_id) return;   // don't overwrite the person's edits on the same claim
    fill(out.fields); auto.fp = fp; auto.dirty = false;
    readMessage(out, true);
    if (cfg.token) await runCheck(); else reset();
  } catch (e) {
    if (e.quiet && Date.now() - auto.lastTry > 1500) say("readmsg", "Press Read this page once to allow Althais on this site. After that it reads automatically.");
    auto.lastTry = Date.now();
  } finally { auto.busy = false; }
}
async function activeTabId() { const [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true }); return t && t.id; }
chrome.tabs.onActivated.addListener(() => { auto.fp = ""; soon(300); });
chrome.tabs.onUpdated.addListener(async (id, ch) => { if (ch.status === "complete" && id === await activeTabId()) soon(400); });
for (const ev of ["onCompleted", "onHistoryStateUpdated", "onReferenceFragmentUpdated"])
  if (chrome.webNavigation && chrome.webNavigation[ev]) chrome.webNavigation[ev].addListener(async (d) => { if (d.tabId === await activeTabId()) soon(600); });
setInterval(() => { if (auto.on) autoScan(); }, 4000);                              // catches screens that change without navigating
document.addEventListener("visibilitychange", () => { if (!document.hidden) soon(200); });
$("form").addEventListener("input", () => { auto.dirty = true; });
$("auto").onchange = async () => { auto.on = $("auto").checked; await chrome.storage.local.set({ auto: auto.on }); setAutoUi(); if (auto.on) { auto.fp = ""; soon(100); } };

// ---- On-page overlay: the background worker reads and checks the claim; the panel mirrors its result.
let overlayOn = true;
function takeResult(r) {
  if (!r || !r.result || !auto.on) return;
  const fields = r.out && r.out.fields;
  if (fields) {
    const f = $("form");
    if (auto.dirty && f.claim_id.value && f.claim_id.value === fields.claim_id) return;   // keep the person's edits on the same claim
    fill(fields); auto.fp = fingerprint(fields); auto.dirty = false;
    if (r.out.found) readMessage(r.out, true);
  }
  showResult(Object.assign({}, r.result));
}
async function pullCurrent() {
  const id = await activeTabId();
  if (id == null) return;
  chrome.runtime.sendMessage({ t: "current", tabId: id }, (r) => { void chrome.runtime.lastError; takeResult(r); });
}
chrome.runtime.onMessage.addListener(async (m) => {
  if (m.t !== "result" || !overlayOn) return;
  if (m.tabId === await activeTabId()) takeResult(m);
});
chrome.tabs.onActivated.addListener(() => { if (overlayOn) setTimeout(pullCurrent, 400); });
$("overlay").onchange = async () => { overlayOn = $("overlay").checked; await chrome.storage.local.set({ overlay: overlayOn }); if (!overlayOn) { auto.fp = ""; soon(100); } else pullCurrent(); };
function showOffSites(list) {
  const box = $("offsites");
  box.hidden = !list.length;
  box.innerHTML = list.length ? "Hidden on: " + list.map((x) => '<span class="tag">' + esc(x) + '</span> <button type="button" class="link" data-show="' + esc(x) + '">Show again</button>').join(" ") : "";
}
$("offsites").addEventListener("click", (e) => {
  const b = e.target.closest("[data-show]"); if (!b) return;
  chrome.storage.local.get(["offHosts"], (s) => chrome.storage.local.set({ offHosts: (s.offHosts || []).filter((x) => x !== b.dataset.show) }));
});
chrome.storage.onChanged.addListener((ch) => {
  if (ch.offHosts) showOffSites(ch.offHosts.newValue || []);
  if (ch.token || ch.server) {
    cfg = { server: (ch.server ? ch.server.newValue : cfg.server) || DEFAULT_SERVER, token: (ch.token ? ch.token.newValue : cfg.token) || "" };
    $("server").value = cfg.server; $("token").value = cfg.token;
    if (cfg.token) { $("settings").hidden = true; say("setmsg", ""); }
    status();
  }
});

$("mkdiag").onclick = async () => {
  try {
    const outs = await scan(true);
    const rep = { tool: "althais-extension 0.2", when: new Date().toISOString(), frames: outs.map((o) => ({ matched: o.found, guessed: o.guessed, layout: o.diag })) };
    $("diagout").value = JSON.stringify(rep, null, 1); $("diagout").hidden = false; $("copydiag").hidden = false;
  } catch (e) { say("readmsg", e.message, true); }
};
$("copydiag").onclick = async () => { $("diagout").select(); try { await navigator.clipboard.writeText($("diagout").value); } catch (e) { document.execCommand("copy"); } $("copydiag").textContent = "Copied"; };

// ---- Results: verification score up top, then the codes, then errors / warnings / info.
const LABEL = { clean: "Ready to submit", review: "Worth a look", fix: "Fix before submitting" };
const SEVS = [["error", "Errors"], ["warn", "Warnings"], ["info", "Info"]];
const sevOf = (x) => (x === "error" || x === "warn" ? x : "info");
const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
let last = null, pick = "";

function setVerify(state, pct, label, meta, flags) {
  const v = $("verify");
  v.className = "card verify " + state;
  // A claim with errors can't go out, so lead with how many things need fixing rather than a percentage.
  const errs = flags ? flags.filter((x) => x.severity === "error").length : 0, warns = flags ? flags.filter((x) => x.severity === "warn").length : 0;
  const count = pct != null && (errs || warns);
  $("pct").parentElement.classList.toggle("count", !!count);
  $("pct").textContent = pct == null ? "–" : count ? (errs || warns) : pct;
  $("pctsign").textContent = pct == null ? "" : count ? (errs ? "to fix" : "to check") : "%";
  $("vlabel").textContent = label;
  $("vmeta").textContent = meta;
  $("rbar").setAttribute("stroke-dasharray", (pct || 0) + " 100");
}
function setCounts(flags) {
  for (const [s] of SEVS) {
    const n = flags ? flags.filter((x) => sevOf(x.severity) === s).length : 0, el = document.querySelector('.stat[data-jump="' + s + '"]');
    $("n-" + s).textContent = n; el.classList.toggle("on", n > 0); el.disabled = !n;
  }
}
function claimMeta() {
  const f = $("form");
  return [f.claim_id.value && "Claim " + f.claim_id.value, f.payer.value, f.dos.value].filter(Boolean).join(" · ") || "Claim from this page";
}
function reset() {
  last = null; pick = ""; $("result").innerHTML = ""; setCounts(null);
  $("recheck").disabled = true; $("copy").disabled = true;
  setVerify("idle", null, "No claim checked yet", cfg.token ? "Open a claim in your EMR to start." : "Connect in settings to check claims.");
}

// Older Althais servers don't send per-code rows, so build them from the form and the flag text.
function codeRows(j) {
  if (Array.isArray(j.codes)) return j.codes;
  const f = $("form"), rank = { error: 3, warn: 2, info: 1 }, rows = [];
  [["CPT", list(f.cpt.value)], ["ICD-10", list(f.icd.value)]].forEach(([type, codes]) => codes.forEach((code) => {
    const hits = j.flags.filter((x) => (x.codes || []).includes(code) || x.message.toUpperCase().includes(code.toUpperCase()));
    const status = hits.reduce((w, x) => (rank[sevOf(x.severity)] > (rank[w] || 0) ? sevOf(x.severity) : w), "ok");
    rows.push({ code, type, description: "", status, issues: hits.length });
  }));
  return rows;
}
const mentions = (x, code) => (x.codes || []).includes(code) || (!x.codes && x.message.toUpperCase().includes(code.toUpperCase()));

function showResult(j) {
  const v = LABEL[j.verdict] ? j.verdict : "review", score = Math.max(0, Math.min(100, Math.round(Number(j.score) || 0)));
  j.flags = j.flags || []; j.rows = codeRows(j); last = j;
  if (pick && !j.rows.some((r) => r.code === pick)) pick = "";
  setVerify(v, score, LABEL[v], claimMeta(), j.flags);
  if ($("readmsg").classList.contains("err")) say("readmsg", "");
  setCounts(j.flags);
  $("recheck").disabled = false; $("copy").disabled = false; $("copy").title = "Copy results";
  render();
}

function render() {
  const j = last, f = $("form");
  if (!j) return;
  let h = "", codesHtml = "";
  if (j.rows.length) {
    let h = '<section class="card codes"><h3>Codes<span class="count">' + j.rows.length + "</span></h3>";
    for (const type of ["CPT", "ICD-10"]) {
      const rows = j.rows.filter((r) => r.type === type);
      if (!rows.length) continue;
      h += '<div class="grp"><div class="grp-h">' + (type === "CPT" ? "Procedures (CPT / HCPCS)" : "Diagnoses (ICD-10)") + "</div>";
      h += rows.map((r) => {
        const st = r.status === "ok" ? "ok" : sevOf(r.status);
        const word = st === "ok" ? "OK" : r.issues + (r.issues === 1 ? " issue" : " issues");
        return '<button type="button" class="code ' + st + (pick === r.code ? " sel" : "") + '" data-code="' + esc(r.code) + '" title="Show issues for ' + esc(r.code) + '">' +
          '<span class="sd"></span><span class="cv">' + esc(r.code) + '</span><span class="cd">' + esc(r.description || "") + '</span><span class="cs">' + word + "</span></button>";
      }).join("") + "</div>";
    }
    const mods = list(f.modifiers.value);
    if (mods.length) h += '<div class="mods">Modifiers ' + mods.map((m) => '<span class="tag">' + esc(m) + "</span>").join("") + "</div>";
    h += "</section>";
    codesHtml = h;
  }
  const shown = pick ? j.flags.filter((x) => mentions(x, pick)) : j.flags;
  if (pick) h += '<div class="filter">Showing issues for <b class="mono">' + esc(pick) + '</b><button type="button" id="clearpick">Show all</button></div>';
  if (!j.flags.length) {
    h += '<section class="card allclear"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.8"/></svg><div>No errors or warnings<span>This is a pre-submission check, not a guarantee of payment.</span></div></section>';
  } else if (pick && !shown.length) {
    h += '<section class="card allclear"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.8"/></svg><div>No issues name ' + esc(pick) + "</div></section>";
  }
  for (const [s, title] of SEVS) {
    const items = shown.filter((x) => sevOf(x.severity) === s);
    if (!items.length) continue;
    h += '<section class="card issues ' + s + '" id="g-' + s + '"><h3><span class="sd"></span>' + title + '<span class="count">' + items.length + "</span></h3>" +
      items.map((x) => {
        const fix = S.FIX[x.code], kind = S.KIND_OF[x.code];
        return '<div class="issue">' + esc(x.message) + (fix ? '<div class="fixhint">' + esc(fix) + "</div>" : "") +
          ((x.codes || []).length ? '<div class="tags">' + x.codes.map((c) => '<span class="tag">' + esc(c) + "</span>").join("") + "</div>" : "") +
          (kind && overlayOn ? '<button type="button" class="link showon" data-flag="' + j.flags.indexOf(x) + '">Show on page</button>' : "") + "</div>";
      }).join("") + "</section>";
  }
  h += codesHtml;                                   // the problems first, the code list after
  $("result").innerHTML = h;
}

$("result").addEventListener("click", (ev) => {
  const c = ev.target.closest(".code");
  if (c) { pick = pick === c.dataset.code ? "" : c.dataset.code; render(); return; }
  if (ev.target.id === "clearpick") { pick = ""; render(); return; }
  const so = ev.target.closest(".showon");
  if (so && last) {
    const x = last.flags[+so.dataset.flag];
    activeTabId().then((id) => { if (id != null && x) chrome.runtime.sendMessage({ t: "focus", tabId: id, code: x.code, codes: x.codes || [] }).catch(() => {}); });
  }
});
document.querySelectorAll(".stat").forEach((b) => (b.onclick = () => {
  if (pick) { pick = ""; render(); }
  const g = $("g-" + b.dataset.jump); if (g) g.scrollIntoView({ behavior: "smooth", block: "start" });
}));
$("recheck").onclick = () => runCheck();
$("copy").onclick = async () => {
  if (!last) return;
  const f = $("form"), lines = ["Althais claim check: " + $("pct").textContent + "% – " + $("vlabel").textContent, claimMeta(),
    "CPT: " + (list(f.cpt.value).join(", ") || "none") + (f.modifiers.value.trim() ? "  Modifiers: " + list(f.modifiers.value).join(", ") : ""), "ICD-10: " + (list(f.icd.value).join(", ") || "none")];
  for (const [s, title] of SEVS) {
    const items = last.flags.filter((x) => sevOf(x.severity) === s);
    if (items.length) lines.push("", title + ":", ...items.map((x) => "- " + x.message));
  }
  try { await navigator.clipboard.writeText(lines.join("\n")); $("copy").title = "Copied"; } catch (e) { say("readmsg", "Couldn't copy. Select the text and copy it instead.", true); }
};

function busy(text) {
  $("verify").classList.add("busy");
  if (!last) setVerify("idle busy", null, text, claimMeta());
}
function fail(e) {
  $("verify").classList.remove("busy");
  if (!last) setVerify("idle", null, "Couldn't check this claim", claimMeta());
  say("readmsg", e.message, true);
}
async function status() {
  const dot = $("dot"), t = $("conn");
  if (!last) reset();
  if (!cfg.token) { dot.className = "dot"; t.textContent = "Not connected. Open settings to pair."; return; }
  try { await api("/api/extension/ping"); dot.className = "dot ok"; t.textContent = "Connected to " + new URL(base()).hostname; }
  catch (e) { dot.className = "dot bad"; t.textContent = e.message; }
}

$("fetch").onclick = async () => {
  const f = $("form"), box = $("result");
  if (!cfg.token) { $("settings").hidden = false; say("setmsg", "Paste your pairing token first.", true); return; }
  busy("Fetching from athenahealth…");
  try {
    const j = await api("/api/extension/lookup", { method: "POST", body: JSON.stringify({ claim_id: f.claim_id.value }) });
    fill(j.fields); showResult(j); say("readmsg", "Codes came from your athenahealth connection, not the screen.");
  } catch (e) { fail(e); }
};

const list = (s) => s.split(/[,\s;]+/).map((x) => x.trim()).filter(Boolean);
async function runCheck() {
  const f = $("form");
  if (!cfg.token) { $("settings").hidden = false; say("setmsg", "Paste your pairing token first.", true); return; }
  busy("Checking claim…");
  try {
    const j = await api("/api/extension/check", { method: "POST", body: JSON.stringify({ claim: {
      claim_id: f.claim_id.value, dos: f.dos.value, payer: f.payer.value, cpt: list(f.cpt.value), icd: list(f.icd.value),
      modifiers: list(f.modifiers.value), amount: f.amount.value } }) });
    showResult(j);
  } catch (e) { fail(e); }
}
$("form").onsubmit = (ev) => { ev.preventDefault(); runCheck(); };
chrome.runtime.connect({ name: "panel" });                // lets the on-page badge shrink while the panel is open
init();
