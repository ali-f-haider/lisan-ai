from pathlib import Path

ap = Path("app.js"); t = ap.read_text(encoding="utf-8")
changed = False

# 1) Restore must not scream "re-upload" when a server probe is about to revive the job
old_mark = 'if (typeof window._markNoMedia === "function") window._markNoMedia();'
new_mark = 'if (typeof window._markNoMedia === "function" && !window._revivePending) window._markNoMedia();'
if old_mark in t:
    t = t.replace(old_mark, new_mark); changed = True; print("banner now waits for server probe")
old_note = 'notify("info", "Restored your previous session from this browser. Re-upload the original file to enable preview/clone/merge.");'
new_note = ('if (window._revivePending) notify("info", "Restored your session. Reconnecting to your last job on the server...");\n'
            '    else notify("info", "Restored your previous session from this browser. Re-upload the original file to enable preview/clone/merge.");')
if old_note in t:
    t = t.replace(old_note, new_note); changed = True; print("restore notice made conditional")

V7 = r'''
// ===== UI UPDATE v7: no leave-popup inside profile, Help/Usage in new tabs, server-revive restore =====
(function () {
    if (window._uiV7) return; window._uiV7 = true;

    // If a saved job exists, restore should wait for the server probe before claiming media is gone
    try {
        var raw = localStorage.getItem("lisan_workspace_v1");
        if (raw && JSON.parse(raw).currentJobId) window._revivePending = true;
    } catch (e) {}

    // 1) beforeunload gatekeeper (capture phase runs before older listeners)
    window.addEventListener("beforeunload", function (e) {
        var generating = false;
        try { generating = !!window.generatePollTimer || !!window._mergeRunning || !!window._mergePollTimer; } catch (err) {}
        var internal = window._internalNav && !window._forceWarn;
        if ((internal && !generating) || (!generating && !window._forceWarn)) { e.stopImmediatePropagation(); return; }
        e.returnValue = "";
    }, true);
    document.addEventListener("click", function (e) {
        var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
        if (a) { var h = a.getAttribute("href") || ""; if (h.charAt(0) === "/" && h.indexOf("//") !== 0) window._internalNav = true; }
    }, true);

    // 2) Help & Usage open in NEW tabs: the working page never unloads
    document.addEventListener("click", function (e) {
        var el = e.target && e.target.closest ? e.target.closest("a,button") : null;
        if (!el) return;
        var href = (el.getAttribute && el.getAttribute("href")) || "";
        var tx = (el.textContent || "").trim();
        var target = null;
        if (href === "/help") target = "/help";
        else if (href === "/account" || /usage|account|النقاط|الاستخدام/i.test(tx)) target = "/account";
        if (!target) return;
        if (el.tagName === "A" && el.getAttribute("target") === "_blank") return;
        e.preventDefault(); e.stopPropagation();
        window.open(target, "_blank", "noopener");
    }, true);

    // 3) Server-revive: reuse the app's own progress handlers to bring Step 6 / timeline / audio back
    function probeAndRevive() {
        window._revivePending = false;
        if (!window.currentJobId) return;
        function banner() {
            if (typeof window._markNoMedia === "function") window._markNoMedia();
            notify("info", "Your last media is no longer on the server (it expires after ~6 hours or a restart). Re-upload the original file to continue.");
        }
        fetch("/api/progress/generate?t=" + Date.now(), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.status === "done" && d.result) {
                    window._mediaAlive = true;
                    if (typeof window.checkGenerateProgress === "function") window.checkGenerateProgress();
                    ["checkMergeProgress", "pollMerge", "checkMerge"].forEach(function (n) {
                        if (typeof window[n] === "function") { try { window[n](); } catch (err) {} }
                    });
                } else banner();
            })
            .catch(banner);
    }
    window._reviveProbe = probeAndRevive;
    var tries = 0;
    var iv = setInterval(function () {
        tries++;
        if (window.currentJobId && document.getElementById("segmentsTable")) { clearInterval(iv); setTimeout(probeAndRevive, 600); }
        else if (tries > 20) clearInterval(iv);
    }, 500);
})();
'''
if "UI UPDATE v7" not in t:
    t = t.rstrip() + "\n" + V7 + "\n"; changed = True; print("UI UPDATE v7 appended")

if changed:
    ap.write_text(t, encoding="utf-8"); print("app.js written")
else:
    print("WARNING: nothing matched - paste me the restoreWs block")