/* Lisan AI: the "Short clip | Full video" switch under the top bar of the app and of the Long Dub page.
 *
 * A page puts <div id="modeSwitch" data-mode="short"></div> (or data-mode="full") under its top bar and loads this file.
 * The numbers in the captions come from /api/limits, the same ones the server enforces, so the switch can never promise
 * a length the upload would refuse. If that call fails the captions stay generic ("Quick dubs" / "Full-length dubs").
 * The language follows the page: Arabic when <html lang> is "ar" or the saved choice (lisan_lang) is Arabic, and the
 * switch redraws by itself when the page flips <html lang> (the app does that without reloading). */
(function () {
  "use strict";
  var host = document.getElementById("modeSwitch");
  if (!host) return;

  var mode = host.getAttribute("data-mode") === "full" ? "full" : "short";
  var limits = null;

  function lang() {
    var attr = document.documentElement.getAttribute("lang");
    if (attr === "ar") return "ar";
    try { if (localStorage.getItem("lisan_lang") === "ar" && attr !== "en") return "ar"; } catch (e) {}
    return "en";
  }

  function num(v) {
    v = Number(v);
    return isFinite(v) && v > 0 ? Math.round(v) : 0;
  }

  // Arabic counted nouns: 3-10 take the plural, everything else the singular ("5 ثوانٍ", "30 ثانية", "10 دقائق", "15 دقيقة").
  function arUnit(n, plural, singular) { return (n >= 3 && n <= 10) ? plural : singular; }

  function shortCaption(l) {
    var n = limits ? num(limits.short_max_sec) : 0;
    if (l === "ar") return n ? "حتى " + n + " " + arUnit(n, "ثوانٍ", "ثانية") : "دبلجة سريعة";
    return n ? "Up to " + n + " seconds" : "Quick dubs";
  }

  function fullCaption(l) {
    var n = limits ? num(limits.long_max_min) : 0;
    if (l === "ar") return n ? "حتى " + n + " " + arUnit(n, "دقائق", "دقيقة") : "دبلجة كاملة";
    return n ? "Up to " + n + " minutes" : "Full-length dubs";
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }

  function tab(key, href, icon, title, caption) {
    var inner = '<span class="ms-ico" aria-hidden="true">' + icon + '</span>' +
      '<span class="ms-txt"><b>' + esc(title) + '</b><small>' + esc(caption) + '</small></span>';
    if (key === mode) return '<div class="ms-tab on" data-mode="' + key + '" aria-current="page">' + inner + '</div>';
    return '<a class="ms-tab" data-mode="' + key + '" href="' + href + '">' + inner + '</a>';
  }

  function render() {
    var l = lang();
    host.className = "mode-switch";
    host.setAttribute("role", "navigation");
    host.setAttribute("aria-label", l === "ar" ? "نوع الدبلجة" : "Dubbing type");
    host.setAttribute("dir", l === "ar" ? "rtl" : "ltr");
    host.innerHTML =
      tab("short", "/app", "⚡", l === "ar" ? "مقطع قصير" : "Short clip", shortCaption(l)) +
      tab("full", "/dub-long", "🎞️", l === "ar" ? "فيديو كامل" : "Full video", fullCaption(l));
  }

  render();

  try {
    fetch("/api/limits", { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && typeof j === "object") { limits = j; render(); } })
      .catch(function () {});
  } catch (e) {}

  try {
    new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  } catch (e) {}
})();
