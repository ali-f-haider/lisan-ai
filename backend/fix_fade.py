from pathlib import Path

# --- Part A: app.js — replace old FADE PACK with v2 (uses real audio lengths) ---
p = Path("app.js")
t = p.read_text(encoding="utf-8")
idx = t.find("// ===== FADE PACK:")
if idx != -1:
    t = t[:idx]
    print("Removed old FADE PACK.")

V2 = '''
// ===== FADE PACK v2: amber shows exactly what the final mix fades/trims =====
(function () {
    if (window._fadePackV2) return;
    window._fadePackV2 = true;
    if (typeof checkGenerateProgress === "function") {
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
    if (typeof renderTimeline !== "function") return;
    var _baseRT = renderTimeline;
    renderTimeline = function () {
        _baseRT.apply(this, arguments);
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        var oldLeg = document.getElementById("timelineLegendFinal");
        if (oldLeg) oldLeg.remove();
        wrap.querySelectorAll(".segFadeOv").forEach(function (f) { f.remove(); });
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var W = wrap.clientWidth || 900;
        var scale = W / total;
        var lanes = wrap.querySelectorAll("div[style*='height:34px']");
        var speakers = []; var seen = {};
        segmentsData.forEach(function (s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; speakers.push(n); } });
        var act = segmentsData.filter(function (s) { return (s.arabic_text || "").trim(); });
        speakers.forEach(function (spk, li) {
            var lane = lanes[li];
            if (!lane) return;
            var boxes = lane.querySelectorAll("div[style*='cursor:grab']");
            var laneSegs = act.filter(function (s) { return (s.speaker || "Speaker 1") === spk; });
            laneSegs.forEach(function (seg, idx) {
                var box = boxes[idx];
                if (!box) return;
                var f = document.createElement("div");
                f.className = "segFadeOv";
                function draw() {
                    var off = segmentOffsets[seg.segment_id] || 0;
                    var cs = seg.start + off;
                    var slot = seg.end - seg.start;
                    var stored = (window._lineDurations || {})[seg.segment_id] || 0;
                    var ae = cs + Math.max(stored, slot);
                    var ns = total;
                    for (var k = 0; k < act.length; k++) {
                        var os = act[k].start + (segmentOffsets[act[k].segment_id] || 0);
                        if (act[k] !== seg && os > cs + 0.0001 && os < ns) ns = os;
                    }
                    var lim = ns - 0.005;
                    var ov = ae - lim;
                    var origNext = null;
                    for (var j = 0; j < act.length; j++) {
                        if (act[j] !== seg && act[j].start > seg.start + 0.0001 && (origNext === null || act[j].start < origNext)) origNext = act[j].start;
                    }
                    var origOverlap = (origNext !== null) && (seg.end > origNext + 0.02);
                    if (ov > 0.02 && !origOverlap) {
                        var lp = Math.max(0, (lim - cs) * scale);
                        var wp = Math.max(3, slot * scale - lp);
                        f.style.cssText = "position:absolute;top:4px;height:26px;left:" + lp + "px;width:" + wp + "px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px);opacity:0.92;border-radius:0 4px 4px 0;pointer-events:none;z-index:4;";
                        f.title = "Faded/trimmed in the final mix (" + ov.toFixed(2) + "s runs into the next line)";
                    } else {
                        f.style.cssText = "display:none;";
                    }
                }
                draw();
                lane.appendChild(f);
                if (typeof MutationObserver !== "undefined") new MutationObserver(draw).observe(box, { attributes: true, attributeFilter: ["style"] });
            });
        });
        var leg = document.createElement("div");
        leg.id = "timelineLegendFinal";
        leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
        leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span>' +
            '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span>';
        wrap.parentNode.insertBefore(leg, wrap.nextSibling);
    };
})();
'''
t = t.rstrip() + "\n" + V2 + "\n"
p.write_text(t, encoding="utf-8")
print("Appended FADE PACK v2 to app.js.")

# --- Part B (optional, safe): eleven_service.py — report each line's real audio length ---
ep = Path("eleven_service.py")
et = ep.read_text(encoding="utf-8")
old = '"auto_gain_db": round(auto_gain, 1),'
if old in et and '"duration": round(stretched_duration' not in et:
    for indent in ("                ", "            ", "        "):
        target = indent + old
        if target in et:
            et = et.replace(target, target + "\n" + indent + '"duration": round(stretched_duration, 3),', 1)
            ep.write_text(et, encoding="utf-8")
            print("Added per-line duration to eleven_service.py lines_meta.")
            break
else:
    print("eleven_service.py left untouched (already has duration or anchor not found).")
print("DONE.")