from pathlib import Path
ap = Path("app.js"); t = ap.read_text(encoding="utf-8")
marks = ["// ===== UI UPDATE v1 =====", "// ===== FADE FINAL:", "// ===== UI UPDATE v2 ====="]
idx = min([t.find(m) for m in marks if t.find(m) != -1], default=-1)
if idx != -1:
    t = t[:idx].rstrip() + "\n"
    print("cut old UI/fade appends")
V3 = r'''
// ===== UI UPDATE v3 =====
(function () {
    window.addEventListener("click", function (e) { var a = e.target && e.target.closest ? e.target.closest('a[href^="/"]') : null; if (a) window._internalNav = true; }, true);
    if (typeof window.doLogout === "function" && !window._dlW3) { window._dlW3 = true; var _dl = window.doLogout; window.doLogout = function () { window._forceWarn = true; return _dl.apply(this, arguments); }; }
    function fixStep2() {
        document.querySelectorAll("button, a").forEach(function (b) {
            var tx = (b.textContent || "").trim();
            if (/^(⬇️?\s*)?(Export\s+)?(SRT|SBV)$/i.test(tx)) b.style.display = "none";
            if (/Import SRT\/SBV/i.test(tx)) b.textContent = "Import Eng. Subtitle";
        });
    }
    function fixLockAll() {
        document.querySelectorAll("#lockAllBtn2").forEach(function (b) { if (!b.closest("#segmentsTable thead")) b.remove(); });
        var thr = document.querySelector("#segmentsTable thead tr");
        if (!thr || document.querySelector("#segmentsTable thead #lockAllBtn2")) return;
        var ths = thr.querySelectorAll("th"), target = null;
        ths.forEach(function (th) { if (/actions/i.test(th.textContent || "")) target = th; });
        if (!target && ths.length) target = ths[ths.length - 1];
        if (!target) return;
        var b = document.createElement("button");
        b.id = "lockAllBtn2"; b.className = "action-btn"; b.textContent = "🔓"; b.title = "Lock / unlock ALL lines"; b.style.marginLeft = "6px";
        b.onclick = function () { var all = segmentsData.length > 0 && segmentsData.every(function (s) { return s.locked; }); segmentsData.forEach(function (s) { s.locked = !all; }); b.textContent = all ? "🔓" : "🔒"; renderTable(); notify("info", all ? "All lines unlocked." : "All lines locked."); };
        target.appendChild(b);
    }
    function fixVolume() {
        var thr = document.querySelector("#volumeTable thead tr");
        if (thr && !thr.dataset.v9) { thr.dataset.v9 = "1"; thr.innerHTML = "<th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; intruders are NOT faded'>No overlap</th><th>▶ Orig</th><th>🔊 Dub</th><th>Auto</th><th style='min-width:130px'>Trim</th><th></th>"; }
        document.querySelectorAll("#volumeSection strong, #volumeSection span").forEach(function (el) { if (/Master trim/i.test(el.textContent || "")) el.textContent = (el.textContent || "").replace(/Master trim[^\(:]*/i, "Master volume"); });
    }
    function fixStep6() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/Fine-?Tune Timeline/i.test((b.textContent || "").trim())) {
                var p = document.createElement("p"); p.className = "step-sub"; p.style.cssText = "font-weight:600;margin:10px 0 4px;"; p.textContent = "Fine-Tune Timeline";
                b.parentNode.insertBefore(p, b); b.remove();
            }
        });
        var rs = document.getElementById("resultSection"), ts = document.getElementById("timelineSection");
        if (rs && ts && !rs.classList.contains("hidden")) { ts.classList.remove("hidden"); if (typeof renderTimeline === "function") renderTimeline(); }
    }
    function fixDub() {
        var btn = null;
        document.querySelectorAll("#resultSection button, #mergeSection button, #videoResults button, #videoResults a").forEach(function (b) { if (/Dub Another Video/i.test(b.textContent || "")) btn = b; });
        if (!btn) return;
        if (btn.style.background.indexOf("237, 108, 2") === -1 && btn.style.cssText.indexOf("#ed6c02") === -1) btn.style.cssText += ";background:#ed6c02;border-color:#ed6c02;color:#fff;font-weight:700;";
        var row = document.querySelector("#videoResults .download-buttons");
        if (row && btn.parentNode !== row) {
            var sep = document.createElement("span");
            sep.style.cssText = "width:1px;align-self:stretch;background:#cbd5e1;margin:0 12px;";
            row.style.display = "flex"; row.style.alignItems = "center";
            row.appendChild(sep); row.appendChild(btn);
            btn.style.marginLeft = "auto";
        }
    }
    // ---- autosave / restore session ----
    var SAVE_KEY = "lisan_workspace_v1";
    function saveWs() {
        try {
            if (!segmentsData.length) return;
            localStorage.setItem(SAVE_KEY, JSON.stringify({
                currentJobId: currentJobId, totalDuration: totalDuration, isVideoUpload: isVideoUpload,
                segments: segmentsData, originalSegments: originalSegments,
                speakerVoices: speakerVoices, speakerVoiceNames: speakerVoiceNames,
                speakerChoices: speakerChoices, clonedBySpeaker: clonedBySpeaker,
                customBySpeaker: window.customBySpeaker || {}, segmentOffsets: segmentOffsets
            }));
        } catch (e) {}
    }
    function restoreWs() {
        try {
            if (segmentsData.length) return;
            var raw = localStorage.getItem(SAVE_KEY);
            if (!raw) return;
            var d = JSON.parse(raw);
            if (!d || !Array.isArray(d.segments) || !d.segments.length) return;
            segmentsData = d.segments; originalSegments = d.originalSegments || [];
            currentJobId = d.currentJobId || null; totalDuration = d.totalDuration || 0; isVideoUpload = !!d.isVideoUpload;
            speakerVoices = d.speakerVoices || {}; speakerVoiceNames = d.speakerVoiceNames || {};
            speakerChoices = d.speakerChoices || {}; clonedBySpeaker = d.clonedBySpeaker || {};
            window.customBySpeaker = d.customBySpeaker || {}; segmentOffsets = d.segmentOffsets || {};
            renderTable(); if (typeof renderSpeakerVoices === "function") renderSpeakerVoices();
            ["editorSection", "voicesSection", "speakerVoicesSection", "generateSection"].forEach(function (id) { var el = document.getElementById(id); if (el) el.classList.remove("hidden"); });
            if (typeof showMediaBanner === "function") showMediaBanner();
            notify("info", "Restored your previous session from this browser. Re-upload the original file to enable preview/clone/merge.");
        } catch (e) {}
    }
    setInterval(saveWs, 4000);
    window.addEventListener("beforeunload", saveWs);
    if (typeof window.renderTable === "function" && !window._saveWrapped) { window._saveWrapped = true; var _rt0 = window.renderTable; window.renderTable = function () { var r = _rt0.apply(this, arguments); saveWs(); return r; }; }
    setTimeout(restoreWs, 600);
    // ---- Step 2 edits flow to 5.5 + timeline, dragged positions preserved ----
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
        if (ci === 2) { var prev = prevStarts[seg.segment_id]; if (typeof prev === "number") { var dlt = seg.start - prev; if (Math.abs(dlt) > 0.0001) segmentOffsets[seg.segment_id] = (segmentOffsets[seg.segment_id] || 0) - dlt; } }
        prevStarts[seg.segment_id] = seg.start;
        if (typeof renderTimeline === "function") renderTimeline();
        if (window._volumeLines && window._volumeLines.length && typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines);
        saveWs();
    }
    document.addEventListener("change", onChg, true);
    snap();
    // ---- FADE v8: fade is the block's own striped tail; cross-speaker only ----
    function draw8() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".fadeFinal").forEach(function (f) { f.remove(); });
        Array.prototype.forEach.call(wrap.querySelectorAll("div"), function (d) {
            var st = d.getAttribute("style") || "";
            if (st.indexOf("repeating-linear-gradient") > -1 && !d.classList.contains("fadeFinal") && !(d.parentNode && (d.parentNode.getAttribute("style") || "").indexOf("cursor") > -1)) d.remove();
        });
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var scale = (wrap.clientWidth || 900) / total;
        var act = segmentsData.filter(function (s) { return (s.arabic_text || "").trim(); });
        var divs = wrap.querySelectorAll("div");
        for (var bi = 0; bi < divs.length; bi++) {
            var b = divs[bi];
            var bst = b.getAttribute("style") || "";
            if (bst.indexOf("cursor") === -1 || bst.indexOf("grab") === -1) continue;
            var num = parseInt(b.textContent, 10);
            if (!num || num < 1 || num > segmentsData.length) continue;
            var seg = segmentsData[num - 1];
            if (!seg || !(seg.arabic_text || "").trim()) continue;
            var off = segmentOffsets[seg.segment_id] || 0;
            var cs = seg.start + off;
            var slot = seg.end - seg.start;
            var dur = Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
            var cap = Infinity;
            act.forEach(function (q) { if (q.segment_id !== seg.segment_id && (q.speaker || "Speaker 1") === (seg.speaker || "Speaker 1")) { var qs = q.start + (segmentOffsets[q.segment_id] || 0); if (qs > cs + 0.0001 && qs < cap) cap = qs; } });
            var visEnd = Math.min(cs + dur, cap);
            var limit = Infinity;
            act.forEach(function (q) {
                if (q.segment_id === seg.segment_id) return;
                if ((q.speaker || "Speaker 1") === (seg.speaker || "Speaker 1")) return;
                if (window.overlapAllowed && window.overlapAllowed[q.segment_id]) return;
                var qs = q.start + (segmentOffsets[q.segment_id] || 0);
                if (qs > cs + 0.0001 && qs < limit) limit = qs;
            });
            var blockWPx = Math.max(8, (visEnd - cs) * scale);
            var wStr = blockWPx + "px";
            if (b.style.width !== wStr) b.style.width = wStr;
            var want = (window.overlapAllowed && window.overlapAllowed[seg.segment_id]) ? "inset 0 0 0 2px #22c55e" : "";
            if (b.style.boxShadow !== want) b.style.boxShadow = want;
            if (limit === Infinity || cs + dur <= limit + 0.02) continue;
            var fadeLeftPx = Math.max(0, (limit - cs) * scale);
            if (fadeLeftPx >= blockWPx - 2) continue;
            var f = document.createElement("div");
            f.className = "fadeFinal";
            f.style.cssText = "position:absolute;top:0;height:100%;left:" + fadeLeftPx + "px;width:" + (blockWPx - fadeLeftPx) + "px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px);opacity:0.9;border-radius:0 4px 4px 0;pointer-events:none;";
            f.title = "Faded/trimmed in the final mix (runs into the next other-speaker line)";
            b.appendChild(f);
        }
        if (!document.getElementById("timelineLegendFinal")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendFinal";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border:2px solid #22c55e;border-radius:3px;display:inline-block;"></span>overlap allowed</span>';
            wrap.parentNode.insertBefore(leg, wrap.nextSibling);
        }
    }
    var pend8 = false;
    function sched8() { if (pend8) return; pend8 = true; requestAnimationFrame(function () { pend8 = false; draw8(); }); }
    function hook8() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || typeof MutationObserver === "undefined") return;
        wrap.querySelectorAll("div").forEach(function (b) {
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") > -1 && st.indexOf("grab") > -1 && !b.dataset.hook8) { b.dataset.hook8 = "1"; new MutationObserver(sched8).observe(b, { attributes: true, attributeFilter: ["style"] }); }
        });
    }
    if (typeof renderTimeline === "function" && !window._fadeV8) {
        window._fadeV8 = true;
        var _rt8 = renderTimeline;
        renderTimeline = function () { var r = _rt8.apply(this, arguments); draw8(); hook8(); return r; };
    }
    function runAll() { fixStep2(); fixLockAll(); fixVolume(); fixStep6(); fixDub(); }
    runAll();
    var tmo = null;
    new MutationObserver(function () { clearTimeout(tmo); tmo = setTimeout(function () { runAll(); hook8(); }, 300); }).observe(document.body, { childList: true, subtree: true });
})();
'''
if "UI UPDATE v3" not in t:
    t = t.rstrip() + "\n" + V3 + "\n"
ap.write_text(t, encoding="utf-8")
print("DONE: UI UPDATE v3 installed.")