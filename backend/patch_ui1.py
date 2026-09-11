import re
from pathlib import Path

ap = Path("app.js"); t = ap.read_text(encoding="utf-8")

# 1) Remove all CPU/GPU sentences from the waiting messages
t2 = re.sub(r'[ \t]*"[^"\n]*\bCPU\b[^"\n]*",?\n', '', t)
t2 = re.sub(r'[ \t]*"[^"\n]*\bGPU\b[^"\n]*",?\n', '', t2)
if t2 != t: print("removed CPU/GPU sentences"); t = t2

# 2) Remove credits tooltip
t2 = re.sub(r'[ \t]*if \(cb\) cb\.title = [^\n]*\n', '', t)
if t2 != t: print("removed credits tooltip"); t = t2

# 3) Rename master slider label
if "Master trim (all lines)" in t:
    t = t.replace("Master trim (all lines)", "Master volume"); print("renamed master label")

# 4) Remove "(names hidden on purpose)" line
t2 = re.sub(r'[^\n]*names hidden on purpose[^\n]*\n?', '', t)
if t2 != t: print("removed names-hidden line"); t = t2

# 5) beforeunload: only warn on logout / back / close — not in-site navigation
m = re.search(r'window\.addEventListener\("beforeunload", function \(e\) \{.*?\n\}\);', t, re.S)
if m:
    t = t[:m.start()] + '''window.addEventListener("beforeunload", function (e) {
    var generating = false;
    try { generating = !!generatePollTimer; } catch (err) {}
    if (window._internalNav && !window._forceWarn) return;
    if ((resultsExist && !resultsDownloaded) || generating) {
        e.preventDefault();
        e.returnValue = "";
        return "";
    }
});''' + t[m.end():]
    print("beforeunload now skips in-site navigation")

RUNTIME = r'''
(function () {
    // Step 2: hide SRT/SBV export buttons, rename import button
    function fixStep2Buttons() {
        document.querySelectorAll("button, a").forEach(function (b) {
            var tx = (b.textContent || "").trim();
            if (/^(⬇️?\s*)?(Export\s+)?(SRT|SBV)$/i.test(tx)) b.style.display = "none";
            if (/Import SRT\/SBV/i.test(tx)) b.textContent = "Import Eng. Subtitle";
        });
    }
    // Lock/unlock ALL icon in the Actions column header
    function fixLockAll() {
        var thr = document.querySelector("#segmentsTable thead tr");
        if (!thr || document.getElementById("lockAllBtn2")) return;
        var ths = thr.querySelectorAll("th");
        var target = null;
        ths.forEach(function (th) { if (/actions/i.test(th.textContent || "")) target = th; });
        if (!target && ths.length) target = ths[ths.length - 1];
        if (!target) return;
        var b = document.createElement("button");
        b.id = "lockAllBtn2"; b.className = "action-btn"; b.textContent = "🔓";
        b.title = "Lock / unlock ALL lines";
        b.style.marginLeft = "6px";
        b.onclick = function () {
            var allLocked = segmentsData.length > 0 && segmentsData.every(function (s) { return s.locked; });
            segmentsData.forEach(function (s) { s.locked = !allLocked; });
            b.textContent = allLocked ? "🔓" : "🔒";
            renderTable();
            notify("info", allLocked ? "All lines unlocked." : "All lines locked.");
        };
        target.appendChild(b);
    }
    // Step 5.5: column order (#, Speaker, Line, No overlap, Orig, Dub, Auto, Trim) + master rename
    function fixVolumeHeader() {
        var thr = document.querySelector("#volumeTable thead tr");
        if (thr && !thr.dataset.v8) {
            thr.dataset.v8 = "1";
            thr.innerHTML = "<th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; lines overlapping it are NOT faded'>No overlap</th><th>▶ Orig</th><th>🔊 Dub</th><th>Auto</th><th style='min-width:130px'>Trim</th><th></th>";
        }
        document.querySelectorAll("#volumeSection strong, #volumeSection span").forEach(function (el) {
            if (/Master trim/i.test(el.textContent || "")) el.textContent = (el.textContent || "").replace(/Master trim[^\(:]*/i, "Master volume");
        });
    }
    // Step 6: Fine-Tune button becomes plain text; timeline always visible with results
    function fixStep6() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/Fine-?Tune Timeline/i.test((b.textContent || "").trim())) {
                var p = document.createElement("p");
                p.className = "step-sub";
                p.style.cssText = "font-weight:600;margin:10px 0 4px;";
                p.textContent = "Fine-Tune Timeline";
                b.parentNode.insertBefore(p, b);
                b.remove();
            }
        });
        var rs = document.getElementById("resultSection");
        var ts = document.getElementById("timelineSection");
        if (rs && ts && !rs.classList.contains("hidden")) { ts.classList.remove("hidden"); if (typeof renderTimeline === "function") renderTimeline(); }
    }
    // Dub Another Video: orange, right side, separator line
    function fixDubBtn() {
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
    // In-site navigation flag (used by beforeunload)
    window.addEventListener("click", function (e) {
        var a = e.target && e.target.closest ? e.target.closest('a[href^="/"]') : null;
        if (a) window._internalNav = true;
    }, true);
    var _dl = window.doLogout;
    if (typeof _dl === "function" && !window._dlWrapped) {
        window._dlWrapped = true;
        window.doLogout = function () { window._forceWarn = true; return _dl.apply(this, arguments); };
    }
    // Step 2 edits -> Step 5.5 table + timeline (preserve drag offsets; sizes update; positions only via Reset Offsets)
    var prevStarts = {};
    function snapshotStarts() { segmentsData.forEach(function (s) { prevStarts[s.segment_id] = s.start; }); }
    function onTableChange(e) {
        var inp = e.target;
        if (!inp || !inp.closest || !inp.closest("#segmentsTable")) return;
        var tr = inp.closest("tr");
        if (!tr || !tr.parentNode) return;
        var i = Array.prototype.indexOf.call(tr.parentNode.children, tr);
        var seg = segmentsData[i];
        if (!seg) return;
        var cell = inp.closest("td");
        var ci = cell ? Array.prototype.indexOf.call(tr.children, cell) : -1;
        if (ci === 1) {
            var prev = prevStarts[seg.segment_id];
            if (typeof prev === "number") {
                var delta = seg.start - prev;
                if (Math.abs(delta) > 0.0001) segmentOffsets[seg.segment_id] = (segmentOffsets[seg.segment_id] || 0) - delta;
            }
        }
        prevStarts[seg.segment_id] = seg.start;
        if (typeof renderTimeline === "function") renderTimeline();
        if (window._volumeLines && window._volumeLines.length && typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines);
    }
    document.addEventListener("change", onTableChange, true);
    if (typeof window.renderTable === "function" && !window._snapWrapped) {
        window._snapWrapped = true;
        var _rt0 = window.renderTable;
        window.renderTable = function () { var r = _rt0.apply(this, arguments); snapshotStarts(); return r; };
    }
    snapshotStarts();
    var cd = document.getElementById("creditsDisplay");
    if (cd) cd.title = "";
    function runAll() { fixStep2Buttons(); fixLockAll(); fixVolumeHeader(); fixStep6(); fixDubBtn(); }
    runAll();
    var tmo = null;
    new MutationObserver(function () { clearTimeout(tmo); tmo = setTimeout(runAll, 300); }).observe(document.body, { childList: true, subtree: true });
})();
'''
if "UI UPDATE v1" not in t:
    t = t.rstrip() + "\n// ===== UI UPDATE v1 =====\n" + RUNTIME + "\n"
    print("appended UI UPDATE v1")
ap.write_text(t, encoding="utf-8")
print("DONE.")