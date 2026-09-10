import re
from pathlib import Path

# ---------- app.js ----------
ap = Path("app.js"); t = ap.read_text(encoding="utf-8")

# 1) Remove Step 6 subtitle line
t2 = re.sub(r',\s*resultSection:\s*"[^"]*"', '', t)
if t2 != t: print("removed Step 6 subtitle"); t = t2

# 2) Remove Orig dB / Dub dB columns (header + all row builders)
t2 = re.sub(r'[ \t]*<th[^>]*>\s*Orig dB\s*</th>\s*', '', t)
t2 = re.sub(r'[ \t]*<th[^>]*>\s*Dub dB\s*</th>\s*', '', t2)
t2 = re.sub(r'[ \t]*td\(ln\.orig_db[^\n]*\n', '', t2)
t2 = re.sub(r'[ \t]*td\(ln\.dub_db[^\n]*\n', '', t2)
if t2 != t: print("removed Orig dB / Dub dB columns"); t = t2

# 3) Cut old broken fade add-ons + TIMELINE v2 (keep RESTORE PATCH)
s = t.find("// ===== ADD-ON: Show faded/trimmed portion")
if s != -1:
    e = t.find("// ===== RESTORE PATCH", s)
    if e == -1: e = len(t)
    t = t[:s] + t[e:]
    print("cut old timeline fade blocks")

# 4) Append new interactive fade overlay + custom-voice dropdown fix
ADD = '''
// ===== FADE PACK v3: interactive fade overlay (audio-length aware, live during drag) =====
(function () {
    if (window._fadePackV3) return; window._fadePackV3 = true;
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
    function drawFades() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".segFadeOv").forEach(function (f) { f.remove(); });
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var scale = (wrap.clientWidth || 900) / total;
        var act = segmentsData.filter(function (s) { return (s.arabic_text || "").trim(); });
        var lanes = wrap.querySelectorAll("div[style*='height:34px']");
        var speakers = []; var seen = {};
        act.forEach(function (s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; speakers.push(n); } });
        speakers.forEach(function (spk, li) {
            var lane = lanes[li]; if (!lane) return;
            act.filter(function (s) { return (s.speaker || "Speaker 1") === spk; }).forEach(function (seg) {
                var off = segmentOffsets[seg.segment_id] || 0;
                var cs = seg.start + off;
                var slot = seg.end - seg.start;
                var dur = Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
                var audioEnd = cs + dur;
                var nextStart = null;
                act.forEach(function (o) {
                    if (o === seg) return;
                    var os = o.start + (segmentOffsets[o.segment_id] || 0);
                    if (os > cs + 0.0001 && (nextStart === null || os < nextStart)) nextStart = os;
                });
                var limit = (nextStart === null) ? total : nextStart - 0.005;
                var overflow = audioEnd - limit;
                if (overflow <= 0.02) return;
                var leftPx = Math.max(0, limit * scale);
                var w = Math.max(3, Math.min(audioEnd, total) * scale - leftPx);
                var f = document.createElement("div");
                f.className = "segFadeOv";
                f.style.cssText = "position:absolute;top:4px;height:26px;left:" + leftPx + "px;width:" + w + "px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px);opacity:0.9;border-radius:0 4px 4px 0;pointer-events:none;z-index:4;";
                f.title = "Faded/trimmed in final mix (" + overflow.toFixed(2) + "s past the next line)";
                lane.appendChild(f);
            });
        });
        if (!document.getElementById("timelineLegendFinal")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendFinal";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span>' +
                '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span>';
            wrap.parentNode.insertBefore(leg, wrap.nextSibling);
        }
    }
    function hookDrag() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || typeof MutationObserver === "undefined") return;
        wrap.querySelectorAll("div[style*='cursor:grab']").forEach(function (b) {
            if (b.dataset.fadeHook) return; b.dataset.fadeHook = "1";
            new MutationObserver(function () { drawFades(); }).observe(b, { attributes: true, attributeFilter: ["style"] });
        });
    }
    if (typeof renderTimeline === "function") {
        var _rt = renderTimeline;
        renderTimeline = function () { var r = _rt.apply(this, arguments); drawFades(); hookDrag(); return r; };
    }
})();
// ===== CUSTOM VOICE v2: appears in speaker dropdown + label =====
(function () {
    window._customVoiceNames = window._customVoiceNames || {};
    function fixCustomLabels() {
        Object.keys(window._customVoiceNames || {}).forEach(function (name) {
            document.querySelectorAll("#speakerVoicesTable tbody tr").forEach(function (row) {
                if ((row.cells[0] || {}).textContent !== name) return;
                var opt = row.querySelector('select option[value="clone"]');
                if (opt) opt.textContent = window._customVoiceNames[name];
                var info = row.querySelector("div.note, div[style*='font-size:12px']");
                if (info && speakerChoices[name] === "clone") info.textContent = window._customVoiceNames[name];
            });
        });
    }
    if (typeof renderSpeakerVoices === "function" && !window._rsvWrapped2) {
        window._rsvWrapped2 = true;
        var _rsv = renderSpeakerVoices;
        renderSpeakerVoices = function () {
            var p = _rsv.apply(this, arguments);
            Promise.resolve(p).then(fixCustomLabels);
            return p;
        };
    }
    window.uploadCustomVoice = function () {
        var fEl = document.getElementById("cvFile"), spEl = document.getElementById("cvSpeaker"), st = document.getElementById("cvStatus");
        if (!fEl || !spEl) { notify("error", "Custom voice box not ready — refresh the page."); return; }
        var f = fEl.files[0], sp = spEl.value || "Speaker 1";
        if (!f) { notify("error", "Choose an MP3 or WAV clip first."); return; }
        if (!/\\.(mp3|wav)$/i.test(f.name)) { notify("error", "Only MP3 or WAV files are allowed."); return; }
        var probe = new Audio(URL.createObjectURL(f));
        probe.onloadedmetadata = function () {
            if (probe.duration > 20.5) { notify("error", "Clip is " + Math.round(probe.duration) + "s — the limit is 20 seconds."); return; }
            var form = new FormData();
            form.append("file", f); form.append("speaker", sp); form.append("job_id", currentJobId || "");
            if (st) st.textContent = "Uploading & creating voice...";
            fetch("/api/upload_custom_voice", { method: "POST", body: form })
                .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (out) {
                    if (st) st.textContent = "";
                    if (!out.ok || !out.d || out.d.error || !out.d.voice_id) { notify("error", (out.d && (out.d.error || out.d.detail)) || "Server error."); return; }
                    window._customVoiceNames[sp] = "📤 Custom voice (" + sp + ")";
                    clonedBySpeaker[sp] = out.d.voice_id;
                    speakerChoices[sp] = "clone";
                    speakerVoices[sp] = out.d.voice_id;
                    speakerVoiceNames[sp] = window._customVoiceNames[sp];
                    window.clonedVoiceIds = window.clonedVoiceIds || [];
                    window.clonedVoiceIds.push(out.d.voice_id);
                    renderSpeakerVoices();
                    notify("success", "Custom voice created and assigned to " + sp + ".");
                })
                .catch(function (e) { if (st) st.textContent = ""; notify("error", "Upload failed: " + e.message); });
        };
        probe.onerror = function () { notify("error", "Could not read that audio file."); };
    };
})();
'''
if "FADE PACK v3" not in t:
    t = t.rstrip() + "\n" + ADD + "\n"
    print("appended FADE PACK v3 + custom voice v2")
ap.write_text(t, encoding="utf-8")

# ---------- eleven_service.py: report real line duration ----------
ep = Path("eleven_service.py"); et = ep.read_text(encoding="utf-8")
if '"duration": round(stretched_duration' not in et:
    et2 = re.sub(r'([ \t]*)"auto_gain_db": round\(auto_gain, 1\),?\n',
                 lambda m: m.group(0) + m.group(1) + '"duration": round(stretched_duration, 3),\n', et, count=1)
    if et2 != et:
        ep.write_text(et2, encoding="utf-8"); print("added duration to lines_meta")

# ---------- main.py: record spends so Usage page fills ----------
mp = Path("main.py"); mt = mp.read_text(encoding="utf-8")
REC = '''

# ===== USAGE RECORDING: log every credit deduction to credit_spends =====
def _record_spend(uid, action, credits, job_id=None):
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/credit_spends",
            data=json.dumps({"uid": uid, "action": action, "job_id": job_id, "credits": credits}).encode("utf-8"),
            headers={"apikey": SUPABASE_SERVICE_KEY, "Content-Type": "application/json", "Prefer": "return=minimal"},
            method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
try:
    _od = deduct_credits
    def deduct_credits(*a, **k):
        r = _od(*a, **k)
        try:
            uid = a[0] if len(a) > 0 else k.get("uid")
            amt = a[1] if len(a) > 1 else k.get("amount", k.get("credits"))
            act = a[2] if len(a) > 2 else k.get("action", "deduction")
            jid = a[3] if len(a) > 3 else k.get("job_id")
            _record_spend(uid, act or "deduction", amt, jid)
        except Exception:
            pass
        return r
except NameError:
    pass
'''
if "credit_spends" not in mt:
    mp.write_text(mt + REC, encoding="utf-8"); print("appended usage recording to main.py")
print("DONE.")