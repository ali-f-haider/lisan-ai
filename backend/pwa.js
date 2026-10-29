// Lisan AI as a computer app (installable web app). Only on a computer-sized screen with a mouse: the editor needs a big
// screen, so phones and touch tablets are not offered "Install". Nothing here changes how the site works in a browser tab.
(function () {
    try {
        if (!window.matchMedia || !window.matchMedia("(min-width: 900px) and (pointer: fine)").matches) return;
        if (!document.querySelector('link[rel="manifest"]')) {
            var link = document.createElement("link");
            link.rel = "manifest";
            link.href = "/manifest.webmanifest";
            link.crossOrigin = "use-credentials";      // the access-code cookie goes with it while the site is closed to the public
            document.head.appendChild(link);
        }
        if ("serviceWorker" in navigator) {
            navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
        }
    } catch (e) { /* the site works the same without it */ }
})();
