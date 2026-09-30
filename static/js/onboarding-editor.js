/* Onboarding requirements editor, shared by Staff > Onboarding (Add Staff Member, Edit Requirements) and
 * Staff > Onboarding Templates.
 *
 *   var ed = OnboardingEditor.mount(container, items, { catalog, forms, trainings, detailed })
 *   ed.items()   -> the edited list, ready to send to the server (which cleans it again: staff_onboarding._clean_items)
 *
 * Items are the server's requirement items: { key, type, title, owner, required, dueDays, reminderDaysBefore, dependsOn, … }.
 * `detailed` shows reminders and dependencies too (the Templates page); Add Staff keeps it to the essentials.
 */
(function () {
  "use strict";
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };

  var GROUPS = ["Personal Information", "Employment Information", "Required Documents", "Professional Credentials",
                "Forms & Agreements", "Training", "System Access", "Manager Approval"];
  function groupOf(it) {
    if (it.type === "info") return it.section === "employment" ? "Employment Information" : it.section === "professional" ? "Professional Credentials" : "Personal Information";
    if (it.type === "document") return it.credentialType ? "Professional Credentials" : "Required Documents";
    if (it.type === "form") return "Forms & Agreements";
    if (it.type === "training") return "Training";
    return (it.key === "manager_review" || it.key === "background") ? "Manager Approval" : "System Access";
  }
  var TYPE_LABEL = { info: "Information", document: "Upload", form: "Form", training: "Training", manager: "Manager Task" };
  function typeLabel(it) { return it.type === "document" && it.credentialType ? "Credential" : (TYPE_LABEL[it.type] || "Task"); }
  function dueText(n) {
    if (n === null || n === undefined || n === "") return "No deadline";
    n = Number(n);
    return n === 0 ? "On start date" : Math.abs(n) + " day" + (Math.abs(n) === 1 ? "" : "s") + (n < 0 ? " before start" : " after start");
  }

  function mount(el, initial, opts) {
    opts = opts || {};
    var items = JSON.parse(JSON.stringify(initial || []));
    var catalog = opts.catalog || {};

    function render() {
      var byGroup = {};
      items.forEach(function (it, i) { (byGroup[groupOf(it)] = byGroup[groupOf(it)] || []).push(i); });
      var missing = Object.keys(catalog).filter(function (k) { return !items.some(function (it) { return it.key === k; }); });
      el.innerHTML = GROUPS.filter(function (g) { return byGroup[g]; }).map(function (g) {
        var idx = byGroup[g];
        return '<div class="oe-group"><div class="oe-group-title">' + esc(g) + '</div>' + idx.map(function (i, pos) {
          var it = items[i];
          var deps = items.filter(function (o) { return o.key !== it.key; });
          return '<div class="oe-row" data-i="' + i + '">' +
            '<div class="oe-move">' +
              '<button type="button" data-act="up" ' + (pos === 0 ? "disabled" : "") + ' aria-label="Move ' + esc(it.title) + ' up">▲</button>' +
              '<button type="button" data-act="down" ' + (pos === idx.length - 1 ? "disabled" : "") + ' aria-label="Move ' + esc(it.title) + ' down">▼</button>' +
            "</div>" +
            '<div class="oe-main">' +
              '<div class="oe-title">' + esc(it.title) + ' <span class="oe-type">' + esc(typeLabel(it)) + (it.owner === "manager" ? " · Clinic" : " · Employee") + "</span></div>" +
              '<div class="oe-controls">' +
                '<label class="oe-check"><input type="checkbox" data-f="required" ' + (it.required !== false ? "checked" : "") + '> Required</label>' +
                '<label class="oe-inline">Due <input type="number" data-f="dueDays" class="oe-num" value="' + (it.dueDays == null ? "" : esc(it.dueDays)) + '" placeholder="-" aria-label="Days from start date (negative = before)"> <span class="oe-hint">' + esc(dueText(it.dueDays)) + "</span></label>" +
                (opts.detailed ? (
                  (it.owner === "manager" ? "" : '<label class="oe-inline">Remind <input type="number" min="0" data-f="reminderDaysBefore" class="oe-num" value="' + (it.reminderDaysBefore == null ? "" : esc(it.reminderDaysBefore)) + '" placeholder="-" aria-label="Days before the deadline to remind them"> days before</label>') +
                  '<label class="oe-inline">After <select data-f="dependsOn" class="oe-sel"><option value="">Anytime</option>' +
                    deps.map(function (o) { return '<option value="' + esc(o.key) + '"' + ((it.dependsOn || [])[0] === o.key ? " selected" : "") + ">" + esc(o.title) + "</option>"; }).join("") +
                  "</select></label>") : "") +
              "</div>" +
            "</div>" +
            '<button type="button" class="oe-remove" data-act="remove" aria-label="Remove ' + esc(it.title) + '">Remove</button>' +
          "</div>";
        }).join("") + "</div>";
      }).join("") +
      '<div class="oe-add"><select class="oe-sel" data-add aria-label="Add requirement"><option value="">+ Add Requirement…</option>' +
        missing.map(function (k) { return '<option value="' + esc(k) + '">' + esc(catalog[k].title) + " (" + esc(typeLabel(catalog[k])) + ")</option>"; }).join("") +
        '<option value="__custom">Custom Requirement…</option></select></div>';
    }

    el.addEventListener("click", function (e) {
      var b = e.target.closest("[data-act]");
      if (!b) return;
      var i = Number(b.closest(".oe-row").dataset.i), g = groupOf(items[i]);
      var same = items.map(function (it, j) { return j; }).filter(function (j) { return groupOf(items[j]) === g; });
      var pos = same.indexOf(i);
      if (b.dataset.act === "remove") items.splice(i, 1);
      else {
        var j = same[pos + (b.dataset.act === "up" ? -1 : 1)];
        if (j === undefined) return;
        var t = items[i]; items[i] = items[j]; items[j] = t;
      }
      render();
    });
    el.addEventListener("change", function (e) {
      if (e.target.matches("[data-add]")) {
        var k = e.target.value;
        if (k === "__custom") {
          var title = (prompt("Name of the requirement:") || "").trim();
          if (!title) return render();
          var kind = (prompt("What kind? Type one of: upload, form, training, manager", "upload") || "").trim().toLowerCase();
          var type = { upload: "document", document: "document", form: "form", training: "training", manager: "manager" }[kind] || "manager";
          var key = "custom_" + title.toLowerCase().replace(/[^a-z0-9]+/g, "_").slice(0, 40) + "_" + Math.random().toString(36).slice(2, 6);
          items.push({ key: key, type: type, title: title, owner: type === "manager" ? "manager" : "employee", required: true, dueDays: 0, reminderDaysBefore: 2, dependsOn: [] });
        } else if (k && catalog[k]) {
          items.push(JSON.parse(JSON.stringify(catalog[k])));
        }
        return render();
      }
      var row = e.target.closest(".oe-row");
      if (!row) return;
      var it = items[Number(row.dataset.i)], f = e.target.dataset.f;
      if (f === "required") it.required = e.target.checked;
      else if (f === "dependsOn") it.dependsOn = e.target.value ? [e.target.value] : [];
      else if (f) it[f] = e.target.value === "" ? null : Number(e.target.value);
      render();
    });

    render();
    return { items: function () { return JSON.parse(JSON.stringify(items)); }, set: function (list) { items = JSON.parse(JSON.stringify(list || [])); render(); } };
  }

  /* styles live here so both pages match */
  var css = document.createElement("style");
  css.textContent =
    ".oe-group{margin-bottom:10px}.oe-group-title{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#6b7280;font-weight:600;margin:10px 0 4px}" +
    ".oe-row{display:flex;align-items:flex-start;gap:8px;padding:7px 8px;border:1px solid #e4e6eb;border-radius:3px;margin-bottom:4px;background:#fff}" +
    ".oe-move{display:flex;flex-direction:column;gap:1px}.oe-move button{font-size:8px;line-height:1;padding:2px 4px;color:#6b7280;border-radius:2px}.oe-move button:hover:not(:disabled){background:#eceef2}.oe-move button:disabled{opacity:.25}" +
    ".oe-main{flex:1;min-width:0}.oe-title{font-size:12.5px;font-weight:600;color:#0f1116}.oe-type{font-size:10.5px;font-weight:500;color:#8b909c;margin-left:4px}" +
    ".oe-controls{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:4px;font-size:11.5px;color:#4a505c;align-items:center}.oe-check,.oe-inline{display:inline-flex;align-items:center;gap:5px}" +
    ".oe-num{width:56px;border:1px solid #e4e6eb;border-radius:2px;padding:2px 6px;font-size:11.5px;background:#fff;color:#0f1116}.oe-hint{color:#8b909c}" +
    ".oe-sel{border:1px solid #e4e6eb;border-radius:2px;padding:3px 6px;font-size:11.5px;background:#fff;color:#0f1116;max-width:220px}" +
    ".oe-remove{font-size:11px;font-weight:600;color:#c83838;padding:2px 4px}.oe-add{margin-top:8px}" +
    "html[data-theme-resolved='dark'] .oe-row{background:#1a1d24;border-color:#2c313b}html[data-theme-resolved='dark'] .oe-title{color:#e7eaf2}" +
    "html[data-theme-resolved='dark'] .oe-num,html[data-theme-resolved='dark'] .oe-sel{background:#14181f;border-color:#2c313b;color:#e7eaf2}";
  document.head.appendChild(css);

  window.OnboardingEditor = { mount: mount, groupOf: groupOf, typeLabel: typeLabel, dueText: dueText, GROUPS: GROUPS };
})();
