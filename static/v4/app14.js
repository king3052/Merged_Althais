/* v4 working preview, part 14: Revenue › Lost Money and Revenue › Recovery. The pages themselves are lost_money.py and
   recovery.py (server-drawn); here they sit under the app's header, in a frame that fills the rest of the window. The
   frame follows the app's light/dark setting and clinic color. */
(function () {
  var app = V.app;
  function framed(key, src) {
    return function () {
      app.innerHTML = U.header(key) + '<iframe id="v4frame" title="' + U.esc(key) + '" src="' + src + '" style="display:block;width:100%;border:0;background:var(--bg)"></iframe>';
      var f = document.getElementById('v4frame');
      var size = function () { f.style.height = Math.max(400, window.innerHeight - f.getBoundingClientRect().top) + 'px'; };
      var look = function () {
        try {
          var d = f.contentDocument && f.contentDocument.documentElement, h = document.documentElement;
          if (!d) return;
          var t = h.getAttribute('data-theme-resolved') || document.body.getAttribute('data-theme-resolved');
          if (t && d.getAttribute('data-theme-resolved') !== t) d.setAttribute('data-theme-resolved', t);
          var b = getComputedStyle(h).getPropertyValue('--brand').trim();
          if (b && d.style.getPropertyValue('--brand') !== b) d.style.setProperty('--brand', b);
        } catch (e) { /* not ours to style */ }
      };
      size(); window.addEventListener('resize', size);
      f.addEventListener('load', look); setInterval(look, 700);   // the report replaces the frame's page when it's built
    };
  }
  V.pages.lostmoney = framed('lostmoney', '/lost-money');
  V.pages.recovery = framed('recovery', '/recovery');
})();
