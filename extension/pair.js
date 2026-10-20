// Runs only on Althais's own pages. Lets the Connect EMR page connect this browser with one click, so nobody has to
// copy and paste a token. It listens for one message type from the page itself and touches nothing else.
(() => {
  document.documentElement.setAttribute("data-althais-extension", chrome.runtime.getManifest().version);
  window.addEventListener("message", (e) => {
    if (e.source !== window || e.origin !== location.origin) return;
    const d = e.data;
    if (!d || d.type !== "althais:pair" || typeof d.token !== "string" || !/^\d{1,9}\.[A-Za-z0-9_\-]{20,80}$/.test(d.token)) return;
    const done = (r) => window.postMessage({ type: "althais:paired", ok: !!(r && r.ok), error: (r && r.error) || "" }, location.origin);
    try {
      chrome.runtime.sendMessage({ t: "pair", token: d.token, server: location.origin }, (r) => { void chrome.runtime.lastError; done(r || { ok: false, error: "The extension didn't answer. Reload it and try again." }); });
    } catch (err) { done({ ok: false, error: "The extension was updated. Reload this page and try again." }); }
  });
})();
