(function () {
  "use strict";

  var mode = "login"; // "login" | "register"
  function $(id) { return document.getElementById(id); }
  var msg = $("auth-msg"), btn = $("submit-btn"), label = $("submit-label");
  var fields = ["f-name", "f-email", "f-password"];

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
    $("forgot-link").hidden = reg;
    $("form-eyebrow").textContent = reg ? "Get Started" : "Welcome";
    $("form-title").innerHTML = reg ? 'Create your <span class="em">account.</span>' : 'Sign in to your<br /><span class="em">Althais workspace.</span>';
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

  $("auth-form").addEventListener("submit", function (e) {
    e.preventDefault();
    clearMsg();
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
    if (mode === "register" && password.length < 8) {
      showMsg("Password must be at least 8 characters.", "err", ["f-password"]);
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
      body.set("full_name", $("f-name").value.trim());
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
      var text = r.data && typeof r.data.error === "string" ? r.data.error : "Something went wrong. Please try again.";
      showMsg(text, "err", mode === "login" && /invalid/i.test(text) ? ["f-email", "f-password"] : []);
      btn.disabled = false; label.textContent = original;
    }).catch(function () {
      showMsg("We could not reach the server. Check your connection and try again.", "err");
      btn.disabled = false; label.textContent = original;
    });
  });
})();
