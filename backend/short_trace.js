/* Lisan AI: where people stop in the short dub (1.104.0, see short_trace.py).
 *
 * Loaded by the short dub page (/app) after app.js and dialogs.js. It sends small notes to /api/trace:
 *   open    - the page was opened
 *   step    - a step (card) of the page appeared, by its id (step1Card, editorSection, generateSection, ...)
 *   msg     - a pop-up message the app showed (our own words, from notify() in app.js)
 *   dialog  - a message box the app showed (LisanDialog.alert / confirm)
 *   jserror - a browser error on the page (the message only)
 *   leave   - the page was closed or left in the background, with the last step and the seconds spent
 * Nothing the person wrote or uploaded is sent. Only for logged-in accounts (the server refuses the rest).
 * Never breaks the page: every part is wrapped, and it gives up quietly when the server does not answer. */
(function () {
  "use strict";
  if (window.lisanTrace) return;

  var STEPS = ["step1Card", "step1_5Card", "progressSection", "editorSection", "voicesSection", "cloneAnalysisSection",
               "speakerVoicesSection", "generateSection", "resultSection", "timelineSection", "mergeSection", "lipsyncSection"];
  var MAX_NOTES = 300, queue = [], sent = 0, timer = null, lastStep = "", started = Date.now(), dead = false, leftSent = false;

  function job() {
    try { return (typeof currentJobId !== "undefined" && currentJobId) ? String(currentJobId) : ""; } catch (e) { return ""; }
  }

  function push(k, s, t, l, d) {
    if (dead || sent >= MAX_NOTES) return;
    var ev = { k: k, s: s || lastStep || "", t: String(t == null ? "" : t).slice(0, 300), l: l || "", j: job() };
    if (d) ev.d = d;
    queue.push(ev); sent++;
    if (!timer) timer = setTimeout(flush, 3000);
  }

  function flush(beacon) {
    timer = null;
    if (!queue.length || dead) return;
    var body = JSON.stringify({ events: queue.splice(0, 40) });
    try {
      if (beacon && navigator.sendBeacon) {
        navigator.sendBeacon("/api/trace", new Blob([body], { type: "application/json" }));
      } else {
        fetch("/api/trace", { method: "POST", headers: { "Content-Type": "application/json" }, body: body, keepalive: true })
          .then(function (r) { if (r.status === 401 || r.status === 404) dead = true; })
          .catch(function () {});
      }
    } catch (e) {}
    if (queue.length && !beacon) timer = setTimeout(flush, 1000);
  }

  function visible(el) { return el && !el.classList.contains("hidden") && el.style.display !== "none"; }

  function watchSteps() {
    var seen = {};
    function check() {
      STEPS.forEach(function (id) {
        var el = document.getElementById(id);
        var on = visible(el);
        if (on && !seen[id]) { lastStep = id; push("step", id); }
        seen[id] = on;
      });
    }
    check();
    try {
      var mo = new MutationObserver(function () { check(); });
      STEPS.forEach(function (id) {
        var el = document.getElementById(id);
        if (el) mo.observe(el, { attributes: true, attributeFilter: ["class", "style"] });
      });
    } catch (e) {}
  }

  function wrapDialogs() {
    var D = window.LisanDialog;
    if (!D) return;
    ["alert", "confirm"].forEach(function (name) {
      var orig = D[name];
      if (typeof orig !== "function") return;
      D[name] = function (message, opts) {
        try { push("dialog", "", message, (opts && opts.type) || (opts && opts.danger ? "warning" : "info")); } catch (e) {}
        return orig.apply(this, arguments);
      };
    });
  }

  function left() {
    if (leftSent) return;
    leftSent = true;
    push("leave", lastStep, "", "", Math.round((Date.now() - started) / 1000));
    flush(true);
  }

  window.lisanTrace = { msg: function (type, text) { push("msg", "", text, type); } };

  try {
    push("open", "", location.pathname);
    watchSteps();
    wrapDialogs();
    window.addEventListener("error", function (e) { push("jserror", "", (e && e.message) || "error"); });
    window.addEventListener("pagehide", left);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") left();
      else leftSent = false;               // back again: a later leave is noted too
    });
  } catch (e) {}
})();
