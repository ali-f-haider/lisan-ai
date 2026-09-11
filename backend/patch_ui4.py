from pathlib import Path
ap = Path("app.js"); t = ap.read_text(encoding="utf-8")
V4 = r'''
// ===== UI UPDATE v4: pure collision timeline (fixed widths, connected fades only) =====
(function () {
    if (window._uiV4) return; window._uiV4 = true;
    function draw9() {
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
            var off = segmentOffsets[seg.segment_id] || 0;
            var cs = seg.start + off;
            var slot = seg.end - seg.start;
            var slotPx = Math.max(8, slot * scale);
            var wStr = slotPx + "px";
            if (b.style.width !== wStr) b.style.width = wStr;
            var want = (window.overlapAllowed && window.overlapAllowed[seg.segment_id]) ? "inset 0 0 0 2px #22c55e" : "";
            if (b.style.boxShadow !== want) b.style.boxShadow = want;
            var dur = Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
            var limit = Infinity;
            act.forEach(function (q) {
                if (q.segment_id === seg.segment_id) return;
                if ((q.speaker || "Speaker 1") === (seg.speaker || "Speaker 1")) return;
                if (window.overlapAllowed && window.overlapAllowed[q.segment_id]) return;
                var qs = q.start + (segmentOffsets[q.segment_id] || 0);
                if (qs > cs + 0.0001 && qs < limit) limit = qs;
            });
            if (limit === Infinity || cs + dur <= limit + 0.02) continue;
            var fadeLeft = Math.max(0, (limit - cs) * scale);
            if (fadeLeft >= slotPx - 2) continue;
            var f = document.createElement("div");
            f.className = "fadeFinal";
            f.style.cssText = "position:absolute;top:0;height:100%;left:" + fadeLeft + "px;width:" + (slotPx - fadeLeft) + "px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px);opacity:0.9;border-radius:0 4px 4px 0;pointer-events:none;";
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
    var pend = false;
    function sched9() { if (pend) return; pend = true; requestAnimationFrame(function () { pend = false; draw9(); }); }
    function hook9() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || typeof MutationObserver === "undefined") return;
        wrap.querySelectorAll("div").forEach(function (b) {
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") > -1 && st.indexOf("grab") > -1 && !b.dataset.hook9) {
                b.dataset.hook9 = "1";
                new MutationObserver(sched9).observe(b, { attributes: true, attributeFilter: ["style"] });
            }
        });
    }
    if (typeof renderTimeline === "function") {
        var _rt = renderTimeline;
        renderTimeline = function () { var r = _rt.apply(this, arguments); draw9(); hook9(); return r; };
    }
})();
'''
if "UI UPDATE v4" not in t:
    t = t.rstrip() + "\n" + V4 + "\n"
ap.write_text(t, encoding="utf-8")
print("DONE: UI UPDATE v4 installed.")