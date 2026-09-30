/* Seasonal theme switch: Christmas (default) or Winter Wonderland, toggled by the snowman button in the top bar
 * (templates/_primary_nav.html). Sets <html data-season="winter">, which static/css/christmas.css reads, and decorates
 * the top bar: string lights for Christmas, falling snow for winter. Remembered per browser.
 *
 * Loaded in <head> right after brand.js so the saved season is painted before the page shows.
 */
(function () {
  "use strict";

  var KEY = "althais.season.v1", root = document.documentElement, deco = null, current = "christmas";

  function stored() {
    try { return localStorage.getItem(KEY) === "winter" ? "winter" : "christmas"; } catch (e) { return "christmas"; }
  }

  /* The top bar's decoration: string lights for Christmas, falling snow for winter. Both stay on the top bar
     (the lights hang just under it), so they never cover the work area. */
  var BULBS = ["#e8333d", "#f2c14e", "#3fb56a", "#4a9be8"];   /* red, gold, green, blue */

  function lights() {
    var el = document.createElement("div"), n = 36;
    el.className = "season-lights";
    for (var i = 0; i < n; i++) {
      var b = document.createElement("i");
      b.style.left = ((i + 0.5) / n * 100) + "%";
      b.style.setProperty("--c", BULBS[i % BULBS.length]);
      b.style.animationDelay = (-Math.random() * 3) + "s";
      el.appendChild(b);
    }
    return el;
  }

  function snowfall() {
    try { if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return null; } catch (e) {}
    var el = document.createElement("div");
    el.className = "season-snow";
    for (var i = 0; i < 28; i++) {
      var f = document.createElement("span");
      f.textContent = i % 3 ? "•" : "\u2744\uFE0E";   /* \uFE0E: plain glyph, not the color emoji */
      f.style.left = (Math.random() * 100) + "%";
      f.style.fontSize = (i % 3 ? 4 + Math.random() * 4 : 8 + Math.random() * 5) + "px";
      f.style.animationDuration = (3.5 + Math.random() * 4) + "s";
      f.style.animationDelay = (-Math.random() * 7) + "s";   /* negative: already mid-fall on load, no empty first seconds */
      f.style.setProperty("--drift", (Math.random() * 24 - 12) + "px");
      el.appendChild(f);
    }
    return el;
  }

  function decorate(season) {
    var bar = document.querySelector("header.bg-med-600");
    if (!bar || (deco && deco.getAttribute("data-for") === season)) return;
    if (deco) { deco.remove(); deco = null; }
    deco = season === "winter" ? snowfall() : lights();
    if (!deco) return;
    deco.setAttribute("data-for", season);
    deco.setAttribute("aria-hidden", "true");
    bar.appendChild(deco);
  }

  function label(season) {
    var next = season === "winter" ? "Switch to Christmas theme" : "Switch to Winter Wonderland theme";
    document.querySelectorAll("[data-season-toggle]").forEach(function (b) {
      b.setAttribute("aria-label", next); b.setAttribute("title", next);
      b.setAttribute("aria-pressed", season === "winter" ? "true" : "false");
    });
  }

  function paint(season) {
    current = season;
    if (season === "winter") root.setAttribute("data-season", "winter"); else root.removeAttribute("data-season");
    if (document.body) { label(season); decorate(season); }
  }

  function set(season) {
    try { localStorage.setItem(KEY, season); } catch (e) { /* storage blocked: still switch for this page */ }
    paint(season);
  }

  paint(stored());
  document.addEventListener("DOMContentLoaded", function () { paint(current); });
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-season-toggle]")) set(current === "winter" ? "christmas" : "winter");
  });
  window.addEventListener("storage", function (e) { if (e.key === KEY) paint(stored()); });   /* another tab changed it */

  window.AlthaisSeason = { current: function () { return current; }, set: set };
})();
