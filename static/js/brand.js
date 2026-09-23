/* Althais brand color, chosen in Settings > Appearance and shared by the whole practice.
 *
 * One color goes in; a full palette comes out as CSS variables on <html>, which every page's styles and the
 * Tailwind "med" color read (see templates/_head.html, static/css/brand-theme.css):
 *   --brand / --brand-700 / --brand-500 / --brand-200 / --brand-100 / --brand-50   surfaces, hover, tints
 *   --brand-text, --brand-text-strong    brand-colored text on white, darkened until it's readable (4.5:1, 7:1)
 *   --brand-on                           text on the brand color: white when that reads well, else a deep shade
 *   --brand-dark-*                       dark-mode header, links and tints
 * (plus -rgb channel versions, used as rgb(var(--brand-rgb) / .2) and by Tailwind opacity)
 *
 * Loaded in <head> right after theme.js so the saved color is painted before the page shows (from this
 * browser's copy), then refreshed from the server (GET /api/branding). Admins save with AlthaisBrand.save().
 */
(function () {
  "use strict";

  var DEFAULT = "#0d5bd7";   /* Althais blue */
  var PRESETS = [
    { name: "Althais Blue", hex: "#0d5bd7" }, { name: "Light Blue", hex: "#87cefa" }, { name: "Light Pink", hex: "#f7b6cb" },
    { name: "Seafoam Green", hex: "#93dfc0" }, { name: "Brown", hex: "#8b5e3c" }, { name: "Light Purple", hex: "#c9b3f5" },
    { name: "Black", hex: "#16161a" }, { name: "White", hex: "#ffffff" },
    { name: "Sage", hex: "#a8bfa0" }, { name: "Dusty Rose", hex: "#d4a5ae" }, { name: "Soft Peach", hex: "#f5c6a5" },
    { name: "Butter", hex: "#f2dea0" }, { name: "Periwinkle", hex: "#a9b7ec" }, { name: "Ocean Mist", hex: "#9cc5c9" },
    { name: "Navy", hex: "#1f3a5f" }, { name: "Forest", hex: "#2f5d50" }, { name: "Terracotta", hex: "#d18f76" }, { name: "Mauve", hex: "#b58db6" },
    { name: "Slate", hex: "#64748b" }, { name: "Stone", hex: "#c8bfb3" }
  ];
  var DARK_SURFACE = "#1a1d24";
  /* this browser's copy of the practice's color, so pages paint in it before the server answers */
  var CACHE_KEY = "althais.brand.v1";

  /* ---------- color math ---------- */
  function norm(hex) {
    var h = String(hex || "").trim().replace(/^#/, "").toLowerCase();
    if (/^[0-9a-f]{3}$/.test(h)) h = h.split("").map(function (c) { return c + c; }).join("");
    return /^[0-9a-f]{6}$/.test(h) ? "#" + h : null;
  }
  function rgb(hex) { var h = norm(hex).slice(1); return [0, 2, 4].map(function (i) { return parseInt(h.slice(i, i + 2), 16); }); }
  function toHex(r, g, b) { return "#" + [r, g, b].map(function (v) { return Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0"); }).join(""); }
  function hsl(hex) {
    var c = rgb(hex).map(function (v) { return v / 255; }), max = Math.max.apply(null, c), min = Math.min.apply(null, c), l = (max + min) / 2, h = 0, s = 0;
    if (max !== min) {
      var d = max - min; s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      h = max === c[0] ? (c[1] - c[2]) / d + (c[1] < c[2] ? 6 : 0) : max === c[1] ? (c[2] - c[0]) / d + 2 : (c[0] - c[1]) / d + 4;
      h *= 60;
    }
    return [h, s * 100, l * 100];
  }
  function fromHsl(h, s, l) {
    s = Math.max(0, Math.min(100, s)) / 100; l = Math.max(0, Math.min(100, l)) / 100;
    var k = function (n) { return (n + h / 30) % 12; }, a = s * Math.min(l, 1 - l);
    var f = function (n) { return l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1))); };
    return toHex(f(0) * 255, f(8) * 255, f(4) * 255);
  }
  function lum(hex) { return rgb(hex).map(function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }).reduce(function (s, v, i) { return s + v * [0.2126, 0.7152, 0.0722][i]; }, 0); }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  /* walk lightness up or down from `start` until `test` passes */
  function seek(h, s, start, step, test) { for (var l = start; l >= 0 && l <= 100; l += step) { var c = fromHsl(h, s, l); if (test(c)) return c; } return fromHsl(h, s, step < 0 ? 0 : 100); }
  function channels(hex) { return rgb(hex).join(" "); }   /* space-separated, for rgb(var(--x) / alpha) and Tailwind */

  function palette(input) {
    var base = norm(input) || DEFAULT, p = hsl(base), H = p[0], S = p[1], L = p[2];
    var gray = S < 10;   /* black, white and grays: keep every derived shade neutral instead of inventing a hue */
    var sat = function (x) { return gray ? 0 : x; };
    var soft = sat(Math.min(S, 90));
    var whiteOk = contrast(base, "#ffffff") >= 4.5;
    var deep = seek(H, sat(Math.max(S, 35)), 20, -1, function (c) { return contrast(c, base) >= 7; });
    var on = whiteOk || contrast(base, "#ffffff") >= contrast(base, deep) ? "#ffffff" : deep;
    var hover = fromHsl(H, S, L + (whiteOk ? -7 : -6));
    var v = {
      "--brand": base, "--brand-700": hover, "--brand-500": fromHsl(H, S, Math.min(L + 8, 88)),
      "--brand-200": fromHsl(H, soft, 88), "--brand-100": fromHsl(H, soft, 94), "--brand-50": fromHsl(H, soft, 97.5),
      "--brand-text": seek(H, sat(Math.max(S, 45)), Math.min(L, 50), -1, function (c) { return contrast(c, "#ffffff") >= 4.5; }),
      "--brand-text-strong": seek(H, sat(Math.max(S, 45)), Math.min(L, 42), -1, function (c) { return contrast(c, "#ffffff") >= 7; }),
      "--brand-on": on,
      "--brand-glass": on === "#ffffff" ? "rgba(255, 255, 255, .08)" : "rgba(255, 255, 255, .45)",
      "--brand-glass-hover": on === "#ffffff" ? "rgba(255, 255, 255, .16)" : "rgba(255, 255, 255, .7)",
      /* dark mode is deliberately muted: lower saturation everywhere, so no color glows against the dark UI */
      "--brand-dark-header": seek(H, sat(Math.min(S * 0.5, 30)), Math.min(L, 36), -1, function (c) { return contrast(c, "#ffffff") >= 5; }),
      "--brand-dark-link": seek(H, sat(Math.min(S * 0.7, 48)), Math.max(Math.min(L, 72), 58), 1, function (c) { return contrast(c, DARK_SURFACE) >= 7; }),
      "--brand-dark-tint": fromHsl(H, sat(Math.min(S, 22)), 15),
      "--brand-dark-border": fromHsl(H, sat(Math.min(S, 24)), 32),
      /* a near-white brand would vanish against white pages: outline its buttons and underline the top bar */
      "--brand-ring": contrast(base, "#ffffff") < 1.35 ? "inset 0 0 0 1px rgba(15, 17, 22, .14)" : "none",
      "--brand-bar-line": contrast(base, "#ffffff") < 1.35 ? "1px solid #e4e6eb" : "0 solid transparent"
    };
    /* Dark-mode top bar. Normally a deep shade with white text, but yellows and oranges turn muddy brown when
       darkened that far, so a light warm brand keeps its own color (dimmed a little) with dark text instead. */
    var warmLight = !gray && !whiteOk && H >= 15 && H <= 75;
    if (warmLight) {
      v["--brand-dark-header"] = fromHsl(H, Math.min(S * 0.55, 42), Math.max(L - 14, 52));
      v["--brand-dark-on"] = seek(H, Math.max(S, 35), 20, -1, function (c) { return contrast(c, v["--brand-dark-header"]) >= 7; });
    } else {
      v["--brand-dark-on"] = "#ffffff";
    }
    v["--brand-dark-on-rgb"] = channels(v["--brand-dark-on"]);
    v["--brand-dark-glass"] = warmLight ? "rgba(255, 255, 255, .35)" : "rgba(255, 255, 255, .10)";
    v["--brand-dark-glass-hover"] = warmLight ? "rgba(255, 255, 255, .55)" : "rgba(255, 255, 255, .18)";
    /* marks drawn in the brand color on white (Althea's logo, the active tab underline) need to stay visible */
    var cw = contrast(base, "#ffffff");
    v["--brand-mark"] = cw < 1.15 ? v["--brand-text"]                          /* white and near-white: use the gray text shade */
                      : cw < 1.6 ? fromHsl(H, S, Math.max(L - 14, 45))           /* pale colors (butter, stone): a deeper tone of the same color */
                      : base;
    /* buttons and highlights in dark mode: a softened brand with its own readable text */
    var dk = fromHsl(H, sat(Math.min(S * 0.6, 42)), Math.max(Math.min(L, 60), 40));
    var dkDeep = seek(H, sat(Math.max(S, 30)), 18, -1, function (c) { return contrast(c, dk) >= 7; });
    var dkOn = contrast(dk, "#ffffff") >= 4.5 ? "#ffffff" : (contrast(dk, "#ffffff") >= contrast(dk, dkDeep) ? "#ffffff" : dkDeep);
    v["--brand-dk"] = dk; v["--brand-dk-700"] = fromHsl(hsl(dk)[0], hsl(dk)[1], hsl(dk)[2] - 5); v["--brand-dk-on"] = dkOn;
    ["--brand-dk", "--brand-dk-700", "--brand-dk-on"].forEach(function (k) { v[k + "-rgb"] = channels(v[k]); });
    v["--brand-dark-link-soft"] = fromHsl(hsl(v["--brand-dark-link"])[0], hsl(v["--brand-dark-link"])[1], Math.min(hsl(v["--brand-dark-link"])[2] + 8, 92));
    ["--brand", "--brand-700", "--brand-500", "--brand-100", "--brand-50", "--brand-on"].forEach(function (k) { v[k + "-rgb"] = channels(v[k]); });
    return v;
  }

  function apply(hex) {
    var v = palette(hex), st = document.documentElement.style;
    Object.keys(v).forEach(function (k) { st.setProperty(k, v[k]); });
    current = v["--brand"];
    try { window.dispatchEvent(new CustomEvent("althais-brand", { detail: { hex: current } })); } catch (e) {}
    return current;
  }

  var current = DEFAULT, serverDoc = null, editable = false;
  var cached = null;
  try { cached = norm(localStorage.getItem(CACHE_KEY)); } catch (e) {}
  apply(cached || DEFAULT);

  /* the practice's saved color, shared across logins */
  var loaded = fetch("/api/branding", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; })   /* signed out: keep the cached color */
    .then(function (res) {
      if (!res) return current;
      serverDoc = res.data || { rev: 0 }; editable = !!res.can_edit;
      var hex = norm(serverDoc.color) || DEFAULT;
      try { localStorage.setItem(CACHE_KEY, hex); } catch (e) {}
      if (hex !== current) apply(hex);
      return hex;
    }).catch(function () { return current; });

  function save(hex) {
    var h = norm(hex);
    if (!h) return Promise.reject(new Error("That isn’t a valid color."));
    return loaded.then(function () {
      function attempt(doc, retries) {
        var body = JSON.parse(JSON.stringify(doc || { rev: 0 }));
        var user = window.__ALTHAIS_USER__ || {};
        body.color = h; body.updatedBy = user.full_name || user.email || ""; body.updatedAt = new Date().toISOString();
        return fetch("/api/branding", { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
          .then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }); })
          .then(function (res) {
            if (res.status === 200) { serverDoc = res.body.data; try { localStorage.setItem(CACHE_KEY, h); } catch (e) {} apply(h); return h; }
            if (res.status === 409 && retries > 0) return attempt(res.body.data, retries - 1);
            throw new Error((res.body && res.body.error) || "Could not save the brand color.");
          });
      }
      return attempt(serverDoc, 2);
    });
  }

  window.AlthaisBrand = {
    DEFAULT: DEFAULT, PRESETS: PRESETS,
    palette: palette, apply: apply, save: save, contrast: contrast, norm: norm,
    current: function () { return current; },
    ready: function () { return loaded; },
    canEdit: function () { return editable; },
    savedBy: function () { return serverDoc && serverDoc.updatedBy ? { by: serverDoc.updatedBy, at: serverDoc.updatedAt } : null; }
  };
})();
