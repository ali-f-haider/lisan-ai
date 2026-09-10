from pathlib import Path

p = Path("app.js")
t = p.read_text(encoding="utf-8")

# 1) Remove the mis-placed amber overlay add-ons (they drew fades at wrong lanes)
s = t.find("// ===== ADD-ON: Show faded/trimmed portion")
if s != -1:
    e = t.find("// ===== TIMELINE v2", s)
    if e == -1: e = t.find("// ===== RESTORE PATCH", s)
    if e == -1: e = t.find("// ===== FADE PACK", s)
    if e == -1: e = len(t)
    t = t[:s] + t[e:]
    print("Removed mis-placed amber add-ons.")

# 2) Remove the broken TIMELINE v2 block (it throws and kills all code after it)
s = t.find("// ===== TIMELINE v2")
if s != -1:
    e = t.find("// ===== RESTORE PATCH", s)
    if e == -1: e = t.find("// ===== FADE PACK", s)
    if e == -1: e = len(t)
    t = t[:s] + t[e:]
    print("Removed broken TIMELINE v2 block.")

RESTORE = '''
// ===== RESTORE PATCH: Step 5.5 play buttons + master slider on the LEFT =====
(function () {
    function clampG(v) { return Math.max(-12, Math.min(12, v)); }
    window.buildVolumeTable = function (lines) {
        var tbody = document.querySelector("#volumeTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        (lines || []).forEach(function (ln, i) {
            var seg = segmentsData.find(function (s) { return s.segment_id === ln.segment_id; }) || {};
            var tr = document.createElement("tr");
            function td(html) { var c = document.createElement("td"); c.innerHTML = html; tr.appendChild(c); return c; }
            td(String(i + 1));
            td(ln.speaker || seg.speaker || "");
            var full = (seg.arabic_text || seg.text || "");
            td('<span title="' + full.replace(/"/g, "'") + '">' + full.slice(0, 60) + "</span>");
            var c1 = document.createElement("td");
            var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "\\u25B6"; b1.title = "Play original line";
            b1.onclick = function () { if (window.playOrigLine) window.playOrigLine(ln, b1); }; c1.appendChild(b1); tr.appendChild(c1);
            var c2 = document.createElement("td");
            var b2 = document.createElement("button"); b2.className = "action-btn green"; b2.textContent = "\\u25B6"; b2.title = "Play dubbed line (with slider trim)";
            b2.onclick = function () { if (window.playDubLine) window.playDubLine(ln, b2); }; c2.appendChild(b2); tr.appendChild(c2);
            td(ln.orig_db == null ? "\\u2014" : ln.orig_db + " dB");
            td(ln.dub_db == null ? "\\u2014" : ln.dub_db + " dB");
            td((ln.auto_gain_db > 0 ? "+" : "") + ln.auto_gain_db + " dB");
            var c3 = document.createElement("td");
            var cur = (window._volumeGains || {})[ln.segment_id] || 0;
            var rg = document.createElement("input"); rg.type = "range"; rg.min = "-12"; rg.max = "12"; rg.step = "0.5"; rg.value = cur;
            rg.oninput = function () { window.onVolSlider(ln.segment_id, parseFloat(rg.value)); };
            c3.appendChild(rg); tr.appendChild(c3);
            td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + " dB</span>");
            tbody.appendChild(tr);
        });
    };
    function bindMasterLeft() {
        var mt = document.getElementById("masterTrim");
        if (!mt || mt.dataset.finalBound === "1") return;
        mt.dataset.finalBound = "1";
        var wrapEl = document.getElementById("masterTrimWrap");
        if (wrapEl) wrapEl.style.justifyContent = "flex-start";
        mt.oninput = function () {
            var m = parseFloat(mt.value) || 0;
            var delta = m - (window._masterPrev || 0);
            window._masterPrev = m;
            var lab = document.getElementById("masterTrimLab");
            if (lab) lab.textContent = (m > 0 ? "+" : "") + m.toFixed(1) + " dB";
            if (Math.abs(delta) < 0.001) return;
            (window._volumeLines || []).forEach(function (ln) {
                var g = clampG(((window._volumeGains || {})[ln.segment_id] || 0) + delta);
                window._volumeGains[ln.segment_id] = g;
                var node = (window.VOL_NODES || {})[ln.segment_id];
                if (node) node.g.gain.value = Math.pow(10, g / 20);
            });
            window.buildVolumeTable(window._volumeLines || []);
            var btn = document.getElementById("applyVolumesBtn");
            if (btn) btn.textContent = "\\uD83D\\uDD0A Apply Volumes & Rebuild MP3 \\u2022";
        };
    }
    new MutationObserver(function () { bindMasterLeft(); }).observe(document.body, { childList: true, subtree: true });
    bindMasterLeft();
    function hideLoadVoices() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/Load Voice Options/i.test(b.textContent)) b.style.display = "none";
        });
    }
    hideLoadVoices();
    new MutationObserver(hideLoadVoices).observe(document.body, { childList: true, subtree: true });
})();
'''

FADE = '''
// ===== FADE PACK: fade follows the block, trim only on real overflow, legend below =====
(function () {
    if (typeof renderTimeline !== "function" || window._fadePackDone) return;
    window._fadePackDone = true;
    var _baseRT = renderTimeline;
    renderTimeline = function () {
        _baseRT.apply(this, arguments);
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        var oldLeg = document.getElementById("timelineLegendFinal");
        if (oldLeg) oldLeg.remove();
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var W = wrap.clientWidth || 900;
        var scale = W / total;
        var speakers = [];
        var seen = {};
        segmentsData.forEach(function (s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; speakers.push(n); } });
        var lanes = wrap.querySelectorAll("div[style*='height:34px']");
        speakers.forEach(function (spk, li) {
            var lane = lanes[li];
            if (!lane) return;
            var laneSegs = segmentsData.filter(function (s) { return (s.speaker || "Speaker 1") === spk && (s.arabic_text || "").trim(); });
            var boxes = lane.querySelectorAll("div[style*='cursor:grab']");
            laneSegs.forEach(function (seg, idx) {
                var off = segmentOffsets[seg.segment_id] || 0;
                var curStart = seg.start + off, curEnd = seg.end + off;
                var nextStart = null, origNext = null;
                laneSegs.forEach(function (o) {
                    if (o === seg) return;
                    var os = o.start + (segmentOffsets[o.segment_id] || 0);
                    if (os > curStart + 0.0001 && (nextStart === null || os < nextStart)) nextStart = os;
                    if (o.start > seg.start + 0.0001 && (origNext === null || o.start < origNext)) origNext = o.start;
                });
                var limit = (nextStart === null) ? total : nextStart - 0.005;
                var overflow = curEnd - limit;
                var origOverlap = (origNext !== null) && (seg.end > origNext + 0.02);
                if (overflow <= 0.02 || origOverlap) return;
                var f = document.createElement("div");
                f.className = "segFadeOv";
                f.style.cssText = "position:absolute;top:4px;height:26px;left:" + (limit * scale) + "px;width:" + Math.max(2, overflow * scale) +
                    "px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px);" +
                    "opacity:0.9;border-radius:0 4px 4px 0;pointer-events:none;z-index:3;";
                f.title = "Faded/trimmed in final mix (" + overflow.toFixed(2) + "s over slot)";
                lane.appendChild(f);
                var box = boxes[idx];
                if (box && typeof MutationObserver !== "undefined") {
                    new MutationObserver(function () {
                        var o2 = segmentOffsets[seg.segment_id] || 0;
                        var ov2 = (seg.end + o2) - limit;
                        if (ov2 <= 0.02) { f.style.display = "none"; }
                        else { f.style.display = ""; f.style.left = (limit * scale) + "px"; f.style.width = Math.max(2, ov2 * scale) + "px"; }
                    }).observe(box, { attributes: true, attributeFilter: ["style"] });
                }
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

if "// ===== RESTORE PATCH" not in t:
    t += "\n" + RESTORE
    print("Appended RESTORE PATCH (buttons + master left).")
if "// ===== FADE PACK" not in t:
    t += "\n" + FADE
    print("Appended FADE PACK (timeline fade + legend below).")

p.write_text(t, encoding="utf-8")
print("DONE. app.js updated.")