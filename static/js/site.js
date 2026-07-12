/* ══════════════════════════════════════════════════════════════════════
   Althais — shared site behavior (nav, reveal-on-scroll, modal, accordions,
   tabs, count-up, feature-card expand, newsletter/demo forms).
   Loaded on every marketing/product page via base.html.
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Sticky nav: transparent → frosted on scroll ─────────────────── */
  var nav = document.getElementById('site-nav');
  if (nav) {
    var threshold = nav.dataset.solidImmediately === 'true' ? 8 : 120;
    function onScroll() {
      if (window.scrollY > threshold) nav.classList.add('scrolled');
      else nav.classList.remove('scrolled');
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ── Trust marquee: keep looping content wide enough for any viewport ── */
  document.querySelectorAll('.trust-marquee-track').forEach(function (track) {
    var guard = 0;
    while (track.scrollWidth < window.innerWidth * 2 && guard < 6) {
      track.insertAdjacentHTML('beforeend', track.innerHTML);
      guard++;
    }
  });

  /* ── Mobile menu ──────────────────────────────────────────────────── */
  window.toggleMobileMenu = function () {
    var h = document.getElementById('hamburger');
    var m = document.getElementById('mobile-menu');
    if (h) h.classList.toggle('open');
    if (m) m.classList.toggle('open');
  };

  /* ── Reveal-on-scroll ─────────────────────────────────────────────── */
  var revealEls = document.querySelectorAll('.reveal, .reveal-scale, .flow-connector');
  if (revealEls.length) {
    if (reduceMotion) {
      revealEls.forEach(function (el) { el.classList.add('in-view'); });
    } else {
      var io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add('in-view');
              io.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.15, rootMargin: '0px 0px -8% 0px' }
      );
      revealEls.forEach(function (el) { io.observe(el); });
    }
  }

  /* ── Count-up numbers: <span data-countup="128000" data-countup-prefix="$" data-countup-suffix="+"> ── */
  var countEls = document.querySelectorAll('[data-countup]');
  if (countEls.length) {
    function animateCount(el) {
      var target = parseFloat(el.getAttribute('data-countup'));
      var prefix = el.getAttribute('data-countup-prefix') || '';
      var suffix = el.getAttribute('data-countup-suffix') || '';
      var decimals = parseInt(el.getAttribute('data-countup-decimals') || '0', 10);
      if (reduceMotion) {
        el.textContent = prefix + target.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
        return;
      }
      var duration = 1400;
      var start = null;
      function step(ts) {
        if (!start) start = ts;
        var progress = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        var value = target * eased;
        el.textContent = prefix + value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
        if (progress < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }
    var cio = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { animateCount(entry.target); cio.unobserve(entry.target); }
      });
    }, { threshold: 0.4 });
    countEls.forEach(function (el) { cio.observe(el); });
  }

  /* ── FAQ / generic accordions ─────────────────────────────────────── */
  document.addEventListener('click', function (e) {
    var q = e.target.closest('.faq-question');
    if (q) {
      var item = q.closest('.faq-item');
      var wasOpen = item.classList.contains('open');
      item.parentElement.querySelectorAll('.faq-item.open').forEach(function (o) {
        if (o !== item && o.hasAttribute('data-accordion-exclusive')) o.classList.remove('open');
      });
      item.classList.toggle('open', !wasOpen);
      q.setAttribute('aria-expanded', String(!wasOpen));
    }
  });

  /* ── Feature card expand (Product page) ──────────────────────────── */
  document.addEventListener('click', function (e) {
    var card = e.target.closest('[data-feature]');
    if (card) {
      var key = card.getAttribute('data-feature');
      var detail = document.querySelector('[data-feature-detail="' + key + '"]');
      var grid = card.closest('[data-feature-grid]');
      if (grid) {
        grid.querySelectorAll('.feature-detail.open').forEach(function (d) {
          if (d !== detail) d.classList.remove('open');
        });
        grid.querySelectorAll('[data-feature].active').forEach(function (c) {
          if (c !== card) c.classList.remove('active');
        });
      }
      if (detail) {
        var willOpen = !detail.classList.contains('open');
        detail.classList.toggle('open', willOpen);
        card.classList.toggle('active', willOpen);
        card.setAttribute('aria-expanded', String(willOpen));
      }
    }
  });

  /* ── Tabs: [data-tab] buttons toggle [data-tab-panel] panels sharing data-tab-group ── */
  document.addEventListener('click', function (e) {
    var tabBtn = e.target.closest('[data-tab]');
    if (tabBtn) {
      var group = tabBtn.getAttribute('data-tab-group');
      var id = tabBtn.getAttribute('data-tab');
      document.querySelectorAll('[data-tab-group="' + group + '"]').forEach(function (b) {
        b.classList.toggle('active', b === tabBtn);
      });
      document.querySelectorAll('[data-tab-panel-group="' + group + '"]').forEach(function (p) {
        p.classList.toggle('active', p.getAttribute('data-tab-panel') === id);
      });
    }
  });

  /* ── Book Demo modal (shared CTA across every page) ──────────────── */
  var bookModal = document.getElementById('book-demo-modal');
  if (bookModal) {
    var bookForm = document.getElementById('book-demo-form');
    var bdMsg = document.getElementById('book-demo-msg');
    var bdBtn = document.getElementById('bd_btn');
    var bdTitle = document.getElementById('book-demo-title');
    var bdSub = document.getElementById('book-demo-sub');

    function openBookModal(context) {
      bookModal.style.display = 'flex';
      document.body.style.overflow = 'hidden';
      if (context && bdTitle) bdTitle.textContent = context.title || 'See Althais in Action';
      if (context && bdSub) bdSub.textContent = context.sub || "Fill in your details and we'll be in touch.";
      var firstInput = document.getElementById('bd_full_name');
      if (firstInput) setTimeout(function () { firstInput.focus(); }, 150);
    }
    window.closeBookDemoModal = function () {
      bookModal.style.display = 'none';
      document.body.style.overflow = '';
    };
    document.addEventListener('click', function (e) {
      var trigger = e.target.closest('[data-open-demo-modal]');
      if (trigger) {
        e.preventDefault();
        openBookModal({ title: trigger.getAttribute('data-modal-title'), sub: trigger.getAttribute('data-modal-sub') });
      }
    });
    bookModal.addEventListener('click', function (e) { if (e.target === bookModal) window.closeBookDemoModal(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && bookModal.style.display === 'flex') window.closeBookDemoModal(); });

    function showBookDemoMsg(text) { bdMsg.textContent = text; bdMsg.style.display = 'block'; }

    if (bookForm) {
      bookForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        var name = document.getElementById('bd_full_name').value.trim();
        var email = document.getElementById('bd_email').value.trim();
        var practice = document.getElementById('bd_practice_name').value.trim();
        if (!name || !email || !practice) { showBookDemoMsg('Please fill in all required fields.'); return; }
        if (!email.includes('@')) { showBookDemoMsg('Enter a valid email address.'); return; }
        bdBtn.disabled = true; bdBtn.textContent = 'Sending…';
        var body = new URLSearchParams();
        body.set('full_name', name);
        body.set('email', email);
        body.set('practice_name', practice);
        body.set('phone', document.getElementById('bd_phone').value.trim());
        try {
          var res = await fetch('/request-demo', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body.toString() });
          var data = await res.json().catch(function () { return {}; });
          if (res.ok && data.ok) {
            document.getElementById('book-demo-form-wrap').style.display = 'none';
            document.getElementById('book-demo-success').style.display = 'block';
          } else {
            showBookDemoMsg(data.error || 'Something went wrong. Please try again.');
            bdBtn.disabled = false; bdBtn.textContent = 'Request Demo →';
          }
        } catch (err) {
          showBookDemoMsg('Network error. Please try again.');
          bdBtn.disabled = false; bdBtn.textContent = 'Request Demo →';
        }
      });
    }
  }

  /* ── Newsletter signup forms: <form data-newsletter-form> ────────── */
  document.querySelectorAll('[data-newsletter-form]').forEach(function (form) {
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      var input = form.querySelector('input[type="email"]');
      var msg = form.querySelector('[data-newsletter-msg]');
      var btn = form.querySelector('button[type="submit"]');
      var email = input.value.trim();
      if (!email.includes('@')) {
        if (msg) { msg.textContent = 'Enter a valid work email.'; msg.style.display = 'block'; msg.style.color = '#c23b3b'; }
        return;
      }
      var originalText = btn.textContent;
      btn.disabled = true; btn.textContent = 'Subscribing…';
      try {
        var body = new URLSearchParams(); body.set('email', email);
        var res = await fetch('/api/newsletter-signup', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body.toString() });
        var data = await res.json().catch(function () { return {}; });
        if (res.ok && data.ok) {
          form.querySelector('.newsletter-fields').style.display = 'none';
          if (msg) { msg.textContent = "You're subscribed. Watch your inbox."; msg.style.display = 'block'; msg.style.color = 'inherit'; }
        } else {
          if (msg) { msg.textContent = data.error || 'Something went wrong.'; msg.style.display = 'block'; msg.style.color = '#c23b3b'; }
          btn.disabled = false; btn.textContent = originalText;
        }
      } catch (err) {
        if (msg) { msg.textContent = 'Network error — please try again.'; msg.style.display = 'block'; msg.style.color = '#c23b3b'; }
        btn.disabled = false; btn.textContent = originalText;
      }
    });
  });

  /* ── Resource search + category filter ───────────────────────────── */
  var resSearch = document.getElementById('resource-search');
  var resGrid = document.getElementById('resource-grid');
  if (resSearch && resGrid) {
    var activeCategory = 'all';
    function applyFilters() {
      var q = resSearch.value.trim().toLowerCase();
      var cards = resGrid.querySelectorAll('[data-article]');
      var visibleCount = 0;
      cards.forEach(function (card) {
        var cat = card.getAttribute('data-category');
        var text = card.getAttribute('data-search') || '';
        var matchesCat = activeCategory === 'all' || cat === activeCategory;
        var matchesQuery = !q || text.indexOf(q) !== -1;
        var show = matchesCat && matchesQuery;
        card.style.display = show ? '' : 'none';
        if (show) visibleCount++;
      });
      var empty = document.getElementById('resource-empty');
      if (empty) empty.style.display = visibleCount === 0 ? 'block' : 'none';
    }
    resSearch.addEventListener('input', applyFilters);
    document.querySelectorAll('[data-resource-category]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        activeCategory = btn.getAttribute('data-resource-category');
        document.querySelectorAll('[data-resource-category]').forEach(function (b) { b.classList.toggle('active', b === btn); });
        applyFilters();
      });
    });
  }

  /* ── How-It-Works scroll story: active step + progress rail ──────── */
  var hiwSteps = document.querySelectorAll('.hiw-step');
  if (hiwSteps.length) {
    var dots = document.querySelectorAll('.hiw-progress-dot');
    var hiwIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var idx = parseInt(entry.target.getAttribute('data-step-index'), 10);
        if (entry.isIntersecting) {
          entry.target.classList.add('in-view');
          dots.forEach(function (d, i) { d.classList.toggle('active', i === idx); });
        }
      });
    }, { threshold: 0.45 });
    hiwSteps.forEach(function (s) { hiwIo.observe(s); });
  }

}());
