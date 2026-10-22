/* v4: Settings › Security and data. Your own sign-in (two-step login, recovery codes, password, where you're signed in),
   and, for the clinic's admins, the clinic's rules: automatic sign-out time and who must use two-step login, with each
   person's status and a reset for a lost phone. Everything here is on the server (security.py), not in this browser. */
(function () {
  var E = U.esc;
  var MFA = { admins_billers: 'Required for admins and billers', everyone: 'Required for everyone', optional: 'Optional' };
  var ROLE = { admin: 'Admin', biller: 'Biller', provider: 'Provider', viewer: 'Viewer', front_desk: 'Front desk' };
  function api(method, url, body, form) {
    var o = { method: method, credentials: 'same-origin', headers: {} };
    if (form) { o.headers['Content-Type'] = 'application/x-www-form-urlencoded'; o.body = new URLSearchParams(body).toString(); }
    else if (body) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || j.detail || 'Something went wrong.'); return j; }); });
  }
  V.sec = null;
  V.loadSecurity = function (after) {
    return Promise.all([api('GET', '/api/me/security'), api('GET', '/api/clinic/security')]).then(function (r) { V.sec = { me: r[0], clinic: r[1] }; if (after) after(); }, function () {});
  };
  var refresh = function () { V.loadSecurity(V.refresh); };
  var when = function (iso) { var d = new Date(iso); return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); };
  var device = function (ua) { ua = ua || ''; return (/iPhone|iPad/.test(ua) ? 'iPhone or iPad' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : 'Browser') + (/Edg\//.test(ua) ? ', Edge' : /Chrome\//.test(ua) ? ', Chrome' : /Safari\//.test(ua) ? ', Safari' : /Firefox\//.test(ua) ? ', Firefox' : ''); };

  V.securityHtml = function () {
    var x = V.sec;
    if (!x) return U.sect('Sign-in security', '', '<div class="dp-sec mut">Loading…</div>');
    var me = x.me, c = x.clinic, pol = c.policy;
    var mine = '<div class="dp-sec"><div class="dp-h">Two-step login</div>' +
      (me.mfa_enabled ? '<p style="margin:0 0 8px"><span class="tc s-grn">' + U.I.check + 'On</span> · a code from your authenticator app at every sign-in. ' + me.recovery_left + ' recovery code' + (me.recovery_left === 1 ? '' : 's') + ' left.</p>' +
          '<button class="btn sm" id="sec-codes">New Recovery Codes</button> ' + (me.mfa_required ? '<span class="mut" style="font-size:12px">Your clinic requires it for your role. New phone? Ask an admin to reset it.</span>' : '<button class="btn sm" id="sec-off">Turn Off</button>')
        : '<p style="margin:0 0 8px">' + (me.mfa_required ? U.st('Required, not set up', 'amb') : '<span class="mut">Off</span>') + ' · sign in with a code from your phone as well as your password.</p><a class="btn sm pri" style="display:inline-flex;align-items:center" href="/setup-2fa">Set Up Two-Step Login</a>') + '</div>' +
      '<div class="dp-sec"><div class="dp-h">Password</div><p style="margin:0 0 8px" class="mut">At least 12 characters. Changing it signs you out everywhere else.</p><button class="btn sm" id="sec-pw">Change Password</button></div>' +
      '<div class="dp-sec"><div class="dp-h">Where you’re signed in <span class="sub">' + me.sessions.length + '</span></div>' +
        U.grid(['Device', 'Signed in', 'Last active', 'Address'], me.sessions.map(function (s) { return { cells: [E(device(s.agent)) + (s.current ? ' <span class="tc s-blue">This one</span>' : ''), when(s.started), when(s.last_seen), '<span class="mono s-mut">' + E(s.ip || '') + '</span>'] }; }), { tight: 1 }) +
        (me.sessions.length > 1 ? '<div style="margin-top:6px"><button class="btn sm" id="sec-endothers">Sign Out Everywhere Else</button></div>' : '') + '</div>';
    var rules = '';
    if (c.can_edit) {
      rules = U.sect('Clinic sign-in rules', 'for everyone at ' + E(S().clinic.name || 'the clinic'),
        '<div class="form" id="secpol" style="padding:10px 12px">' + U.fields([
          { k: 'idle_minutes', label: 'Sign out automatically after this long without activity', type: 'select', options: c.idle_choices.map(function (m) { return [m, m + ' minutes']; }), span: 2 },
          { k: 'mfa', label: 'Two-step login', type: 'select', options: c.mfa_choices.map(function (k) { return [k, MFA[k]]; }), span: 2 }], pol).replace('<div class="form" style="padding:0">', '').replace(/<\/div>$/, '') +
        '</div><div style="padding:0 12px 10px;display:flex;gap:8px;align-items:center"><button class="btn sm pri" id="sec-savepol">Save Sign-In Rules</button><span class="mut" style="font-size:12px">Applies right away to everyone signed in.</span></div>' +
        '<div class="dp-sec"><div class="dp-h">People <span class="sub">' + c.members.filter(function (m) { return m.mfa; }).length + ' of ' + c.members.length + ' use two-step login</span></div>' +
        U.grid(['Name', 'Role', 'Two-step login', ''], c.members.map(function (m) {
          return { cells: [E(m.name) + '<div class="mut" style="font-size:11.5px">' + E(m.email) + '</div>', E(ROLE[m.role] || m.role),
            (m.mfa ? '<span class="s-grn">On</span>' : m.required ? U.st('Required, not set up', 'amb') : '<span class="mut">Off</span>') + (m.locked ? ' · <span class="s-red">Locked ' + m.locked + ' min</span>' : ''),
            (m.mfa && m.email !== me.email ? '<button class="mini" data-sec-reset="' + m.id + '" data-email="' + E(m.email) + '">Reset</button> ' : '') + (m.locked ? '<button class="mini" data-sec-unlock="' + m.id + '">Unlock</button>' : '')] };
        }), { tight: 1 }) + '</div>');
    }
    return U.sect('Your sign-in', E(me.email), mine) + rules;
  };
  var S = function () { return D.S; };

  var showCodes = function (codes, title) {
    var text = 'Althais recovery codes for ' + V.sec.me.email + '\nEach code works once.\n\n' + codes.join('\n') + '\n';
    U.modal(title || 'New Recovery Codes', '<p class="cf">Each code signs you in once if you don’t have your phone. Save them somewhere safe. Your old codes no longer work.</p><div class="mono" style="display:grid;grid-template-columns:1fr 1fr;gap:4px 18px;font-size:15px;padding:10px 14px;background:var(--ph);border:1px solid var(--b2);margin:8px 0">' + codes.map(function (c) { return '<span>' + E(c) + '</span>'; }).join('') + '</div>',
      [['Download', '', function () { U.download('althais-recovery-codes.txt', text, 'text/plain'); return false; }], ['Copy', '', function () { navigator.clipboard.writeText(text); U.toast('Copied.'); return false; }], ['Done', 'pri', function () {}]], { width: 460 });
  };
  var body = document.body;
  U.on(body, '#sec-codes', 'click', function () {
    U.ask('New Recovery Codes', 'Code from your authenticator app', '', function (code) {
      api('POST', '/api/me/2fa/recovery-codes', { code: code }).then(function (j) { showCodes(j.recovery_codes); refresh(); }, function (e) { U.toast(e.message, 'warn'); });
    }, { ok: 'Make New Codes', ph: '123456' });
  });
  U.on(body, '#sec-off', 'click', function () {
    U.form('Turn Off Two-Step Login', [{ k: 'password', label: 'Your password', type: 'password', req: 1, span: 4 }, { k: 'code', label: 'Code from your authenticator app', req: 1, span: 4, ph: '123456' }], {}, function (v) {
      api('POST', '/api/me/2fa/disable', v).then(function () { U.toast('Two-step login is off.'); refresh(); }, function (e) { U.toast(e.message, 'warn'); });
    }, { save: 'Turn Off', width: 420, intro: 'Without it, anyone with your password can sign in as you.' });
  });
  U.on(body, '#sec-pw', 'click', function () {
    U.form('Change Password', [{ k: 'current_password', label: 'Current password', type: 'password', req: 1, span: 4 }, { k: 'new_password', label: 'New password (at least 12 characters)', type: 'password', req: 1, span: 4 }, { k: 'confirm', label: 'New password again', type: 'password', req: 1, span: 4 }], {}, function (v) {
      if (v.new_password !== v.confirm) return 'The new passwords don’t match.';
      if (v.new_password.length < 12) return 'Use at least 12 characters.';
      api('POST', '/api/change-password', { current_password: v.current_password, new_password: v.new_password }, true)
        .then(function () { U.toast('Password changed. You’re signed out everywhere else.'); refresh(); }, function (e) { U.toast(e.message, 'warn'); });
    }, { save: 'Change Password', width: 420 });
  });
  U.on(body, '#sec-endothers', 'click', function () {
    api('POST', '/api/me/sessions/end-others').then(function (j) { U.toast('Signed out of ' + j.ended + ' other session' + (j.ended === 1 ? '' : 's') + '.'); refresh(); }, function (e) { U.toast(e.message, 'warn'); });
  });
  U.on(body, '#sec-savepol', 'click', function () {
    var v = U.values(document.getElementById('secpol'));
    api('PUT', '/api/clinic/security', { idle_minutes: +v.idle_minutes, mfa: v.mfa }).then(function () { U.toast('Sign-in rules saved.'); refresh(); }, function (e) { U.toast(e.message, 'warn'); });
  });
  U.on(body, '[data-sec-reset]', 'click', function (b) {
    U.confirm('Reset Two-Step Login', '<p class="cf">Reset two-step login for ' + E(b.dataset.email) + '? Do this only after you’ve confirmed it’s really them, for example in person or by phone. They’re signed out everywhere and set it up again at their next sign-in.</p>', 'Reset', function () {
      api('POST', '/api/clinic/members/' + b.dataset.secReset + '/reset-2fa').then(function () { U.toast('Reset. They’ll set it up again at their next sign-in.'); refresh(); }, function (e) { U.toast(e.message, 'warn'); });
    }, { danger: 1 });
  });
  U.on(body, '[data-sec-unlock]', 'click', function (b) {
    api('POST', '/api/clinic/members/' + b.dataset.secUnlock + '/unlock').then(function () { U.toast('Unlocked.'); refresh(); }, function (e) { U.toast(e.message, 'warn'); });
  });
})();
