/* Lisan AI: shared script of the public pages (Login, Help, Terms, Privacy).
   1. Follows the app's light/dark choice (the same "lisan_dark_mode" setting the app uses; dark unless the person chose light).
   2. Makes the "Back" link go back to the APP for a signed-in person (and to the home page for a visitor). */
(function () {
  var dark = true;
  try { dark = localStorage.getItem("lisan_dark_mode") !== "0"; } catch (e) {}
  if (!dark) document.documentElement.classList.add("light");          // set before the page paints, so there is no flash
  document.addEventListener("DOMContentLoaded", function () {
    if (dark) document.body.classList.add("dark");                      // lets the chat bubble use its dark look too
    var a = document.getElementById("siteBack");
    if (!a) return;
    fetch("/api/auth/check", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (r.ok) { a.href = "/app"; a.textContent = a.getAttribute("data-app") || a.textContent; }
    }).catch(function () {});
  });
})();
