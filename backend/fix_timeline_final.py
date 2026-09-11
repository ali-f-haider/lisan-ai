import re
from pathlib import Path
ap = Path("app.js"); t = ap.read_text(encoding="utf-8")
MARKERS = ["// ===== ADD-ON: Show faded/trimmed portion",
           "// ===== ADD-ON: show faded/trimmed portion",
           "// ===== ADD-ON: timeline fade/trim overlay",
           "// ===== ADD-ON: paint the faded/trimmed tail",
           "// ===== TIMELINE v2",
           "// ===== FADE PACK v2", "// ===== FADE PACK v3", "// ===== FADE PACK v4",
           "// ===== FADE FINAL"]
for m in MARKERS:
    while True:
        i = t.find(m)
        if i == -1: break
        j = t.find("\n// =====", i + 10)
        if j == -1: j = len(t)
        t = t[:i] + t[j + 1:]
        print("cut:", m)
ENGINE = r'''
// ===== FADE FINAL: one engine, glued fades, overlap flags, server-identical rule =====
(function () {
    if (window._fadeFinal) return; window._fadeFinal = true;
    window.overlapAllowed = window.overlapAllowed || {};
    if (typeof checkGenerateProgress === "function" && !window._durF) {
        window._durF = true;
        var _cg = checkGenerateProgress;
        checkGenerateProgress = async function () {
            await _cg.apply(this, arguments);
            try {
                var r = await fetch("/api/progress/generate?t=" + Date.now());
                var d = await r.json();
                if (d && d.status === "done" && d.result && Array.isArray(d.result.lines)) {
                    window._lineDurations = window._lineDurations || {};
                    d.result.lines.forEach(function (ln) { if (ln && ln.duration) window._lineDurations[ln.segment_id] = ln.duration; });
                }
            } catch (e) {}
        };
    }
    var pending = false;
    function schedule() { if (pending) return; pending = true; requestAnimationFrame(function () { pending = false; draw(); }); }
    function draw() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".fadeFinal").forEach(function (f) { f.remove(); });
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var scale = (wrap.clientWidth || 900) / total;
        var act = segmentsData.filter(function (s) { return (s.arabic_text || "").trim(); });
        var divs = wrap.querySelectorAll("div");
        for (var bi = 0; bi < divs.length; bi++) {
            var b = divs[bi];
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") === -1 || st.indexOf("grab") === -1) continue;
            var num = parseInt(b.textContent, 10);
            if (!num || num < 1 || num > segmentsData.length) continue;
            var seg = segmentsData[num - 1];
            if (!seg || !(seg.arabic_text || "").trim()) continue;
            var want = window.overlapAllowed[seg.segment_id] ? "inset 0 0 0 2px #22c55e" : "";
            if (b.style.boxShadow !== want) b.style.boxShadow = want;
            var cs = seg.start + (segmentOffsets[seg.segment_id] || 0);
            var slot = seg.end - seg.start;
            var audioEnd = cs + Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
            var boundary = total;
            act.forEach(function (q) {
                if (q.segment_id === seg.segment_id) return;
                if (window.overlapAllowed[q.segment_id]) return;   // overlap-able lines never protect
                var qs = q.start + (segmentOffsets[q.segment_id] || 0);
                if (qs > cs + 0.0001 && qs < boundary) boundary = qs;
            });
            var fadeFrom = boundary - 0.005;
            var ov = audioEnd - fadeFrom;
            var lane = b.parentElement; if (!lane) continue;
            var f = document.createElement("div");
            f.className = "fadeFinal";
            f.style.position = "absolute";
            f.style.top = b.offsetTop + "px";
            f.style.height = b.offsetHeight + "px";
            if (ov <= 0.02) f.style.display = "none";
            f.style.left = (b.offsetLeft + Math.max(0, (fadeFrom - cs) * scale)) + "px";
            f.style.width = Math.max(3, (Math.min(audioEnd, total) - fadeFrom) * scale) + "px";
            f.style.background = "repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px)";
            f.style.opacity = "0.9";
            f.style.borderRadius = "0 4px 4px 0";
            f.style.pointerEvents = "none";
            f.style.zIndex = "4";
            f.title = "Faded/trimmed in the final mix";
            lane.appendChild(f);
            if (!b.dataset.fadeF) { b.dataset.fadeF = "1"; new MutationObserver(schedule).observe(b, { attributes: true, attributeFilter: ["style"] }); }
        }
        if (!document.getElementById("timelineLegendFinal")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendFinal";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border:2px solid #22c55e;border-radius:3px;display:inline-block;"></span>overlap allowed</span>';
            wrap.parentNode.insertBefore(leg, wrap.nextSibling);
        }
    }
    if (typeof renderTimeline === "function") {
        var _rt = renderTimeline;
        renderTimeline = function () { var r = _rt.apply(this, arguments); draw(); return r; };
    }
})();
'''
t = t.rstrip() + "\n" + ENGINE + "\n"
ap.write_text(t, encoding="utf-8")
print("DONE: single fade engine installed.")