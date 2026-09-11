import re
from pathlib import Path
ap = Path("app.js"); t = ap.read_text(encoding="utf-8")
# CPU/GPU sentences gone everywhere
t = re.sub(r'[ \t]*"[^"\n]*\bCPU\b[^"\n]*",?\n', '', t)
t = re.sub(r'[ \t]*"[^"\n]*\bGPU\b[^"\n]*",?\n', '', t)
# credits tooltip gone
t = re.sub(r'[ \t]*if \(cb\) cb\.title = [^\n]*\n', '', t)
# "(names hidden on purpose)" gone
t = re.sub(r'[^\n]*names hidden on purpose[^\n]*\n?', '', t)
# Master rename
t = t.replace("Master trim (all lines)", "Master volume")
# beforeunload: only logout/back/close warn
m = re.search(r'window\.addEventListener\("beforeunload", function \(e\) \{.*?\n\}\);', t, re.S)
if m:
    t = t[:m.start()] + '''window.addEventListener("beforeunload", function (e) {
    var generating = false;
    try { generating = !!generatePollTimer; } catch (err) {}
    if (window._internalNav && !window._forceWarn) return;
    if ((resultsExist && !resultsDownloaded) || generating) { e.preventDefault(); e.returnValue = ""; return ""; }
});''' + t[m.end():]
    print("beforeunload restricted")
UI = r'''
// ===== UI UPDATE v2 =====
(function () {
    window.addEventListener("click", function (e) {
        var a = e.target && e.target.closest ? e.target.closest('a[href^="/"]') : null;
        if (a) window._internalNav = true;
    }, true);
    if (typeof window.doLogout === "function" && !window._dlW2) {
        window._dlW2 = true;
        var _dl = window.doLogout;
        window.doLogout = function () { window._forceWarn = true; return _dl.apply(this, arguments); };
    }
    function fixStep2() {
        document.querySelectorAll("button, a").forEach(function (b) {
            var tx = (b.textContent || "").trim();
            if (/^(⬇️?\s*)?(Export\s+)?(SRT|SBV)$/i.test(tx)) b.style.display = "none";
            if (/Import SRT\/SBV/i.test(tx)) b.textContent = "Import Eng. Subtitle";
        });
    }
    function fixLockAll() {
        var thr = document.querySelector("#segmentsTable thead tr");
        if (!thr || document.getElementById("lockAllBtn2")) return;
        var ths = thr.querySelectorAll("th"), target = null;
        ths.forEach(function (th) { if (/actions/i.test(th.textContent || "")) target = th; });
        if (!target && ths.length) target = ths[ths.length - 1];
        if (!target) return;
        var b = document.createElement("button");
        b.id = "lockAllBtn2"; b.className = "action-btn"; b.textContent = "🔓";
        b.title = "Lock / unlock ALL lines"; b.style.marginLeft = "6px";
        b.onclick = function () {
            var all = segmentsData.length > 0 && segmentsData.every(function (s) { return s.locked; });
            segmentsData.forEach(function (s) { s.locked = !all; });
            b.textContent = all ? "🔓" : "🔒";
            renderTable();
            notify("info", all ? "All lines unlocked." : "All lines locked.");
        };
        target.appendChild(b);
    }
    function fixVolume() {
        var thr = document.querySelector("#volumeTable thead tr");
        if (thr && !thr.dataset.v9) {
            thr.dataset.v9 = "1";
            thr.innerHTML = "<th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; intruders are NOT faded'>No overlap</th><th>▶ Orig</th><th>🔊 Dub</th><th>Auto</th><th style='min-width:130px'>Trim</th><th></th>";
        }
        document.querySelectorAll("#volumeSection strong, #volumeSection span").forEach(function (el) {
            if (/Master trim/i.test(el.textContent || "")) el.textContent = (el.textContent || "").replace(/Master trim[^\(:]*/i, "Master volume");
        });
    }
    function fixStep6() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/Fine-?Tune Timeline/i.test((b.textContent || "").trim())) {
                var p = document.createElement("p");
                p.className = "step-sub"; p.style.cssText = "font-weight:600;margin:10px 0 4px;";
                p.textContent = "Fine-Tune Timeline";
                b.parentNode.insertBefore(p, b); b.remove();
            }
        });
        var rs = document.getElementById("resultSection"), ts = document.getElementById("timelineSection");
        if (rs && ts && !rs.classList.contains("hidden")) { ts.classList.remove("hidden"); if (typeof renderTimeline === "function") renderTimeline(); }
    }
    function fixDub() {
        var btn = null;
        document.querySelectorAll("#resultSection button, #mergeSection button, #videoResults button, #videoResults a").forEach(function (b) {
            if (/Dub Another Video/i.test(b.textContent || "")) btn = b;
        });
        if (!btn || btn.dataset.moved) return;
        var row = document.querySelector("#videoResults .download-buttons");
        if (!row) return;
        btn.dataset.moved = "1";
        btn.style.cssText += ";background:#ed6c02;color:#fff;border-color:#ed6c02;font-weight:700;margin-left:auto;";
        var sep = document.createElement("span");
        sep.style.cssText = "width:1px;align-self:stretch;background:#cbd5e1;margin:0 12px;";
        row.style.display = "flex"; row.style.alignItems = "center";
        row.appendChild(sep); row.appendChild(btn);
    }
    // Step 2 edits flow into 5.5 + timeline (sizes yes, dragged positions preserved)
    var prevStarts = {};
    function snap() { segmentsData.forEach(function (s) { prevStarts[s.segment_id] = s.start; }); }
    function onChg(e) {
        var inp = e.target;
        if (!inp || !inp.closest || !inp.closest("#segmentsTable")) return;
        var tr = inp.closest("tr"); if (!tr || !tr.parentNode) return;
        var i = Array.prototype.indexOf.call(tr.parentNode.children, tr);
        var seg = segmentsData[i]; if (!seg) return;
        var cell = inp.closest("td");
        var ci = cell ? Array.prototype.indexOf.call(tr.children, cell) : -1;
        if (ci === 1) {
            var prev = prevStarts[seg.segment_id];
            if (typeof prev === "number") {
                var d = seg.start - prev;
                if (Math.abs(d) > 0.0001) segmentOffsets[seg.segment_id] = (segmentOffsets[seg.segment_id] || 0) - d;
            }
        }
        prevStarts[seg.segment_id] = seg.start;
        if (typeof renderTimeline === "function") renderTimeline();
        if (window._volumeLines && window._volumeLines.length && typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines);
    }
    document.addEventListener("change", onChg, true);
    if (typeof window.renderTable === "function" && !window._snapW2) {
        window._snapW2 = true;
        var _rt0 = window.renderTable;
        window.renderTable = function () { var r = _rt0.apply(this, arguments); snap(); return r; };
    }
    snap();
    function runAll() { fixStep2(); fixLockAll(); fixVolume(); fixStep6(); fixDub(); }
    runAll();
    var tmo = null;
    new MutationObserver(function () { clearTimeout(tmo); tmo = setTimeout(runAll, 300); }).observe(document.body, { childList: true, subtree: true });
})();
'''
if "UI UPDATE v2" not in t:
    t = t.rstrip() + "\n" + UI + "\n"
ap.write_text(t, encoding="utf-8")
print("DONE: UI UPDATE v2 installed.")