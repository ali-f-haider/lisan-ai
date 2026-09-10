// ===== POLISH PASS: favicon, meta, subtitles, empty states, mobile wraps =====
(function () {
    // Favicon + meta description (no HTML edits needed)
    var fl = document.createElement("link");
    fl.rel = "icon"; fl.type = "image/png"; fl.href = "/logo.png";
    document.head.appendChild(fl);
    if (!document.querySelector('meta[name="description"]')) {
        var m = document.createElement("meta");
        m.name = "description";
        m.content = "Lisan AI — automatic English to Arabic video dubbing with voice cloning, emotions and lip-timed audio.";
        document.head.appendChild(m);
    }

    // Notify safety net: panel visible while it has cards, hidden when empty
    if (typeof notify === "function") {
        var _n = notify;
        notify = function (type, msg) {
            _n(type, msg);
            var p = document.getElementById("notifyPanel");
            if (p) {
                p.style.display = "flex";
                setTimeout(function () {
                    if (!p.children.length) p.style.display = "none";
                }, 10500);
            }
        };
    }

    // Hide the disabled Lip-Sync card whatever its id is
    document.querySelectorAll(".card h3").forEach(function (h) {
        if (/Lip-Sync/i.test(h.textContent)) {
            var c = h.closest(".card");
            if (c) c.style.display = "none";
        }
    });

    // Wrap every table in a horizontal scroll container (mobile)
    document.querySelectorAll("table").forEach(function (t) {
        if (t.parentElement && t.parentElement.classList.contains("table-wrap")) return;
        var w = document.createElement("div");
        w.className = "table-wrap";
        t.parentNode.insertBefore(w, t);
        w.appendChild(t);
    });

    // One-line subtitles under each step title
    var SUBS = {
        editorSection: "Fix timings, edit text, translate, and protect lines with 🔒.",
        voicesSection: "Optional: clone each speaker's own voice from the video.",
        cloneAnalysisSection: "See how much clean speech each speaker has before cloning.",
        speakerVoicesSection: "Assign a cloned or studio voice to every speaker.",
        generateSection: "Generate the final Arabic audio with emotions and exact timing.",
        resultSection: "Preview, fine-tune the timeline, merge — then DOWNLOAD immediately."
    };
    Object.keys(SUBS).forEach(function (id) {
        var sec = document.getElementById(id);
        if (!sec || sec.querySelector(".step-sub")) return;
        var h = sec.querySelector("h3");
        if (!h) return;
        var p = document.createElement("p");
        p.className = "step-sub";
        p.textContent = SUBS[id];
        h.insertAdjacentElement("afterend", p);
    });

    // Friendly empty states for the three tables
    function ensureEmptyState(tableId, text) {
        var tb = document.querySelector("#" + tableId + " tbody");
        if (!tb) return;
        var update = function () {
            var hasRows = tb.querySelectorAll("tr").length > 0;
            var es = document.getElementById(tableId + "_empty");
            if (!hasRows && !es) {
                var wrap = tb.closest(".table-wrap") || tb.parentNode;
                es = document.createElement("div");
                es.id = tableId + "_empty";
                es.className = "empty-state";
                es.textContent = text;
                wrap.appendChild(es);
            } else if (hasRows && es) {
                es.remove();
            }
        };
        new MutationObserver(update).observe(tb, { childList: true });
        update();
    }
    ensureEmptyState("segmentsTable", "No segments yet — upload a video in Step 1 and press Start.");
    ensureEmptyState("speakerVoicesTable", "Voice assignments will appear here after transcription.");
    ensureEmptyState("cloneAnalysisTable", "Clone analysis will appear here after Step 3.");

    // Credits badge tooltip
    var cb = document.getElementById("creditsDisplay");
    if (cb) cb.title = "100 credits = $1.00 · Transcribe 3 · Generate ≈ chars/60 · Merge 1";
})();

// ===== STEP 5.5: VOLUME MATCH & PER-LINE MIX =====
window.VOL_NODES = {};
window._volumeGains = window._volumeGains || {};
window._volumeLines = window._volumeLines || [];
var volOrigAudio = null;

(function injectVolumeCard() {
    if (document.getElementById("volumeSection")) return;
    var res = document.getElementById("resultSection");
    if (!res) return;
    var card = document.createElement("div");
    card.className = "card hidden";
    card.id = "volumeSection";
    card.innerHTML = '<h3>Step 5.5: Volume Match & Per-Line Mix</h3>' +
        '<p class="note">Every Arabic line was automatically loudness-matched to the original speaker\'s voice (see <strong>Auto</strong> column). Play 🔊 a dubbed line, fine-tune it with the slider (−6…+6 dB, live preview), then apply to rebuild the final MP3. Re-running Generate resets trims to auto.</p>' +
        '<div class="table-wrap"><table id="volumeTable"><thead><tr><th>#</th><th>Speaker</th><th>Line</th><th>▶ Orig</th><th>🔊 Dub</th><th>Orig dB</th><th>Dub dB</th><th>Auto</th><th style="min-width:130px">Trim</th><th></th></tr></thead><tbody></tbody></table></div>' +
        '<button id="applyVolumesBtn" class="green">🔊 Apply Volumes & Rebuild MP3</button> ' +
        '<button id="resetVolumesBtn" class="blue">↺ Reset All Sliders</button>';
    res.parentNode.insertBefore(card, res);
    document.getElementById("applyVolumesBtn").onclick = applyVolumes;
    document.getElementById("resetVolumesBtn").onclick = function () {
        window._volumeGains = {};
        buildVolumeTable(window._volumeLines || []);
        notify("info", "Sliders reset to auto-matched values. Press Apply to rebuild.");
    };
})();

function volCtx() { if (!window._volCtx) window._volCtx = new (window.AudioContext || window.webkitAudioContext)(); return window._volCtx; }
function linGain(db) { return Math.pow(10, (db || 0) / 20); }
function stopVolDubAll() { Object.keys(window.VOL_NODES).forEach(function (k) { try { window.VOL_NODES[k].a.pause(); } catch (e) {} }); }

function playOrigLine(line) {
    stopVolDubAll();
    if (volOrigAudio) { volOrigAudio.pause(); volOrigAudio = null; }
    if (typeof stopPreview === "function") stopPreview();
    var a = new Audio("/api/source/" + currentJobId);
    volOrigAudio = a;
    a.addEventListener("loadedmetadata", function () { a.currentTime = Math.max(0, line.start); a.play().catch(function (e) { notify("error", "Preview failed: " + e.message); }); }, { once: true });
    a.addEventListener("timeupdate", function () { if (a.currentTime >= line.end) a.pause(); });
}

function playDubLine(line) {
    if (volOrigAudio) { volOrigAudio.pause(); volOrigAudio = null; }
    if (typeof stopPreview === "function") stopPreview();
    var node = window.VOL_NODES[line.segment_id];
    if (!node) {
        var a = new Audio("/api/segment_audio/" + currentJobId + "/" + line.segment_id);
        var ctx = volCtx();
        var src = ctx.createMediaElementSource(a);
        var g = ctx.createGain();
        src.connect(g); g.connect(ctx.destination);
        node = { a: a, g: g };
        window.VOL_NODES[line.segment_id] = node;
    }
    node.g.gain.value = linGain((window._volumeGains || {})[line.segment_id] || 0);
    try { volCtx().resume(); } catch (e) {}
    if (node.a.paused) node.a.play(); else node.a.pause();
}

function onVolSlider(sid, val) {
    window._volumeGains = window._volumeGains || {};
    window._volumeGains[sid] = val;
    var node = window.VOL_NODES[sid];
    if (node) node.g.gain.value = linGain(val);
    var lab = document.getElementById("vollab_" + sid);
    if (lab) lab.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + " dB";
    var btn = document.getElementById("applyVolumesBtn");
    if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
}

function buildVolumeTable(lines) {
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
        td('<span title="' + full.replace(/"/g, "'") + '">' + full.slice(0, 60) + '</span>');
        var c1 = document.createElement("td");
        var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "▶"; b1.title = "Play original line";
        b1.onclick = function () { playOrigLine(ln); }; c1.appendChild(b1); tr.appendChild(c1);
        var c2 = document.createElement("td");
        var b2 = document.createElement("button"); b2.className = "action-btn blue"; b2.textContent = "🔊"; b2.title = "Play dubbed line (with slider trim)";
        b2.onclick = function () { playDubLine(ln); }; c2.appendChild(b2); tr.appendChild(c2);
        td(ln.orig_db === null || ln.orig_db === undefined ? "—" : ln.orig_db + " dB");
        td(ln.dub_db === null || ln.dub_db === undefined ? "—" : ln.dub_db + " dB");
        td((ln.auto_gain_db > 0 ? "+" : "") + ln.auto_gain_db + " dB");
        var c3 = document.createElement("td");
        var cur = (window._volumeGains || {})[ln.segment_id] || 0;
        var rg = document.createElement("input"); rg.type = "range"; rg.min = "-6"; rg.max = "6"; rg.step = "0.5"; rg.value = cur;
        rg.oninput = function () { onVolSlider(ln.segment_id, parseFloat(rg.value)); };
        c3.appendChild(rg); tr.appendChild(c3);
        td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + ' dB</span>');
        tbody.appendChild(tr);
    });
}

function showVolumeSection(lines) {
    window._volumeLines = lines;
    window._volumeGains = window._volumeGains || {};
    buildVolumeTable(lines);
    var el = document.getElementById("volumeSection");
    if (el) el.classList.remove("hidden");
}

async function applyVolumes() {
    if (!currentJobId) { notify("error", "No job."); return; }
    var btn = document.getElementById("applyVolumesBtn");
    btn.disabled = true; btn.textContent = "⏳ Rebuilding...";
    var offs = {};
    Object.keys(segmentOffsets).forEach(function (k) { if (Math.abs(segmentOffsets[k]) > 0.001) offs[k] = segmentOffsets[k]; });
    try {
        var res = await fetch("/api/remix_audio", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                job_id: currentJobId, segments: segmentsData, offsets: offs,
                gains: window._volumeGains || {}, total_duration: totalDuration,
                duration_mode: document.getElementById("durationMode").value
            })
        });
        var data = await res.json();
        if (!res.ok || !data || data.status !== "success") {
            notify("error", "Rebuild failed: " + ((data && (data.error || data.detail)) || res.status));
            return;
        }
        notify("success", "Volumes applied — final MP3 rebuilt with your per-line trim.");
        var au = document.querySelector("#audioResults audio");
        if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        btn.textContent = "🔊 Apply Volumes & Rebuild MP3";
    } catch (e) { notify("error", e.message); }
    finally { btn.disabled = false; }
}

// Populate the table when generation finishes
(function () {
    if (typeof checkGenerateProgress !== "function") return;
    var orig = checkGenerateProgress;
    checkGenerateProgress = async function () {
        await orig();
        try {
            var r = await fetch("/api/progress/generate?t=" + Date.now());
            var d = await r.json();
            if (d && d.status === "done" && d.result && Array.isArray(d.result.lines) && d.result.lines.length) {
                var sig = currentJobId + ":" + d.result.lines.length;
                if (window._volBuiltFor !== sig) {
                    window._volBuiltFor = sig;
                    showVolumeSection(d.result.lines);
                }
            }
        } catch (e) {}
    };
})();

// Reset hides & clears the volume section too
(function () {
    if (typeof resetWorkspace !== "function") return;
    var orig = resetWorkspace;
    resetWorkspace = function () {
        orig();
        window._volumeLines = [];
        window._volumeGains = {};
        stopVolDubAll();
        if (volOrigAudio) { volOrigAudio.pause(); volOrigAudio = null; }
        var vs = document.getElementById("volumeSection");
        if (vs) vs.classList.add("hidden");
    };
})();

// ===== STEP 5.5 v2: sliders default to matched volume + master trim =====
(function () {
    function clampG(v) { return Math.max(-12, Math.min(12, v)); }
    window.masterTrimValue = function () {
        var el = document.getElementById("masterTrim");
        return el ? (parseFloat(el.value) || 0) : 0;
    };
    function ensureMaster() {
        var card = document.getElementById("volumeSection");
        if (!card) return;
        if (!document.getElementById("masterTrimWrap")) {
            var wrap = document.createElement("div");
            wrap.id = "masterTrimWrap";
            wrap.style.cssText = "display:flex;align-items:center;gap:10px;margin:12px 0 4px;flex-wrap:wrap;";
            wrap.innerHTML = '<strong style="font-size:13px;">🎚️ Master trim (all lines):</strong>' +
                '<input type="range" id="masterTrim" min="-12" max="12" step="0.5" value="0" style="width:220px;">' +
                '<span id="masterTrimLab" style="min-width:64px;">+0.0 dB</span>';
            var tw = card.querySelector(".table-wrap");
            if (tw) card.insertBefore(wrap, tw); else card.appendChild(wrap);
            document.getElementById("masterTrim").oninput = function () {
                var v = parseFloat(this.value) || 0;
                document.getElementById("masterTrimLab").textContent = (v > 0 ? "+" : "") + v.toFixed(1) + " dB";
                Object.keys(window.VOL_NODES || {}).forEach(function (sid) {
                    var base = (window._volumeGains || {})[sid] || 0;
                    window.VOL_NODES[sid].g.gain.value = Math.pow(10, (base + v) / 20);
                });
                var btn = document.getElementById("applyVolumesBtn");
                if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
            };
        }
        var ab = document.getElementById("applyVolumesBtn");
        if (ab) ab.onclick = function () { window.applyVolumesV2(); };
        var rb = document.getElementById("resetVolumesBtn");
        if (rb) rb.onclick = function () { window.resetVolumesV2(); };
    }
    window.resetVolumesV2 = function () {
        window._volumeGains = {};
        (window._volumeLines || []).forEach(function (ln) {
            window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0);
        });
        if (typeof buildVolumeTable === "function") buildVolumeTable(window._volumeLines || []);
        document.querySelectorAll("#volumeTable input[type=range]").forEach(function (rg) { rg.min = -12; rg.max = 12; });
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; document.getElementById("masterTrimLab").textContent = "+0.0 dB"; }
        notify("info", "Sliders reset to the auto-matched volumes.");
    };
    window.applyVolumesV2 = function () {
        if (!currentJobId) { notify("error", "No job."); return; }
        var btn = document.getElementById("applyVolumesBtn");
        var m = window.masterTrimValue();
        var gains = {};
        Object.keys(window._volumeGains || {}).forEach(function (sid) {
            gains[sid] = clampG((window._volumeGains[sid] || 0) + m);
        });
        btn.disabled = true; btn.textContent = "⏳ Rebuilding...";
        var offs = {};
        Object.keys(segmentOffsets).forEach(function (k) { if (Math.abs(segmentOffsets[k]) > 0.001) offs[k] = segmentOffsets[k]; });
        fetch("/api/remix_audio", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: currentJobId, segments: segmentsData, offsets: offs, gains: gains, total_duration: totalDuration, duration_mode: document.getElementById("durationMode").value })
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (!data || data.status !== "success") { notify("error", "Rebuild failed: " + ((data && (data.error || data.detail)) || "server error")); return; }
            Object.keys(gains).forEach(function (sid) { window._volumeGains[sid] = gains[sid]; });
            if (typeof buildVolumeTable === "function") buildVolumeTable(window._volumeLines || []);
            document.querySelectorAll("#volumeTable input[type=range]").forEach(function (rg) { rg.min = -12; rg.max = 12; });
            var mt = document.getElementById("masterTrim");
            if (mt) { mt.value = 0; document.getElementById("masterTrimLab").textContent = "+0.0 dB"; }
            notify("success", "Volumes applied — final MP3 rebuilt with your mix.");
            var au = document.querySelector("#audioResults audio");
            if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        }).catch(function (e) { notify("error", e.message); }).finally(function () {
            btn.disabled = false; btn.textContent = "🔊 Apply Volumes & Rebuild MP3";
        });
    };
    var _oldShow = window.showVolumeSection;
    window.showVolumeSection = function (lines) {
        window._volumeGains = {};
        (lines || []).forEach(function (ln) {
            window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0);
        });
        if (typeof _oldShow === "function") _oldShow(lines);
        document.querySelectorAll("#volumeTable input[type=range]").forEach(function (rg) { rg.min = -12; rg.max = 12; });
        ensureMaster();
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; document.getElementById("masterTrimLab").textContent = "+0.0 dB"; }
    };
    window.onVolSlider = function (sid, val) {
        window._volumeGains = window._volumeGains || {};
        window._volumeGains[sid] = val;
        var node = (window.VOL_NODES || {})[sid];
        if (node) node.g.gain.value = Math.pow(10, (val + window.masterTrimValue()) / 20);
        var lab = document.getElementById("vollab_" + sid);
        if (lab) lab.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + " dB";
        var btn = document.getElementById("applyVolumesBtn");
        if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
    };
    var _oldPlayDub = window.playDubLine;
    window.playDubLine = function (line) {
        var r = _oldPlayDub(line);
        var node = (window.VOL_NODES || {})[line.segment_id];
        if (node) node.g.gain.value = Math.pow(10, (((window._volumeGains || {})[line.segment_id] || 0) + window.masterTrimValue()) / 20);
        return r;
    };
})();


