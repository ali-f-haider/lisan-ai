import re
from pathlib import Path

# ================= 1. app.js =================
ap = Path("app.js"); t = ap.read_text(encoding="utf-8")
s = t.find("// ===== ADD-ON: Show faded/trimmed portion")
e = t.find("// ===== RESTORE PATCH")
if s != -1 and e != -1 and e > s:
    t = t[:s] + t[e:]
    print("cut broken amber add-ons + TIMELINE v2 (RESTORE PATCH now runs)")

BLOCK = r'''
// ===== OVERLAP CONTROL v1: per-segment overlap permission + attached fades + custom-voice separation =====
(function () {
    window.overlapAllowed = window.overlapAllowed || {};
    window.customBySpeaker = window.customBySpeaker || {};

    // attach overlap_allowed to remix/generate payloads
    if (!window._fetchOverlapWrapped) {
        window._fetchOverlapWrapped = true;
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
            } catch (err) {}
            return _fetch.call(this, url, opts);
        };
    }

    // custom voice no longer overwrites the video clone
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
                    notify("success", "Custom voice created. The cloned voice stays available in the dropdown.");
                })
                .catch(function (err) { if (st) st.textContent = ""; notify("error", "Upload failed: " + err.message); });
        };
        probe.onerror = function () { notify("error", "Could not read that audio file."); };
    };
    if (typeof applyChoice === "function" && !window._acWrap6) {
        window._acWrap6 = true;
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
    if (typeof renderSpeakerVoices === "function" && !window._rsvWrap6) {
        window._rsvWrap6 = true;
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

    // Step 5.5 table: overlap checkbox column, dB columns removed
    function fixedHeader() {
        var thr = document.querySelector("#volumeTable thead tr");
        if (!thr || thr.dataset.v6) return;
        thr.dataset.v6 = "1";
        thr.innerHTML = "<th>#</th><th>Speaker</th><th>Line</th><th>\u25B6 Orig</th><th>\uD83D\uDD0A Dub</th><th>Auto</th><th style='min-width:130px'>Trim</th><th title='Unchecked = this line may be talked over; intruders are not faded'>No overlap</th><th></th>";
    }
    window.buildVolumeTable = function (lines) {
        fixedHeader();
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
            var c4 = document.createElement("td");
            var cb = document.createElement("input"); cb.type = "checkbox";
            cb.checked = !window.overlapAllowed[ln.segment_id];
            cb.title = "Checked = intruders into this line get faded. Uncheck to allow talk-over without fading.";
            cb.onchange = function () {
                if (cb.checked) delete window.overlapAllowed[ln.segment_id];
                else window.overlapAllowed[ln.segment_id] = true;
                if (typeof renderTimeline === "function") renderTimeline();
                var btn = document.getElementById("applyVolumesBtn");
                if (btn) btn.textContent = "\uD83D\uDD0A Apply Volumes & Rebuild MP3 \u2022";
            };
            c4.appendChild(cb); tr.appendChild(c4);
            td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + " dB</span>");
            tbody.appendChild(tr);
        });
    };

    // FADE v6: glued to blocks via real offsets, live during drag, honors overlap flags
    var pending6 = false;
    function scheduleDraw6() { if (pending6) return; pending6 = true; requestAnimationFrame(function () { pending6 = false; drawFadesV6(); }); }
    function drawFadesV6() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".segFadeOv").forEach(function (f) { f.remove(); });
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
            var wantShadow = window.overlapAllowed[seg.segment_id] ? "inset 0 0 0 2px #22c55e" : "";
            if (b.style.boxShadow !== wantShadow) b.style.boxShadow = wantShadow;
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
            var lane = b.parentElement;
            if (!lane) continue;
            var f = document.createElement("div");
            f.className = "segFadeOv";
            if (ov <= 0.02) f.style.display = "none";
            f.style.position = "absolute";
            f.style.top = b.offsetTop + "px";
            f.style.height = b.offsetHeight + "px";
            f.style.left = (b.offsetLeft + Math.max(0, (fadeFrom - cs) * scale)) + "px";
            f.style.width = Math.max(3, (Math.min(audioEnd, total) - fadeFrom) * scale) + "px";
            f.style.background = "repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px)";
            f.style.opacity = "0.9";
            f.style.borderRadius = "0 4px 4px 0";
            f.style.pointerEvents = "none";
            f.style.zIndex = "4";
            f.title = "Faded/trimmed in the final mix (runs into the next protected line)";
            lane.appendChild(f);
            if (!b.dataset.fadeHook6) {
                b.dataset.fadeHook6 = "1";
                new MutationObserver(scheduleDraw6).observe(b, { attributes: true, attributeFilter: ["style"] });
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
    if (typeof renderTimeline === "function" && !window._fadeV6) {
        window._fadeV6 = true;
        var _rt6 = renderTimeline;
        renderTimeline = function () { var r = _rt6.apply(this, arguments); drawFadesV6(); return r; };
    }
})();
'''
if "OVERLAP CONTROL v1" not in t:
    t = t.rstrip() + "\n" + BLOCK + "\n"
    print("appended OVERLAP CONTROL v1 to app.js")
ap.write_text(t, encoding="utf-8")

# ================= 2. eleven_service.py =================
ep = Path("eleven_service.py"); et = ep.read_text(encoding="utf-8")

def mk(indent, lst, flags_expr):
    return (indent + "_flags = " + flags_expr + "\n" +
            indent + "_boundary = final_duration\n" +
            indent + "for _j in range(len(" + lst + ")):\n" +
            indent + "    if _j == i: continue\n" +
            indent + "    if " + lst + '[_j]["start"] > item["start"] and ' + lst + '[_j]["start"] < _boundary and not _flags.get(' + lst + '_j].get("sid")):\n'.replace(lst + "_j]", lst + '[_j]') +
            indent + "        _boundary = " + lst + '[_j]["start"]\n' +
            indent + "allowed_end = (_boundary - 0.005) if _boundary < final_duration else final_duration")

T1 = 'allowed_end = generated_files[i + 1]["start"] - 0.005 if i + 1 < len(generated_files) else final_duration'
T2 = 'allowed_end = items[i + 1]["start"] - 0.005 if i + 1 < len(items) else final_duration'

def indent_of(text, idx):
    ls = text.rfind("\n", 0, idx) + 1
    return text[ls:idx]

i1 = et.find(T1)
if i1 != -1:
    et = et[:i1] + mk(indent_of(et, i1), "generated_files", 'getattr(req, "overlap_allowed", None) or {}') + et[i1 + len(T1):]
    print("patched generate_worker overlap logic")
i2 = et.find(T2)
if i2 != -1:
    et = et[:i2] + mk(indent_of(et, i2), "items", "flags or {}") + et[i2 + len(T2):]
    i3 = et.find(T2)
    if i3 != -1:
        et = et[:i3] + mk(indent_of(et, i3), "items", 'getattr(req, "overlap_allowed", None) or {}') + et[i3 + len(T2):]
    print("patched rebuild_final_mix + remix_with_offsets overlap logic")
et = et.replace('def rebuild_final_mix(segments, total_duration, duration_mode="exact", job_id=None):',
                'def rebuild_final_mix(segments, total_duration, duration_mode="exact", job_id=None, flags=None):')
et = et.replace('mix = rebuild_final_mix(req.segments, req.total_duration, req.duration_mode, job_id=req.job_id)',
                'mix = rebuild_final_mix(req.segments, req.total_duration, req.duration_mode, job_id=req.job_id, flags=getattr(req, "overlap_allowed", None))')
ep.write_text(et, encoding="utf-8")

# ================= 3. main.py: accept overlap_allowed in request models =================
mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")
if "overlap_allowed" not in mt:
    mt2 = re.sub(r'^([ \t]*)duration_mode:[^\n]*\n',
                 lambda m: m.group(0) + m.group(1) + "overlap_allowed: dict = {}\n",
                 mt, flags=re.M)
    if mt2 != mt:
        mp.write_text(mt2, encoding="utf-8")
        print("added overlap_allowed field to request models in main.py")
    else:
        print("WARNING: no duration_mode line found in main.py - add overlap_allowed manually")
else:
    print("main.py already has overlap_allowed")
print("DONE.")