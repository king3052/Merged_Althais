/* Althais colour theme (Light / Dark / System). Loaded in the <head> so the page never flashes the wrong colours.
   Shares one setting (localStorage "althais.theme.v1") with the EMR's Settings > Appearance. */
(function () {
  "use strict";
  var KEY = "althais.theme.v1", root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try { var v = localStorage.getItem(KEY); if (v === "dark" || v === "light" || v === "system") return v; } catch (e) { /* storage blocked */ }
    return "light";
  }
  function resolve(mode) { return mode === "system" ? (mq && mq.matches ? "dark" : "light") : mode; }

  function paint(mode) {
    var resolved = resolve(mode);
    root.setAttribute("data-theme", mode);
    root.setAttribute("data-theme-resolved", resolved);
    if (document.body) {
      document.body.setAttribute("data-theme", mode);
      document.body.setAttribute("data-theme-resolved", resolved);
    }
    var dark = resolved === "dark";
    var toggles = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < toggles.length; i++) {
      toggles[i].setAttribute("aria-pressed", String(dark));
      toggles[i].setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      toggles[i].setAttribute("title", dark ? "Switch to light mode" : "Switch to dark mode");
    }
    try { window.dispatchEvent(new CustomEvent("althais-theme", { detail: { mode: mode, resolved: resolved } })); } catch (e) { /* old browser */ }
    return resolved;
  }
  function set(mode) {
    try { localStorage.setItem(KEY, mode); } catch (e) { /* storage blocked */ }
    return paint(mode);
  }

  window.AlthaisTheme = {
    mode: stored,
    resolved: function () { return resolve(stored()); },
    set: set,
    toggle: function () { return set(resolve(stored()) === "dark" ? "light" : "dark"); },
    sync: function () { return paint(stored()); }
  };

  paint(stored());
  document.addEventListener("DOMContentLoaded", function () {
    paint(stored());
    var toggles = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < toggles.length; i++) toggles[i].addEventListener("click", function () { window.AlthaisTheme.toggle(); });
  });
  if (mq && mq.addEventListener) mq.addEventListener("change", function () { if (stored() === "system") paint("system"); });
  window.addEventListener("storage", function (e) { if (e.key === KEY) paint(stored()); });   /* another tab changed it */
})();
