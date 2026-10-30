// Althais extension background worker.
// - Opens the side panel when the toolbar icon is clicked.
// - Runs the on-page overlay: content.js in each frame of an allowed site reports changes, this worker reads every frame,
//   merges the claim, asks Althais to check it, then sends the result back to the page (overlay + outlines) and the panel.
// - One-click pairing from the Althais Connect EMR page (pair.js).
importScripts("shared.js");
const S = self.AlthaisShared;

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

const get = (keys) => new Promise((r) => chrome.storage.local.get(keys, r));
const set = (obj) => new Promise((r) => chrome.storage.local.set(obj, r));

// ---------- where the overlay runs ----------
// Sites the biller allowed (optional host permissions), plus Althais's own synthetic test screens.
// Althais's own synthetic test screens (a medical EMR and a dental practice system)
const MOCK = ["mock-emr", "mock-dental"].flatMap((d) => ["https://web-production-8fe4.up.railway.app/static/" + d + "/*", "https://*.althais.com/static/" + d + "/*",
  "http://localhost/static/" + d + "/*", "http://127.0.0.1/static/" + d + "/*"]);
const isMock = (path) => /^\/static\/mock-(emr|dental)\//.test(path);
const BUILT_IN = new Set(chrome.runtime.getManifest().host_permissions || []);
const SCRIPT_ID = "althais-overlay";

async function syncScripts() {
  const { overlay = true } = await get(["overlay"]);
  try { await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] }); } catch (e) {}
  if (!overlay) return;
  const perms = await chrome.permissions.getAll();
  const allowed = (perms.origins || []).filter((o) => !BUILT_IN.has(o) && /^https:\/\//.test(o));
  try {
    await chrome.scripting.registerContentScripts([{ id: SCRIPT_ID, js: ["shared.js", "parse.js", "content.js"], matches: [...new Set([...allowed, ...MOCK])],
      allFrames: true, runAt: "document_idle", persistAcrossSessions: true }]);
  } catch (e) { console.warn("Althais: couldn't register the overlay", e); }
}

// Put the overlay into tabs that are already open on a site the biller just allowed (no reload needed).
async function injectInto(tabId) {
  try { await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["shared.js", "parse.js", "content.js"] }); } catch (e) {}
}
// On install or update, put the overlay into tabs that are already open, so nobody has to reload their EMR.
async function injectOpenTabs() {
  const { overlay = true } = await get(["overlay"]);
  if (!overlay) return;
  const perms = await chrome.permissions.getAll();
  const allowed = new Set((perms.origins || []).filter((o) => !BUILT_IN.has(o)));
  const tabsNow = await chrome.tabs.query({});
  for (const t of tabsNow) {
    let u; try { u = new URL(t.url); } catch (e) { continue; }
    if (allowed.has(u.origin + "/*") || isMock(u.pathname)) injectInto(t.id);
  }
}
chrome.runtime.onInstalled.addListener(async () => { await syncScripts(); injectOpenTabs(); });
chrome.runtime.onStartup.addListener(syncScripts);
chrome.permissions.onAdded.addListener(async (p) => {
  await syncScripts();
  const { overlay = true } = await get(["overlay"]);
  if (!overlay || !(p.origins || []).length) return;
  const tabs = await chrome.tabs.query({});
  for (const t of tabs) {
    try { const o = new URL(t.url).origin + "/*"; if (p.origins.includes(o)) injectInto(t.id); } catch (e) {}
  }
});
chrome.permissions.onRemoved.addListener(syncScripts);
chrome.storage.onChanged.addListener((ch) => { if (ch.overlay) syncScripts(); });

// ---------- checking ----------
const tabs = new Map();   // tabId -> { timer, busy, again, fp, res, out }
const tab = (id) => { if (!tabs.has(id)) tabs.set(id, {}); return tabs.get(id); };
chrome.tabs.onRemoved.addListener((id) => tabs.delete(id));
chrome.tabs.onActivated.addListener(({ tabId }) => schedule(tabId, 200));

function schedule(tabId, ms, force) {
  const st = tab(tabId);
  st.force = st.force || !!force;
  clearTimeout(st.timer);
  st.timer = setTimeout(() => run(tabId), ms == null ? 450 : ms);
}

const toTop = (tabId, msg) => chrome.tabs.sendMessage(tabId, msg, { frameId: 0 }).catch(() => {});
async function toAll(tabId, msg) {
  let frames = [];
  try { frames = (await chrome.webNavigation.getAllFrames({ tabId })) || []; } catch (e) {}
  if (!frames.length) frames = [{ frameId: 0 }];
  await Promise.all(frames.map((f) => chrome.tabs.sendMessage(tabId, msg, { frameId: f.frameId }).catch(() => null)));
}
async function scanAll(tabId) {
  let frames = [];
  try { frames = (await chrome.webNavigation.getAllFrames({ tabId })) || []; } catch (e) {}
  if (!frames.length) frames = [{ frameId: 0 }];
  const outs = await Promise.all(frames.map((f) => chrome.tabs.sendMessage(tabId, { t: "scan" }, { frameId: f.frameId }).catch(() => null)));
  return outs.filter((o) => o && o.fields);
}

function badge(tabId, j) {
  if (!j) { chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {}); return; }
  const n = j.flags.filter((x) => x.severity === "error" || x.severity === "warn").length;
  chrome.action.setBadgeText({ tabId, text: n ? String(n) : "✓" }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ tabId, color: j.verdict === "fix" ? "#b42318" : j.verdict === "review" ? "#a15c07" : "#17784a" }).catch(() => {});
}

async function check(cfg, fields) {
  const base = (cfg.server || S.DEFAULT_SERVER).replace(/\/+$/, "");
  let r;
  try {
    r = await fetch(base + "/api/extension/check", { method: "POST", headers: { Authorization: "Bearer " + cfg.token, "Content-Type": "application/json" },
      body: JSON.stringify({ claim: { claim_id: fields.claim_id, dos: fields.dos, payer: fields.payer, cpt: fields.cpt, icd: fields.icd, modifiers: fields.modifiers, amount: fields.amount,
      lines: (fields.lines || []).filter((l) => "tooth" in l), tooth_column: !!fields.tooth_column,
      line_mods: (fields.lines || []).filter((l) => l.mod).map((l) => ({ code: l.code, mod: l.mod })) } }) });
  } catch (e) { throw Object.assign(new Error("Couldn't reach Althais. Check your connection."), { kind: "offline" }); }
  let j = {};
  try { j = await r.json(); } catch (e) {}
  if (r.status === 401) throw Object.assign(new Error("This browser isn't connected to Althais anymore. Connect it again from Revenue > Connect EMR."), { kind: "unpaired" });
  if (!r.ok) throw new Error(j.error || "Althais returned an error (" + r.status + ").");
  j.flags = j.flags || [];
  return j;
}

async function deliver(tabId, st, quiet) {
  const j = st.res, msg = { t: "verdict", verdict: j.verdict, score: j.score, flags: j.flags, codes: j.codes || [], fields: st.out.fields, quiet: !!quiet };
  await toAll(tabId, msg);
  if (quiet) return;
  badge(tabId, j);
  chrome.runtime.sendMessage({ t: "result", tabId, result: j, out: st.out }).catch(() => {});
}

async function run(tabId) {
  const st = tab(tabId);
  if (st.busy) { st.again = true; return; }
  st.busy = true;
  const force = st.force; st.force = false;
  try {
    const cfg = await get(["server", "token", "overlay", "offHosts"]);
    if (cfg.overlay === false) return;
    let host = "";
    try { host = new URL((await chrome.tabs.get(tabId)).url).host; } catch (e) {}
    if ((cfg.offHosts || []).includes(host)) {                       // the biller hid Althais on this site
      st.fp = ""; st.res = null;
      await toAll(tabId, { t: "verdict", verdict: null, flags: [] });
      badge(tabId, null);
      return;
    }
    const outs = await scanAll(tabId);
    if (!outs.length) return;
    const out = S.merge(outs);
    if (!out.fields.cpt.length && !out.fields.icd.length) {            // not a claim screen
      st.fp = ""; st.res = null; st.out = null;
      await toAll(tabId, { t: "verdict", verdict: null, flags: [] });
      await toTop(tabId, { t: "state", state: "idle" });
      badge(tabId, null);
      return;
    }
    if (!cfg.token) { await toTop(tabId, { t: "state", state: "unpaired" }); return; }
    const fp = S.fingerprint(out.fields);
    if (fp === st.fp && st.res && !force) { st.out = out; await deliver(tabId, st, true); return; }   // same claim: refresh outlines only
    await toTop(tabId, { t: "state", state: "checking" });
    st.res = await check(cfg, out.fields);
    st.fp = fp; st.out = out;
    await deliver(tabId, st);
  } catch (e) {
    await toTop(tabId, { t: "state", state: e.kind === "unpaired" ? "unpaired" : "error", text: e.message });
  } finally {
    st.busy = false;
    if (st.again) { st.again = false; schedule(tabId, 250); }
  }
}

// ---------- code a note, create a claim, fill codes (each only when the biller presses the button) ----------
async function ask(tabId, msg) {
  let frames = [];
  try { frames = (await chrome.webNavigation.getAllFrames({ tabId })) || []; } catch (e) {}
  if (!frames.length) frames = [{ frameId: 0 }];
  return (await Promise.all(frames.map((f) => chrome.tabs.sendMessage(tabId, msg, { frameId: f.frameId }).catch(() => null)))).filter(Boolean);
}
async function post(path, body) {
  const cfg = await get(["server", "token"]);
  if (!cfg.token) throw new Error("Connect this browser to Althais first (Revenue › Connect EMR › Browser Extension).");
  const base = (cfg.server || S.DEFAULT_SERVER).replace(/\/+$/, "");
  let r;
  try { r = await fetch(base + path, { method: "POST", headers: { Authorization: "Bearer " + cfg.token, "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch (e) { throw new Error("Couldn't reach Althais. Check your connection."); }
  let j = {};
  try { j = await r.json(); } catch (e) {}
  if (!r.ok) { const err = new Error(j.error || j.detail || "Althais returned an error (" + r.status + ")."); err.data = j; throw err; }
  return j;
}
async function codeNote(tabId) {
  const notes = await ask(tabId, { t: "note" });
  const pick = notes.filter((n) => n && n.text).sort((a, b) => (b.score || 0) - (a.score || 0))[0];
  if (!pick) throw new Error("Couldn't find a visit note on this screen. Open the chart note, or select the note text, and try again.");
  const j = await post("/api/extension/code-note", { note: pick.text });
  return { codes: j.codes || [], how: pick.how, chars: pick.text.length };
}
async function claimPrefill(tabId) {
  const parties = await ask(tabId, { t: "party" });
  const party = {};
  parties.forEach((p) => Object.keys(p).forEach((k) => { if (!party[k] && p[k]) party[k] = p[k]; }));
  const outs = await scanAll(tabId);
  const f = outs.length ? S.merge(outs).fields : {};
  return { party, fields: f };
}
async function fillAll(tabId, cpt, icd) {
  const rs = await ask(tabId, { t: "fill", cpt, icd });
  return rs.reduce((a, r) => ({ icd: a.icd + (r.icd || 0), cpt: a.cpt + (r.cpt || 0), skipped: Math.max(a.skipped, r.skipped || 0), noRoom: Math.min(a.noRoom, r.noRoom == null ? 99 : r.noRoom) }), { icd: 0, cpt: 0, skipped: 0, noRoom: 99 });
}
const respond = (p, reply) => { p.then((v) => reply({ ok: true, v }), (e) => reply({ ok: false, error: e.message, data: e.data })); return true; };

// ---------- messages ----------
chrome.runtime.onMessage.addListener((m, sender, reply) => {
  const tabId = sender.tab && sender.tab.id;
  if (m.t === "changed" && tabId != null) { schedule(tabId, 450); return; }
  if (m.t === "recheck" && tabId != null) { schedule(tabId, 0, true); return; }
  if (m.t === "submitWarn" && tabId != null) { toTop(tabId, { t: "submitWarn", n: m.n }); return; }
  if (m.t === "focus") { const id = m.tabId != null ? m.tabId : tabId; if (id != null) toAll(id, { t: "focus", i: m.i, code: m.code, codes: m.codes }); return; }
  if (m.t === "current") {          // the panel or a freshly loaded overlay asks for the latest result
    const id = m.tabId != null ? m.tabId : tabId, st = tabs.get(id);
    reply(Object.assign({ panelOpen: panels > 0 }, st && st.res ? { result: st.res, out: st.out } : {}));
    return;
  }
  if (m.t === "codeNote" && tabId != null) return respond(codeNote(tabId), reply);
  if (m.t === "claimPrefill" && tabId != null) return respond(claimPrefill(tabId), reply);
  if (m.t === "claimAction" && tabId != null) return respond(post("/api/extension/claim", { action: m.action, claim: m.claim }), reply);
  if (m.t === "fill" && tabId != null) return respond(fillAll(tabId, m.cpt || [], m.icd || []), reply);
  if (m.t === "pair") {
    (async () => {
      const server = String(m.server || "").replace(/\/+$/, "");
      if (!/^https:\/\/([a-z0-9-]+\.)*(althais\.com|up\.railway\.app)$|^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(server)) return reply({ ok: false, error: "That isn't an Althais address." });
      try {
        const r = await fetch(server + "/api/extension/ping", { headers: { Authorization: "Bearer " + m.token } });
        if (!r.ok) return reply({ ok: false, error: "Althais didn't accept that token. Create a new one and try again." });
        await set({ server, token: m.token });
        tabs.forEach((st) => { st.fp = ""; });
        reply({ ok: true });
      } catch (e) { reply({ ok: false, error: "Couldn't reach Althais to confirm the connection." }); }
    })();
    return true;
  }
});

// While the side panel is open, the on-page badge shrinks to a dot so the two don't compete.
let panels = 0;
async function tellPanel() {
  const all = await chrome.tabs.query({});
  for (const t of all) chrome.tabs.sendMessage(t.id, { t: "panel", open: panels > 0 }, { frameId: 0 }).catch(() => {});
}
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "panel") return;
  panels++; tellPanel();
  port.onDisconnect.addListener(() => { panels = Math.max(0, panels - 1); tellPanel(); });
});

chrome.commands.onCommand.addListener(async (cmd) => {
  if (cmd !== "toggle-overlay") return;
  const [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (t) toTop(t.id, { t: "toggle" });
});

// A new token or server means every tab should check again.
chrome.storage.onChanged.addListener((ch) => { if (ch.token || ch.server) tabs.forEach((st) => { st.fp = ""; st.res = null; }); });
