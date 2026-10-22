(function () {
  "use strict";

  var mode = "login"; // "login" | "register" | "mfa" (the code after a correct password)
  var challenge = "";
  function $(id) { return document.getElementById(id); }
  var msg = $("auth-msg"), btn = $("submit-btn"), label = $("submit-label");
  var fields = ["f-email", "f-password"];

  function showMsg(text, kind, badIds) {
    msg.textContent = text;
    msg.classList.toggle("ok", kind === "ok");
    msg.hidden = false;
    fields.forEach(function (id) { $(id).classList.toggle("bad", (badIds || []).indexOf(id) !== -1); });
  }
  function clearMsg() {
    msg.hidden = true;
    fields.forEach(function (id) { $(id).classList.remove("bad"); });
  }

  function setMode(m) {
    mode = m;
    var reg = m === "register";
    document.querySelector(".lg-left").classList.toggle("is-register", reg);   /* compact layout so the whole card fits on screen */
    $("reg-fields").hidden = !reg;
    $("pw-hint").hidden = !reg;
    $("pw-hint").textContent = "Use at least 12 characters. A short phrase of a few words works well.";
    $("forgot-link").hidden = reg;
    $("form-eyebrow").textContent = reg ? "Get Started" : "Welcome";
    $("form-title").innerHTML = reg ? 'Create your <span class="em">account.</span>' : 'Sign in to your<br /><span class="em">Althais Workspace.</span>';
    $("form-sub").textContent = reg ? "Set up access for your practice." : "Enter your details to continue.";
    label.textContent = reg ? "Create Account" : "Sign In";
    $("toggle-prompt").textContent = reg ? "Already have an account?" : "New to Althais?";
    $("toggle-mode").textContent = reg ? "Sign In" : "Create An Account";
    $("f-password").setAttribute("autocomplete", reg ? "new-password" : "current-password");
    $("f-password").placeholder = reg ? "Choose A Password" : "Enter Your Password";
    document.title = (reg ? "Create Account" : "Sign In") + " | Althais";
    clearMsg();
  }

  $("toggle-mode").addEventListener("click", function () { setMode(mode === "login" ? "register" : "login"); });

  /* show / hide password */
  $("pw-toggle").addEventListener("click", function () {
    var pw = $("f-password"), show = pw.type === "password";
    pw.type = show ? "text" : "password";
    this.setAttribute("aria-pressed", String(show));
    this.setAttribute("aria-label", show ? "Hide password" : "Show password");
    this.querySelector(".eye-on").hidden = show;
    this.querySelector(".eye-off").hidden = !show;
    pw.focus();
  });

  fields.forEach(function (id) { $(id).addEventListener("input", function () { $(id).classList.remove("bad"); }); });

  /* arriving from the sign-out page's "Create an account" button, or via /login?mode=register */
  var wantsRegister = false;
  try {
    if (sessionStorage.getItem("althais_mode") === "register") { sessionStorage.removeItem("althais_mode"); wantsRegister = true; }
  } catch (e) { /* storage blocked: fine */ }
  if (/(?:^|[?&])mode=register(?:&|$)/.test(location.search)) wantsRegister = true;
  if (wantsRegister) setMode("register");
  /* signed out automatically after inactivity, or the session ended elsewhere */
  if (/(?:^|[?&])(idle|expired)=1(?:&|$)/.test(location.search)) showMsg("You were signed out after a while without activity. Sign in again to continue.", "ok");

  function mfaMode(ch) {
    mode = "mfa"; challenge = ch;
    ["f-email", "f-password"].forEach(function (id) { $(id).closest(".field").hidden = true; });
    $("mfa-fields").hidden = false;
    $("form-eyebrow").textContent = "Two-Step Login";
    $("form-title").innerHTML = 'Enter your<br /><span class="em">code.</span>';
    $("form-sub").textContent = "Open your authenticator app and type the 6-digit code for Althais.";
    label.textContent = "Verify";
    $("toggle-mode").closest(".lg-switch").hidden = true;
    $("f-code").value = ""; $("f-code").focus();
  }
  function passwordMode() {
    mode = "login"; challenge = "";
    ["f-email", "f-password"].forEach(function (id) { $(id).closest(".field").hidden = false; });
    $("mfa-fields").hidden = true;
    $("toggle-mode").closest(".lg-switch").hidden = false;
    setMode("login"); $("f-password").value = ""; $("f-password").focus();
  }
  $("f-code").addEventListener("input", function () {
    var v = this.value.trim();
    if (/^\d{6}$/.test(v)) $("auth-form").requestSubmit();
  });

  $("auth-form").addEventListener("submit", function (e) {
    e.preventDefault();
    clearMsg();
    if (mode === "mfa") return sendCode();
    var email = $("f-email").value.trim();
    var password = $("f-password").value;

    if (!email || !password) {
      var bad = [];
      if (!email) bad.push("f-email");
      if (!password) bad.push("f-password");
      showMsg("Enter your email and password.", "err", bad);
      $(bad[0]).focus();
      return;
    }
    if (mode === "register" && password.length < 12) {
      showMsg("Use at least 12 characters. A short phrase of a few words works well.", "err", ["f-password"]);
      $("f-password").focus();
      return;
    }

    btn.disabled = true;
    var original = label.textContent;
    label.textContent = mode === "register" ? "Creating account…" : "Signing in…";

    /* the backend takes URL-encoded form fields */
    var body = new URLSearchParams();
    body.set("email", email);
    body.set("password", password);
    if (mode === "register") {
      body.set("organization", $("f-org").value.trim());
    }

    fetch("/" + (mode === "register" ? "register" : "login"), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      credentials: "same-origin"
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; });
    }).then(function (r) {
      if (r.ok && r.data && r.data.ok) {
        label.textContent = "Redirecting…";
        window.location.href = r.data.redirect || "/overview";
        return;
      }
      if (r.ok && r.data && r.data.mfa) { btn.disabled = false; mfaMode(r.data.challenge); return; }
      var text = r.data && typeof r.data.error === "string" ? r.data.error : "Something went wrong. Please try again.";
      showMsg(text, "err", mode === "login" && /invalid/i.test(text) ? ["f-email", "f-password"] : []);
      btn.disabled = false; label.textContent = original;
    }).catch(function () {
      showMsg("We could not reach the server. Check your connection and try again.", "err");
      btn.disabled = false; label.textContent = original;
    });
  });

  /* the second step: the code from the authenticator app (or a recovery code) */
  function sendCode() {
    var code = $("f-code").value.trim();
    if (!code) { showMsg("Type the code from your authenticator app.", "err"); $("f-code").focus(); return; }
    btn.disabled = true; label.textContent = "Checking…";
    var body = new URLSearchParams(); body.set("challenge", challenge); body.set("code", code);
    fetch("/login/mfa", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body.toString(), credentials: "same-origin" })
      .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (r) {
        if (r.ok && r.data.ok) {
          label.textContent = "Redirecting…";
          var go = function () { window.location.href = r.data.redirect || "/overview"; };
          if (r.data.notice) { showMsg(r.data.notice, "ok"); setTimeout(go, 3500); } else go();
          return;
        }
        btn.disabled = false; label.textContent = "Verify";
        if (r.data.restart) { passwordMode(); showMsg(r.data.error || "Enter your password again.", "err"); return; }
        showMsg(r.data.error || "That code didn't work.", "err");
        $("f-code").value = ""; $("f-code").focus();
      })
      .catch(function () { btn.disabled = false; label.textContent = "Verify"; showMsg("We could not reach the server. Check your connection and try again.", "err"); });
  }
})();
