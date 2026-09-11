from pathlib import Path

ap = Path("app.js"); t = ap.read_text(encoding="utf-8")

V5 = r'''
// ===== UI UPDATE v5: remove stray lock-all button beside Step 2 title, keep the header one =====
(function () {
    if (window._uiV5) return; window._uiV5 = true;
    function isStrayLock(b) {
        if (!b || b.tagName !== "BUTTON") return false;
        if (b.closest("#segmentsTable thead")) return false;           // keep the header one
        var id = (b.id || "") + " " + (b.className || "");
        var tx = (b.textContent || "").trim();
        var ti = (b.title || "");
        return (/lockAll/i.test(id)) || ((tx === "\uD83D\uDD13" || tx === "\uD83D\uDD12") && /lock/i.test(ti));
    }
    function killStray() {
        document.querySelectorAll("button").forEach(function (b) { if (isStrayLock(b)) b.remove(); });
    }
    // Block the old injector at the door: swallow buttons appended to the Step 2 heading
    function shieldHeading() {
        var h = document.querySelector("#editorSection h3");
        if (h && !h._lockShield) {
            h._lockShield = true;
            var orig = h.appendChild.bind(h);
            h.appendChild = function (n) { if (n && n.tagName === "BUTTON") return n; return orig(n); };
        }
    }
    killStray(); shieldHeading();
    var rounds = 0;
    var iv = setInterval(function () { killStray(); shieldHeading(); if (++rounds > 30) clearInterval(iv); }, 1200);
    new MutationObserver(function () { killStray(); }).observe(document.body, { childList: true, subtree: true });
})();
'''

if "UI UPDATE v5" not in t:
    t = t.rstrip() + "\n" + V5 + "\n"
    ap.write_text(t, encoding="utf-8")
    print("UI UPDATE v5 appended.")
else:
    print("already present")