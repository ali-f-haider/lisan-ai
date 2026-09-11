from pathlib import Path

ap = Path("app.js"); t = ap.read_text(encoding="utf-8")

V6 = r'''
// ===== UI UPDATE v6: clear saved workspace on logout (fresh start after proper logout) =====
(function () {
    if (window._uiV6) return; window._uiV6 = true;
    var KEYS = ["lisan_workspace_v1"];
    function clearWs() {
        try { KEYS.forEach(function (k) { localStorage.removeItem(k); }); } catch (e) {}
    }
    // Wrap doLogout whenever it exists
    function wrapLogout() {
        if (typeof window.doLogout === "function" && !window.doLogout._v6) {
            var orig = window.doLogout;
            window.doLogout = function () { clearWs(); return orig.apply(this, arguments); };
            window.doLogout._v6 = true;
        }
    }
    wrapLogout();
    var iv = setInterval(function () { wrapLogout(); }, 1500);
    setTimeout(function () { clearInterval(iv); }, 30000);
    // Fallback 1: catch logout clicks even if they bypass doLogout
    document.addEventListener("click", function (e) {
        var el = e.target && e.target.closest ? e.target.closest("button, a") : null;
        if (!el) return;
        var tx = (el.textContent || "").trim().toLowerCase();
        var id = (el.id || "").toLowerCase();
        if (id.indexOf("logout") > -1 || /log ?out|sign ?out|تسجيل الخروج/.test(tx)) clearWs();
    }, true);
    // Fallback 2: catch direct calls to the logout API
    if (!window._fetchV6) {
        window._fetchV6 = true;
        var _f = window.fetch;
        window.fetch = function (url, opts) {
            try { if (String(url).indexOf("/api/logout") > -1) clearWs(); } catch (e) {}
            return _f.apply(this, arguments);
        };
    }
})();
'''

if "UI UPDATE v6" not in t:
    t = t.rstrip() + "\n" + V6 + "\n"
    ap.write_text(t, encoding="utf-8")
    print("UI UPDATE v6 appended.")
else:
    print("already present")