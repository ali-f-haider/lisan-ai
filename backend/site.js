/* Lisan AI: shared script of the public pages (Login, Pricing, Help, Terms, Privacy).
   1. Follows the app's light/dark choice (the same "lisan_dark_mode" setting the app uses; dark unless the person chose light).
   2. Follows the interface language (the same "lisan_lang" setting as the home page and the app): sets <html lang dir data-lang>
      before the page paints, shows only the .en or only the .ar parts of a page, adds the EN / العربية switch to the top bar,
      and uses the page's data-title-en / data-title-ar for the tab title. Pages can listen for the "lisan-lang" event.
   3. Makes the "Back" link go back to the APP for a signed-in person (and to the home page for a visitor).
   4. Fills every [data-limit] with the limit the server really enforces (from /api/limits); the text written in the page is the
      fallback if the numbers cannot be loaded. */
(function () {
  var root = document.documentElement;
  var dark = true;
  try { dark = localStorage.getItem("lisan_dark_mode") !== "0"; } catch (e) {}
  if (!dark) root.classList.add("light");                               // set before the page paints, so there is no flash

  function stored() {
    try { return localStorage.getItem("lisan_lang") === "ar" ? "ar" : "en"; } catch (e) { return "en"; }
  }
  function lang() { return root.getAttribute("data-lang") === "ar" ? "ar" : "en"; }
  window.lisanLang = lang;

  function apply(l, save) {
    root.setAttribute("data-lang", l); root.lang = l; root.dir = l === "ar" ? "rtl" : "ltr";
    if (save) { try { localStorage.setItem("lisan_lang", l); } catch (e) {} }
    var title = root.getAttribute("data-title-" + l);
    if (title) document.title = title;
    var btns = document.querySelectorAll("[data-set-lang]");
    for (var i = 0; i < btns.length; i++) btns[i].setAttribute("aria-pressed", btns[i].getAttribute("data-set-lang") === l ? "true" : "false");
    try { window.dispatchEvent(new CustomEvent("lisan-lang", { detail: { lang: l } })); } catch (e) {}
  }
  apply(stored(), false);                                               // before paint; nothing is written on a first visit

  document.addEventListener("DOMContentLoaded", function () {
    if (dark) document.body.classList.add("dark");                      // lets the chat bubble use its dark look too

    // the language switch, in the top bar of every page that loads this file
    var bar = document.querySelector(".site-nav .in");
    if (bar && !bar.querySelector(".site-lang")) {
      var box = document.createElement("div");
      box.className = "site-lang"; box.setAttribute("role", "group"); box.setAttribute("aria-label", "Language / اللغة");
      box.innerHTML = '<button type="button" data-set-lang="en">EN</button><button type="button" data-set-lang="ar" lang="ar">العربية</button>';
      var before = bar.querySelector(".back") || bar.querySelector(".links");
      if (before) bar.insertBefore(box, before); else bar.appendChild(box);
    }
    var sw = document.querySelectorAll("[data-set-lang]");
    for (var i = 0; i < sw.length; i++) {
      (function (b) { b.addEventListener("click", function () { apply(b.getAttribute("data-set-lang"), true); }); })(sw[i]);
    }
    apply(lang(), false);                                               // reflect the choice on the buttons just added

    var a = document.getElementById("siteBack");
    if (a) {
      fetch("/api/auth/check", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
        if (!r.ok) return;
        a.href = "/app";
        var parts = a.querySelectorAll("[data-app]");
        if (parts.length) { for (var j = 0; j < parts.length; j++) parts[j].textContent = parts[j].getAttribute("data-app"); }
        else if (a.getAttribute("data-app")) a.textContent = a.getAttribute("data-app");
      }).catch(function () {});
    }

    var spots = document.querySelectorAll("[data-limit]");
    if (spots.length) {
      fetch("/api/limits", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d) return;
        for (var k = 0; k < spots.length; k++) {
          var v = d[spots[k].getAttribute("data-limit")];
          if (v === undefined || v === null || v === "") continue;
          var txt = String(v);
          if (spots[k].hasAttribute("data-ar-digits")) txt = txt.replace(/[0-9]/g, function (c) { return "٠١٢٣٤٥٦٧٨٩".charAt(+c); });
          spots[k].textContent = txt;
        }
      }).catch(function () {});
    }
  });
})();
