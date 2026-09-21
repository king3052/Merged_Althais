(function () {
  "use strict";

  var form = document.getElementById("demo-form");
  if (!form) return;

  function $(id) { return document.getElementById(id); }
  var msg = $("msg"), btn = $("btn"), label = $("btn-label");
  var fields = ["full_name", "email", "practice_name", "phone"];

  function showMsg(text, badIds) {
    msg.textContent = text;
    msg.hidden = false;
    fields.forEach(function (id) { $(id).classList.toggle("bad", (badIds || []).indexOf(id) !== -1); });
  }
  function clearMsg() {
    msg.hidden = true;
    fields.forEach(function (id) { $(id).classList.remove("bad"); });
  }
  function setBusy(busy) {
    btn.disabled = busy;
    label.textContent = busy ? "Sending…" : "Request a demo";
  }

  fields.forEach(function (id) { $(id).addEventListener("input", function () { $(id).classList.remove("bad"); }); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearMsg();

    var name = $("full_name").value.trim();
    var email = $("email").value.trim();
    var practice = $("practice_name").value.trim();
    var phone = $("phone").value.trim();

    var missing = [];
    if (!name) missing.push("full_name");
    if (!email) missing.push("email");
    if (!practice) missing.push("practice_name");
    if (missing.length) { showMsg("Please fill in all required fields.", missing); $(missing[0]).focus(); return; }
    if (email.indexOf("@") === -1) { showMsg("Enter a valid email address.", ["email"]); $("email").focus(); return; }

    setBusy(true);
    var body = new URLSearchParams();
    body.set("full_name", name);
    body.set("email", email);
    body.set("practice_name", practice);
    body.set("phone", phone);

    fetch("/request-demo", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString()
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; });
    }).then(function (r) {
      if (r.ok && r.data && r.data.ok) {
        $("form-wrap").hidden = true;
        $("success").hidden = false;
        $("form-card").scrollIntoView({ block: "nearest", behavior: "smooth" });
      } else {
        showMsg((r.data && (r.data.error || r.data.detail)) && typeof (r.data.error || r.data.detail) === "string" ? (r.data.error || r.data.detail) : "Something went wrong. Please try again.");
        setBusy(false);
      }
    }).catch(function () {
      showMsg("Network error. Please try again.");
      setBusy(false);
    });
  });
})();
