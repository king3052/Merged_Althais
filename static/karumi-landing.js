(function () {
  "use strict";

  // Setting the `.hidden` IDL property doesn't reflect onto the `hidden`
  // content attribute for SVG elements in every browser, so the lucide-
  // rendered icons never actually hide via the property alone. Toggle the
  // attribute directly everywhere so it works for both HTML and SVG nodes.
  function setHidden(el, value) {
    if (value) el.setAttribute("hidden", "");
    else el.removeAttribute("hidden");
  }

  function setupTabs(root, opts) {
    var buttons = root.querySelectorAll(opts.buttonSelector);
    var panels = root.querySelectorAll(opts.panelSelector);
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var index = btn.getAttribute(opts.buttonAttr);
        buttons.forEach(function (b) {
          var active = b === btn;
          b.classList.toggle("active", active);
          if (b.hasAttribute("aria-selected")) b.setAttribute("aria-selected", String(active));
        });
        panels.forEach(function (p) {
          setHidden(p, p.getAttribute(opts.panelAttr) !== index);
        });
      });
    });
  }

  function init() {
    var landing = document.getElementById("karumi-landing");
    if (!landing) return;

    // Render icons first — the mobile-menu toggle below grabs live references
    // to the rendered <svg> nodes, so it must run after <i data-lucide> is replaced.
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }

    // Mobile nav toggle
    var menuBtn = landing.querySelector("#mobile-menu-btn");
    var menuPop = landing.querySelector("#mobile-pop");
    var iconMenu = landing.querySelector("#mobile-menu-icon");
    var iconClose = landing.querySelector("#mobile-close-icon");
    if (menuBtn && menuPop) {
      var closeMenu = function () {
        setHidden(menuPop, true);
        menuBtn.setAttribute("aria-expanded", "false");
        if (iconMenu) setHidden(iconMenu, false);
        if (iconClose) setHidden(iconClose, true);
      };
      menuBtn.addEventListener("click", function () {
        var isOpen = !menuPop.hasAttribute("hidden");
        setHidden(menuPop, isOpen);
        menuBtn.setAttribute("aria-expanded", String(!isOpen));
        if (iconMenu) setHidden(iconMenu, !isOpen);
        if (iconClose) setHidden(iconClose, isOpen);
      });
      menuPop.querySelectorAll("a").forEach(function (a) {
        a.addEventListener("click", closeMenu);
      });
    }

    // Use-cases tabs (Clinical Documentation / Medical Coding / Revenue Cycle)
    setupTabs(landing, {
      buttonSelector: ".case-tabs button",
      panelSelector: "[data-case-panel]",
      buttonAttr: "data-case-index",
      panelAttr: "data-case-panel",
    });

    // How-it-works tabs
    setupTabs(landing, {
      buttonSelector: ".how-tabs button",
      panelSelector: "[data-how-panel]",
      buttonAttr: "data-how-index",
      panelAttr: "data-how-panel",
    });

    // Testimonial tabs
    setupTabs(landing, {
      buttonSelector: ".testimonial-tabs button",
      panelSelector: "[data-testimonial-panel]",
      buttonAttr: "data-testimonial-index",
      panelAttr: "data-testimonial-panel",
    });

    // Althea "context sources" tabs — visual only, no content swap in the source design
    var sourceButtons = landing.querySelectorAll(".source-tabs button");
    sourceButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        sourceButtons.forEach(function (b) { b.classList.toggle("active", b === btn); });
      });
    });

    // Hero headline word rotator ("...to reimbursement." cycles through related words)
    var rotatingWord = landing.querySelector("#hero-rotating-word");
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (rotatingWord && !reduceMotion) {
      var words = ["revenue", "reimbursement", "cash flow", "profit", "clarity"];
      var wordIndex = 0;
      setInterval(function () {
        rotatingWord.style.opacity = "0";
        setTimeout(function () {
          wordIndex = (wordIndex + 1) % words.length;
          rotatingWord.textContent = words[wordIndex];
          rotatingWord.style.opacity = "1";
        }, 350);
      }, 2400);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
