import re
from pathlib import Path

# ================= 1. app.js =================
ap = Path("app.js"); t = ap.read_text(encoding="utf-8")

# 1a. Remove OVERLAP CONTROL v1 entirely (it was appended last; v2 replaces it)
i = t.find("// ===== OVERLAP CONTROL v1")
if i != -1:
    t = t[:i].rstrip() + "\n"
    print("cut OVERLAP CONTROL v1")

# 1b. Delete ALL old fade engines (they ignore the overlap flags)
MARKERS = [
    "// ===== FADE PACK v2",
    "// ===== FADE PACK v3",
    "// ===== FADE PACK v4",
    "// ===== ADD-ON: Show faded/trimmed portion",
    "// ===== ADD-ON: show faded/trimmed portion",
    "// ===== ADD-ON: timeline fade/trim overlay",
    "// ===== ADD-ON: paint the faded/trimmed tail",
    "// ===== TIMELINE v2",
]
for m in MARKERS:
    while True:
        i = t.find(m)
        if i == -1:
            break
        j = t.find("\n// =====", i + 10)
        if j == -1:
            j = len(t)
        t = t[:i] + t[j + 1:]
        print("cut old fade engine:", m)

V2 = r'''
// ===== OVERLAP CONTROL v2: single flag-aware fade engine + UI updates =====
(function () {
    window.overlapAllowed = window.overlapAllowed || {};
    window.customBySpeaker = window.customBySpeaker || {};

    // send overlap flags with remix/generate requests
    if (!window._fetchOvWrap2) {
        window._fetchOvWrap2 = true;
        var _fetch = window.fetch;
        window.fetch = function (url, opts) {
            try {
                var u = String(url);
                if (opts && opts.body && typeof opts.body === "string" &&
                    (u.indexOf("/api/remix_audio") > -1 || u.indexOf("/api/generate") > -1)) {
                    var obj = JSON.parse(opts.body);
                    if (obj && typeof obj === "object") {
                        obj.overlap_allowed = window.overlapAllowed || {};
                        opts = Object.assign({}, opts, { body: JSON.stringify(obj) });
                    }
                }
            } catch (e) {}
            return _fetch.call(this, url, opts);
        };
    }

    // custom voice stays separate from the video clone (working — kept as-is)
    window.uploadCustomVoice = function () {
        var fEl = document.getElementById("cvFile"), spEl = document.getElementById("cvSpeaker"), st = document.getElementById("cvStatus");
        if (!fEl || !spEl) { notify("error", "Custom voice box not ready - refresh the page."); return; }
        var f = fEl.files[0], sp = spEl.value || "Speaker 1";
        if (!f) { notify("error", "Choose an MP3 or WAV clip first."); return; }
        if (!/\.(mp3|wav)$/i.test(f.name)) { notify("error", "Only MP3 or WAV files are allowed."); return; }
        var probe = new Audio(URL.createObjectURL(f));
        probe.onloadedmetadata = function () {
            if (probe.duration > 20.5) { notify("error", "Clip is " + Math.round(probe.duration) + "s - the limit is 20 seconds."); return; }
            var form = new FormData();
            form.append("file", f); form.append("speaker", sp); form.append("job_id", currentJobId || "");
            if (st) st.textContent = "Uploading & creating voice...";
            fetch("/api/upload_custom_voice", { method: "POST", body: form })
                .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (out) {
                    if (st) st.textContent = "";
                    if (!out.ok || !out.d || out.d.error || !out.d.voice_id) { notify("error", (out.d && (out.d.error || out.d.detail)) || "Server error."); return; }
                    window.customBySpeaker[sp] = out.d.voice_id;
                    window.clonedVoiceIds = window.clonedVoiceIds || [];
                    window.clonedVoiceIds.push(out.d.voice_id);
                    speakerChoices[sp] = "custom";
                    speakerVoices[sp] = out.d.voice_id;
                    speakerVoiceNames[sp] = "\uD83D\uDCE4 Custom voice";
                    renderSpeakerVoices();
                    notify("success", "Custom voice created. The cloned voice stays in the dropdown.");
                })
                .catch(function (err) { if (st) st.textContent = ""; notify("error", "Upload failed: " + err.message); });
        };
        probe.onerror = function () { notify("error", "Could not read that audio file."); };
    };
    if (typeof applyChoice === "function" && !window._acWrap7) {
        window._acWrap7 = true;
        var _ac = applyChoice;
        applyChoice = function (name) {
            if ((speakerChoices[name] || "") === "custom" && window.customBySpeaker[name]) {
                speakerVoices[name] = window.customBySpeaker[name];
                speakerVoiceNames[name] = "\uD83D\uDCE4 Custom voice";
                return;
            }
            return _ac.apply(this, arguments);
        };
    }
    if (typeof renderSpeakerVoices === "function" && !window._rsvWrap7) {
        window._rsvWrap7 = true;
        var _rsv = renderSpeakerVoices;
        renderSpeakerVoices = function () {
            var p = _rsv.apply(this, arguments);
            var done = function () {
                document.querySelectorAll("#speakerVoicesTable tbody tr").forEach(function (row) {
                    var name = (row.cells[0] || {}).textContent || "";
                    var sel = row.querySelector("select");
                    if (!sel || !window.customBySpeaker[name]) return;
                    if (!sel.querySelector('option[value="custom"]')) {
                        var o = document.createElement("option");
                        o.value = "custom";
                        o.textContent = "\uD83D\uDCE4 Custom voice (" + name + ")";
                        var co = sel.querySelector('option[value="clone"]');
                        if (co) co.insertAdjacentElement("afterend", o); else sel.appendChild(o);
                    }
                    sel.value = speakerChoices[name] || sel.value;
                });
            };
            if (p && p.then) p.then(done); else done();
            return p;
        };
    }

    // Step 2 header: lock / unlock ALL lines icon
    function injectLockAll() {
        var sec = document.getElementById("editorSection");
        if (!sec || document.getElementById("lockAllBtn")) return;
        var h = sec.querySelector("h3");
        if (!h) return;
        var b = document.createElement("button");
        b.id = "lockAllBtn"; b.className = "action-btn"; b.textContent = "\uD83D\uDD13";
        b.title = "Lock / unlock ALL lines";
        b.style.marginLeft = "8px";
        b.onclick = function () {
            var allLocked = segmentsData.length > 0 && segmentsData.every(function (s) { return s.locked; });
            segmentsData.forEach(function (s) { s.locked = !allLocked; });
            b.textContent = allLocked ? "\uD83D\uDD13" : "\uD83D\uDD12";
            renderTable();
            notify("info", allLocked ? "All lines unlocked." : "All lines locked.");
        };
        h.appendChild(b);
    }
    injectLockAll();
    new MutationObserver(injectLockAll).observe(document.body, { childList: true, subtree: true });

    // Step 6: hide Fine-Tune button, timeline always visible with results
    function syncTimelineVisibility() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/fine-?tune timeline/i.test(b.textContent || "")) b.style.display = "none";
        });
        var rs = document.getElementById("resultSection");
        var ts = document.getElementById("timelineSection");
        if (rs && ts && !rs.classList.contains("hidden") && ts.classList.contains("hidden")) {
            ts.classList.remove("hidden");
            if (typeof renderTimeline === "function") renderTimeline();
        }
    }
    syncTimelineVisibility();
    new MutationObserver(syncTimelineVisibility).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });

    // Step 5.5 table: No-overlap checkbox as 4th column
    window.buildVolumeTable = function (lines) {
        var thead = document.querySelector("#volumeTable thead");
        if (thead && !thead.dataset.v7) {
            thead.dataset.v7 = "1";
            thead.innerHTML = "<tr><th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; lines overlapping it are NOT faded'>No overlap</th><th>\u25B6 Orig</th><th>\uD83D\uDD0A Dub</th><th>Auto</th><th style='min-width:130px'>Trim</th><th></th></tr>";
        }
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
            var cN = document.createElement("td");
            var cb = document.createElement("input"); cb.type = "checkbox";
            cb.checked = !window.overlapAllowed[ln.segment_id];
            cb.title = "Checked = protected (intruders get faded). Uncheck = allow talk-over without fading.";
            cb.onchange = function () {
                if (cb.checked) delete window.overlapAllowed[ln.segment_id];
                else window.overlapAllowed[ln.segment_id] = true;
                if (typeof renderTimeline === "function") renderTimeline();
                var btn = document.getElementById("applyVolumesBtn");
                if (btn) btn.textContent = "\uD83D\uDD0A Apply Volumes & Rebuild MP3 \u2022";
            };
            cN.appendChild(cb); tr.appendChild(cN);
            var c1 = document.createElement("td");
            var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "\u25B6"; b1.title = "Play original line";
            b1.onclick = function () { if (window.playOrigLine) window.playOrigLine(ln, b1); }; c1.appendChild(b1); tr.appendChild(c1);
            var c2 = document.createElement("td");
            var b2 = document.createElement("button"); b2.className = "action-btn green"; b2.textContent = "\u25B6"; b2.title = "Play dubbed line";
            b2.onclick = function () { if (window.playDubLine) window.playDubLine(ln, b2); }; c2.appendChild(b2); tr.appendChild(c2);
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

    // THE single fade engine: glued to blocks, live during drag, honors overlap flags
    var pending7 = false;
    function schedule7() { if (pending7) return; pending7 = true; requestAnimationFrame(function () { pending7 = false; draw7(); }); }
    function draw7() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".segFadeOv, .fadeOv7").forEach(function (f) { f.remove(); });
        Array.prototype.forEach.call(wrap.querySelectorAll("div"), function (d) {
            var bg = (d.style.background || "") + (d.style.backgroundImage || "");
            if (bg.indexOf("f59e0b") > -1 && d.style.pointerEvents === "none") d.remove();
        });
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
            b.style.boxShadow = window.overlapAllowed[seg.segment_id] ? "inset 0 0 0 2px #22c55e" : "";
            var cs = seg.start + (segmentOffsets[seg.segment_id] || 0);
            var slot = seg.end - seg.start;
            var audioEnd = cs + Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
            var boundary = total;
            act.forEach(function (q) {
                if (q.segment_id === seg.segment_id) return;
                if (window.overlapAllowed[q.segment_id]) return;
                var qs = q.start + (segmentOffsets[q.segment_id] || 0);
                if (qs > cs + 0.0001 && qs < boundary) boundary = qs;
            });
            var fadeFrom = boundary - 0.005;
            var ov = audioEnd - fadeFrom;
            var lane = b.parentElement; if (!lane) continue;
            var f = document.createElement("div");
            f.className = "fadeOv7";
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
            if (!b.dataset.fadeHook7) {
                b.dataset.fadeHook7 = "1";
                new MutationObserver(schedule7).observe(b, { attributes: true, attributeFilter: ["style"] });
            }
        }
        if (!document.getElementById("timelineLegendFinal")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendFinal";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border:2px solid #22c55e;border-radius:3px;display:inline-block;"></span>overlap allowed</span>';
            wrap.parentNode.insertBefore(leg, wrap.nextSibling);
        }
    }
    if (typeof renderTimeline === "function" && !window._fadeV7) {
        window._fadeV7 = true;
        var _rt7 = renderTimeline;
        renderTimeline = function () { var r = _rt7.apply(this, arguments); draw7(); return r; };
    }
    if (typeof checkGenerateProgress === "function" && !window._durWrap7) {
        window._durWrap7 = true;
        var _cg7 = checkGenerateProgress;
        checkGenerateProgress = async function () {
            await _cg7.apply(this, arguments);
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
})();
'''
if "OVERLAP CONTROL v2" not in t:
    t = t.rstrip() + "\n" + V2 + "\n"
    print("appended OVERLAP CONTROL v2")
ap.write_text(t, encoding="utf-8")

# ================= 2. eleven_service.py: wire overlap flags into the mix =================
ep = Path("eleven_service.py"); et = ep.read_text(encoding="utf-8")
if "OVERLAP_FLAGS" not in et:
    et = et.replace("USER_GAINS = {}", "USER_GAINS = {}\nOVERLAP_FLAGS = {}  # segment_id -> True: this line may be talked over (intruders not faded)", 1)
    for fn, indent in (("def generate_worker(req):", "    "),
                       ("def remix_with_offsets(req):", "    "),
                       ('def rebuild_final_mix(segments, total_duration, duration_mode="exact", job_id=None', "    ")):
        i = et.find(fn)
        if i == -1:
            continue
        line_end = et.find("\n", i)
        if fn.startswith("def rebuild"):
            if "flags=None" not in et[i:line_end]:
                et = et[:line_end] + ", flags=None" + et[line_end:]
                line_end = et.find("\n", i)
            ins = indent + "global OVERLAP_FLAGS\n" + indent + "if flags: OVERLAP_FLAGS = dict(flags)\n"
        else:
            nl = et.find("\n", line_end + 1)
            ins = indent + "global OVERLAP_FLAGS\n" + indent + "OVERLAP_FLAGS = dict(getattr(req, 'overlap_allowed', None) or {})\n"
            et = et[:nl + 1] + ins + et[nl + 1:]
            continue
        et = et[:line_end + 1] + ins + et[line_end + 1:]
    pat = re.compile(r'^([ \t]*)allowed_end = (generated_files|items)\[i \+ 1\]\["start"\] - 0\.005 if i \+ 1 < len\(\2\) else final_duration', re.M)
    def rep(m):
        ind, lst = m.group(1), m.group(2)
        return (ind + "_bnd = final_duration\n" +
                ind + "for _j in range(len(" + lst + ")):\n" +
                ind + "    if _j == i: continue\n" +
                ind + "    if OVERLAP_FLAGS.get(" + lst + '[_j].get("sid")): continue\n' +
                ind + "    _os = " + lst + '_[_j]["start"]\n' +
                ind + '    if _os > item["start"] and _os < _bnd: _bnd = _os\n' +
                ind + "allowed_end = (_bnd - 0.005) if _bnd < final_duration else final_duration")
    et, n = pat.subn(rep, et)
    print(f"eleven_service: wired OVERLAP_FLAGS into {n} mix location(s)")
    ep.write_text(et, encoding="utf-8")
else:
    print("eleven_service: OVERLAP_FLAGS already present")

# ================= 3. main.py: ensure request models accept overlap_allowed =================
mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")
added = 0
for m in list(re.finditer(r'^([ \t]*)duration_mode:[^\n]*', mt, re.M)):
    nxt = mt[m.end():m.end() + 200]
    if "overlap_allowed" in nxt.split("\n", 4)[0] or "overlap_allowed" in "\n".join(nxt.split("\n")[:4]):
        continue
    line_end = mt.find("\n", m.start())
    mt = mt[:line_end + 1] + m.group(1) + "overlap_allowed: dict = {}\n" + mt[line_end + 1:]
    added += 1
if added:
    mp.write_text(mt, encoding="utf-8")
print(f"main.py: added overlap_allowed field to {added} model(s)")
print("DONE.")