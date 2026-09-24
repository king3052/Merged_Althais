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
  /* Every color the Althais admin can offer clinics (admin console > Settings > Appearance). Which ones a clinic
     actually sees is chosen there; out of the box it's PRESETS above. */
  var LIBRARY = [
    { group: "Blues", colors: [["Althais Blue", "#0d5bd7"], ["Light Blue", "#87cefa"], ["Sky", "#7cc4f4"], ["Cornflower", "#6495ed"], ["Azure", "#2f80ed"], ["Royal Blue", "#2b50c8"], ["Cobalt", "#1e4fa3"], ["Navy", "#1f3a5f"], ["Midnight", "#1b2440"], ["Periwinkle", "#a9b7ec"], ["Powder Blue", "#b8d4ea"], ["Steel Blue", "#4a7fa7"]] },
    { group: "Teals & Greens", colors: [["Ocean Mist", "#9cc5c9"], ["Teal", "#1f8a8a"], ["Aqua", "#5cc8c8"], ["Seafoam Green", "#93dfc0"], ["Mint", "#a8e6cf"], ["Emerald", "#1f9d6b"], ["Jade", "#2e8b6f"], ["Sage", "#a8bfa0"], ["Olive", "#7a8450"], ["Moss", "#5f7a4a"], ["Forest", "#2f5d50"], ["Pine", "#1f4a3d"]] },
    { group: "Purples", colors: [["Light Purple", "#c9b3f5"], ["Lavender", "#b9a7e8"], ["Lilac", "#cda8d8"], ["Violet", "#7b5cd6"], ["Amethyst", "#8e5bb5"], ["Plum", "#7a3f6f"], ["Mauve", "#b58db6"], ["Grape", "#5b3a8e"], ["Indigo", "#3f3d9e"]] },
    { group: "Pinks & Reds", colors: [["Light Pink", "#f7b6cb"], ["Blush", "#f2c4ce"], ["Dusty Rose", "#d4a5ae"], ["Rose", "#e0607e"], ["Raspberry", "#c2336b"], ["Coral", "#f08a7a"], ["Salmon", "#f4a08c"], ["Cherry", "#c8323c"], ["Crimson", "#a8203a"], ["Wine", "#6e2233"]] },
    { group: "Oranges & Yellows", colors: [["Soft Peach", "#f5c6a5"], ["Apricot", "#f7b27a"], ["Tangerine", "#f28c38"], ["Burnt Orange", "#c8612c"], ["Terracotta", "#d18f76"], ["Butter", "#f2dea0"], ["Lemon", "#f4e27a"], ["Marigold", "#e8b23a"], ["Mustard", "#c99a2e"], ["Gold", "#b8912f"]] },
    { group: "Browns & Neutrals", colors: [["Brown", "#8b5e3c"], ["Chocolate", "#5c3a24"], ["Caramel", "#b07a4a"], ["Sand", "#dcc7a1"], ["Stone", "#c8bfb3"], ["Taupe", "#9a8b7a"], ["Warm Gray", "#a39e98"], ["Slate", "#64748b"], ["Charcoal", "#3a3f47"], ["Silver", "#b8bec8"]] },
    { group: "Black & White", colors: [["Black", "#16161a"], ["Graphite", "#2a2c31"], ["White", "#ffffff"], ["Ivory", "#f7f3e8"]] }
  ];
  function nameOf(hex) {
    var h = String(hex || "").toLowerCase();
    for (var g = 0; g < LIBRARY.length; g++) for (var i = 0; i < LIBRARY[g].colors.length; i++) if (LIBRARY[g].colors[i][1] === h) return LIBRARY[g].colors[i][0];
    for (var j = 0; j < PRESETS.length; j++) if (PRESETS[j].hex.toLowerCase() === h) return PRESETS[j].name;
    return h;
  }
  /* the colors this clinic may choose from today, and whether it may also type its own */
  function offered() {
    return fetch("/api/branding/palette", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var list = d && Array.isArray(d.colors) && d.colors.length ? d.colors : PRESETS.map(function (p) { return p.hex; });
        return { colors: list.map(function (h) { return { name: nameOf(h), hex: h.toLowerCase() }; }), custom: !d || d.custom !== false };
      })
      .catch(function () { return { colors: PRESETS.map(function (p) { return { name: p.name, hex: p.hex.toLowerCase() }; }), custom: true }; });
  }
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
  var cached = null, isAdmin = /^\/admin(\/|$)/.test(location.pathname);
  /* the Althais admin console isn't a clinic: its own color, never the last clinic's color on this browser */
  try { cached = norm(localStorage.getItem(isAdmin ? "althais.admin_brand.v1" : CACHE_KEY)); } catch (e) {}   /* admin has its own color (admin Settings > Appearance) */
  apply(cached || DEFAULT);

  /* the practice's saved color, shared across logins */
  var loaded = (isAdmin ? Promise.resolve(null)
      : fetch("/api/branding", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }))   /* signed out: keep the cached color */
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
    DEFAULT: DEFAULT, PRESETS: PRESETS, LIBRARY: LIBRARY, nameOf: nameOf, offered: offered,
    palette: palette, apply: apply, save: save, contrast: contrast, norm: norm,
    current: function () { return current; },
    ready: function () { return loaded; },
    canEdit: function () { return editable; },
    savedBy: function () { return serverDoc && serverDoc.updatedBy ? { by: serverDoc.updatedBy, at: serverDoc.updatedAt } : null; }
  };
})();
