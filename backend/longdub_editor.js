/* Both editors keep their sticky page controls below the shared, wrapping account bar. */
(function () {
  'use strict';
  var header = document.getElementById('userBar');
  if (!header) return;
  function measure() {
    document.documentElement.style.setProperty('--ld-header-height', header.getBoundingClientRect().height + 'px');
  }
  measure();
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(measure).observe(header);
  else window.addEventListener('resize', measure);
})();
