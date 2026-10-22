/* Automatic sign-out after inactivity (security.py). Added to every signed-in page by the server.
   - Mouse, keyboard, scroll and touch count as activity; the server is told at most once a minute (POST .../ping).
     Background requests never count, so a screen left open signs out on time even while it keeps refreshing data.
   - A minute before the clinic's limit, a notice counts down; any activity keeps you signed in.
   - Activity in another tab or window of Althais counts too (the server keeps one clock per session).
   - When the time is up, the page goes to the signed-out screen. The server has already ended the session, so
     nothing on the page can be reached by anyone who walks up to the screen afterwards. */
(function () {
  'use strict';
  var me = document.currentScript, admin = me && me.dataset.admin === '1';
  var base = admin ? '/admin/api/session' : '/api/session', out = admin ? '/admin/login' : '/logout?idle=1';
  var lastActive = Date.now(), lastPing = Date.now(), lastStatus = 0, limit = 0, deadline = 0, box = null, tick = null, busy = false;

  function call(method, path) {
    return fetch(base + path, { method: method, credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.status === 401 ? null : r.json().catch(function () { return {}; }); }, function () { return {}; });
  }
  function signOut() {
    clearInterval(tick);
    try { localStorage.setItem('althais.signedOut', String(Date.now())); } catch (e) { /* storage blocked */ }
    if (admin) fetch('/admin/logout', { method: 'POST', credentials: 'same-origin' }).catch(function () {}).then(function () { location.href = out; });
    else location.href = out;
  }
  function apply(s) {
    if (s === null) return signOut();
    if (!s || !s.idle_seconds) return;
    limit = s.idle_seconds; deadline = Date.now() + s.remaining * 1000;
  }
  function ping() {
    lastPing = Date.now();
    return call('POST', '/ping').then(function (s) { apply(s); hide(); });
  }
  function active() {
    lastActive = Date.now();
    try { localStorage.setItem('althais.active', String(lastActive)); } catch (e) { /* storage blocked */ }
    if (box) ping();
  }
  ['mousemove', 'mousedown', 'keydown', 'wheel', 'scroll', 'touchstart'].forEach(function (ev) { window.addEventListener(ev, active, { passive: true, capture: true }); });
  window.addEventListener('storage', function (e) {
    if (e.key === 'althais.active') { lastActive = Math.max(lastActive, +e.newValue || 0); if (box) hide(); }
    if (e.key === 'althais.signedOut') location.href = out;   // another tab timed out: this one follows
  });

  function show() {
    if (box) return;
    box = document.createElement('div');
    box.setAttribute('role', 'alertdialog'); box.setAttribute('aria-live', 'assertive'); box.setAttribute('aria-label', 'Signing out soon');
    box.style.cssText = 'position:fixed;inset:0;z-index:2147483000;background:rgba(15,17,22,.45);display:flex;align-items:center;justify-content:center;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif';
    box.innerHTML = '<div style="background:#fff;color:#0f1116;border-radius:8px;box-shadow:0 18px 50px rgba(0,0,0,.3);padding:22px 24px;max-width:400px;margin:16px">' +
      '<div style="font-size:16px;font-weight:600;margin-bottom:6px">Still there?</div>' +
      '<div style="color:#4a505c;margin-bottom:14px">For patient privacy, Althais signs you out after <b id="sg-min"></b> without activity. Signing out in <b id="sg-left" style="font-variant-numeric:tabular-nums"></b>.</div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" id="sg-out" style="padding:8px 14px;border:1px solid #d5d9e0;background:#fff;border-radius:6px;cursor:pointer;font:inherit">Sign Out</button>' +
      '<button type="button" id="sg-stay" style="padding:8px 14px;border:0;background:#0d5bd7;color:#fff;border-radius:6px;cursor:pointer;font:inherit;font-weight:600">Stay Signed In</button></div></div>';
    document.body.appendChild(box);
    box.querySelector('#sg-min').textContent = Math.round(limit / 60) + ' minute' + (Math.round(limit / 60) === 1 ? '' : 's');
    box.querySelector('#sg-stay').addEventListener('click', function () { active(); ping(); });
    box.querySelector('#sg-out').addEventListener('click', function () { location.href = admin ? out : '/logout'; });
    box.querySelector('#sg-stay').focus();
    paint();
  }
  function hide() { if (box) { box.remove(); box = null; } }
  function paint() {
    if (!box) return;
    var s = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    box.querySelector('#sg-left').textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  function check() {
    if (!limit || busy) return;
    var now = Date.now();
    // someone is working here: tell the server (at most once a minute)
    if (lastActive > lastPing && now - lastPing > 60000) { ping(); return; }
    var left = deadline - now;
    if (left > 75000) { hide(); return; }
    if (left > 0 && now - lastStatus < 5000) return;
    // close to the limit: ask the server first, since another tab or window may have kept the session alive
    busy = true; lastStatus = now;
    call('GET', '/status').then(function (s) {
      busy = false; apply(s);
      if (s === null) return;
      if (deadline - Date.now() <= 0) return signOut();
      if (deadline - Date.now() <= 62000) show(); else hide();
    });
  }
  call('GET', '/status').then(function (s) { apply(s); tick = setInterval(function () { check(); paint(); }, 1000); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { lastPing = 0; check(); } });
})();
