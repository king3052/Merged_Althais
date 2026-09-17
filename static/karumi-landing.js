(function () {
  "use strict";

  function setupTabs(root, opts) {
    var buttons = root.querySelectorAll(opts.buttonSelector);
    var panels = root.querySelectorAll(opts.panelSelector);
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var index = btn.getAttribute(opts.dataAttr);
        buttons.forEach(function (b) {
          var active = b === btn;
          b.classList.toggle("active", active);
          if (b.hasAttribute("aria-selected")) b.setAttribute("aria-selected", String(active));
        });
        panels.forEach(function (p) {
          p.hidden = p.getAttribute(opts.dataAttr) !== index;
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
        menuPop.hidden = true;
        menuBtn.setAttribute("aria-expanded", "false");
        if (iconMenu) iconMenu.hidden = false;
        if (iconClose) iconClose.hidden = true;
      };
      menuBtn.addEventListener("click", function () {
        var isOpen = !menuPop.hidden;
        menuPop.hidden = isOpen;
        menuBtn.setAttribute("aria-expanded", String(!isOpen));
        if (iconMenu) iconMenu.hidden = !isOpen;
        if (iconClose) iconClose.hidden = isOpen;
      });
      menuPop.querySelectorAll("a").forEach(function (a) {
        a.addEventListener("click", closeMenu);
      });
    }

    // Use-cases tabs (Clinical Documentation / Medical Coding / Revenue Cycle)
    setupTabs(landing, {
      buttonSelector: ".case-tabs button",
      panelSelector: "[data-case-panel]",
      dataAttr: "data-case-index",
    });

    // How-it-works tabs
    setupTabs(landing, {
      buttonSelector: ".how-tabs button",
      panelSelector: "[data-how-panel]",
      dataAttr: "data-how-index",
    });

    // Testimonial tabs
    setupTabs(landing, {
      buttonSelector: ".testimonial-tabs button",
      panelSelector: "[data-testimonial-panel]",
      dataAttr: "data-testimonial-index",
    });

    // Althea "context sources" tabs — visual only, no content swap in the source design
    var sourceButtons = landing.querySelectorAll(".source-tabs button");
    sourceButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        sourceButtons.forEach(function (b) { b.classList.toggle("active", b === btn); });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
